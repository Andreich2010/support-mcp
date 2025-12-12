"""Инструмент для получения подробной информации о тикете (issue) из GitHub."""

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
async def get_ticket_detail(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    📝 Получение деталей одного тикета из GitHub Issues.

    Args:
        issue_number: Номер issue (как отображается в GitHub: #1, #2, ...).
        ctx: Контекст для логирования и прогресса.

    Returns:
        ToolResult: Информация о тикете и краткое человекочитаемое описание.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("get_ticket_detail") as span:
        span.set_attribute("issue_number", issue_number)

        try:
            await ctx.info(f"🚀 Загружаем детали тикета #{issue_number} из GitHub")
            await ctx.report_progress(progress=0, total=100)

            # 1) Настройки GitHub из окружения
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")

            span.set_attribute("github_repo", repo)

            # 2) Запрос к GitHub Issues API
            #    GET /repos/{owner}/{repo}/issues/{issue_number}
            url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            await ctx.info(f"📡 Запрашиваем GitHub Issues API для тикета #{issue_number}")
            await ctx.report_progress(progress=40, total=100)

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                issue: Dict[str, Any] = response.json()

            await ctx.info("✅ Детали тикета получены")
            await ctx.report_progress(progress=80, total=100)

            # Фильтруем случай PR (у GitHub PR = особый вид issue)
            if "pull_request" in issue:
                msg = "Указан номер pull request, а не обычного issue."
                await ctx.error(msg)
                raise McpError(
                    ErrorData(
                        code=-32602,
                        message=msg,
                    )
                )

            labels: List[str] = [lbl.get("name", "") for lbl in issue.get("labels", [])]

            simplified: Dict[str, Any] = {
                "id": issue.get("id"),
                "number": issue.get("number"),
                "title": issue.get("title"),
                "body": issue.get("body") or "",
                "state": issue.get("state"),
                "created_at": issue.get("created_at"),
                "updated_at": issue.get("updated_at"),
                "url": issue.get("html_url"),
                "user": issue.get("user", {}).get("login"),
                "assignee": (issue.get("assignee") or {}).get("login"),
                "labels": labels,
                "comments": issue.get("comments", 0),
            }

            await ctx.report_progress(progress=100, total=100)

            # Короткое человекочитаемое описание
            body_preview = (simplified["body"] or "").strip()
            if len(body_preview) > 400:
                body_preview = f"{body_preview[:400]}..."

            lines = [
                f"Тикет #{simplified['number']} ({simplified['state']})",
                f"Заголовок: {simplified['title']}",
                f"Автор: {simplified['user']}",
            ]
            if simplified["assignee"]:
                lines.append(f"Исполнитель: {simplified['assignee']}")
            if labels:
                lines.append(f"Метки: {', '.join(labels)}")
            lines.append(f"Ссылка: {simplified['url']}")
            if body_preview:
                lines.extend(("", "Описание:", body_preview))
            text = "\n".join(lines)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"ticket": simplified},
                meta={"repo": repo},
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
        except Exception as e:
            await ctx.error(f"💥 Неожиданная ошибка: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка: {e}",
                )
            ) from e
