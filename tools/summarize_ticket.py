"""Инструмент для краткого резюме тикета (summary) без вызова LLM внутри MCP."""

import os
from typing import Any, Dict, List

import httpx
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace
from pydantic import Field

from mcp_instance import mcp
from .utils import ToolResult, require_env

tracer = trace.get_tracer(__name__)


@mcp.tool()
async def summarize_ticket(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    comments_limit: int = Field(
        default=10,
        ge=0,
        le=50,
        description="Сколько последних комментариев учитывать в резюме.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    📝 Делает краткое резюме тикета БЕЗ использования LLM внутри MCP.

    MCP-сервер собирает все важные данные (заголовок, тело, статус, метки,
    последние комментарии) и формирует простое текстовое summary.
    Уже модель-хост может, при желании, переформулировать это более красиво.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("summarize_ticket") as span:
        span.set_attribute("issue_number", issue_number)
        span.set_attribute("comments_limit", comments_limit)

        try:
            await ctx.info(f"📝 Делаем резюме тикета #{issue_number}")
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            span.set_attribute("github_repo", repo)

            headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            base_issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"

            async with httpx.AsyncClient(timeout=20.0) as client:
                # 1) Сам тикет
                resp_issue = await client.get(base_issue_url, headers=headers)
                resp_issue.raise_for_status()
                issue: Dict[str, Any] = resp_issue.json()

                if "pull_request" in issue:
                    msg = "Указан номер pull request, а не обычного issue."
                    await ctx.error(msg)
                    raise McpError(
                        ErrorData(
                            code=-32602,
                            message=msg,
                        )
                    )

                title: str = issue.get("title") or ""
                body: str = issue.get("body") or ""
                state: str = issue.get("state") or "open"
                author: str = (issue.get("user") or {}).get("login") or "unknown"
                labels = [l.get("name", "") for l in issue.get("labels", [])]

                comments_block = ""
                comments: List[Dict[str, Any]] = []

                if comments_limit > 0:
                    comments_url = f"{base_issue_url}/comments"
                    resp_comments = await client.get(
                        comments_url,
                        headers=headers,
                        params={"per_page": max(comments_limit, 10)},
                    )
                    resp_comments.raise_for_status()
                    comments = resp_comments.json()

            await ctx.report_progress(progress=40, total=100)

            # Берём последние comments_limit комментариев (если есть)
            last_comments: List[Dict[str, Any]] = []
            if comments:
                comments_sorted = sorted(
                    comments,
                    key=lambda c: c.get("created_at") or "",
                )
                last_comments = comments_sorted[-comments_limit:] if comments_limit > 0 else []
            comment_lines: List[str] = []
            for c in last_comments:
                c_author = (c.get("user") or {}).get("login") or "unknown"
                c_body = (c.get("body") or "").strip()
                if len(c_body) > 400:
                    c_body = f"{c_body[:400]}..."
                comment_lines.append(f"- [{c_author}]: {c_body}")
            await ctx.report_progress(progress=70, total=100)

            # Простое "summary" без LLM: структура + обрезка текста
            short_body = body.strip()
            if len(short_body) > 600:
                short_body = f"{short_body[:600]}..."

            lines: List[str] = [
                f"Тикет #{issue_number} — краткое резюме",
                "",
                "1) Краткое описание",
                f"Заголовок: {title}",
            ]
            if short_body:
                lines.append(f"Описание (усечённое): {short_body}")
            lines.extend(
                (
                    "",
                    "2) Текущее состояние",
                    f"Статус: {state}",
                    f"Автор: {author}",
                    f"Метки: {', '.join(labels) or 'нет'}",
                    "",
                    "3) Последние комментарии",
                )
            )
            if comments_block := "\n".join(comment_lines):
                lines.append(comments_block)
            else:
                lines.append("Комментариев нет или они не загружены.")
            lines.extend(
                (
                    "",
                    "4) Следующие шаги (черновик)",
                    "Требуется анализ инженером поддержки. MCP-сервер сформировал только "
                    "краткое структурное резюме без интерпретации.",
                )
            )
            summary_text = "\n".join(lines)

            await ctx.report_progress(progress=100, total=100)

            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=summary_text,
                    )
                ],
                structured_content={
                    "issue_number": issue_number,
                    "summary": summary_text,
                    "state": state,
                    "labels": labels,
                },
            )

        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response else "unknown"
            await ctx.error(f"❌ HTTP ошибка GitHub API: {status}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Ошибка при запросе к GitHub API: {status}",
                )
            ) from e
        except ValueError as e:
            await ctx.error(f"❌ Ошибка конфигурации: {e}")
            raise McpError(
                ErrorData(
                    code=-32602,
                    message=f"Неверная конфигурация окружения: {e}",
                )
            ) from e
        except Exception as e:  # noqa: BLE001
            await ctx.error(f"💥 Неожиданная ошибка при резюмировании тикета: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при резюмировании тикета: {e}",
                )
            ) from e
