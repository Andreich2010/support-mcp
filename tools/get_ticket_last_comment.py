"""Инструмент для получения последнего комментария по тикету (issue) из GitHub."""

import os
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace
from pydantic import Field

from mcp_instance import mcp
from .utils import ToolResult, require_env

tracer = trace.get_tracer(__name__)


@mcp.tool()
async def get_ticket_last_comment(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    📝 Получение **последнего комментария** по тикету.

    Если комментариев нет — вернёт понятный текст и comment = None
    в structured_content.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("get_ticket_last_comment") as span:
        span.set_attribute("issue_number", issue_number)

        try:
            await ctx.info(f"🔍 Получаем последний комментарий по тикету #{issue_number}")
            await ctx.report_progress(progress=0, total=100)

            # 1) Настройки GitHub
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")  # может быть пустым

            span.set_attribute("github_repo", repo)

            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with httpx.AsyncClient(timeout=20.0) as client:
                # 2) Сначала узнаём количество комментариев у issue
                issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
                await ctx.info("📡 Запрашиваем данные тикета (количество комментариев)")
                issue_resp = await client.get(issue_url, headers=headers)
                issue_resp.raise_for_status()
                issue_data: Dict[str, Any] = issue_resp.json()
                comments_count: int = issue_data.get("comments", 0)

                await ctx.report_progress(progress=40, total=100)

                if comments_count == 0:
                    text = f"У тикета #{issue_number} пока нет комментариев."
                    await ctx.info(text)
                    await ctx.report_progress(progress=100, total=100)

                    return ToolResult(
                        content=[TextContent(type="text", text=text)],
                        structured_content={
                            "issue_number": issue_number,
                            "comment": None,
                        },
                        meta={"repo": repo},
                    )

                # 3) Берём последний комментарий: per_page=1, page=comments_count
                comments_url = f"{issue_url}/comments"
                params = {
                    "per_page": 1,
                    "page": comments_count,
                }

                await ctx.info("📡 Запрашиваем последний комментарий")
                comments_resp = await client.get(comments_url, headers=headers, params=params)
                comments_resp.raise_for_status()
                comments: List[Dict[str, Any]] = comments_resp.json()

            await ctx.report_progress(progress=80, total=100)

            if not comments:
                # На всякий случай — маловероятно, но вдруг
                text = f"Не удалось получить комментарии для тикета #{issue_number}."
                await ctx.info(text)
                await ctx.report_progress(progress=100, total=100)
                return ToolResult(
                    content=[TextContent(type="text", text=text)],
                    structured_content={
                        "issue_number": issue_number,
                        "comment": None,
                    },
                    meta={"repo": repo},
                )

            last = comments[0]

            body: str = last.get("body") or ""
            user: Optional[str] = (last.get("user") or {}).get("login")
            created_at: Optional[str] = last.get("created_at")
            html_url: Optional[str] = last.get("html_url") or last.get("url")

            simplified = {
                "issue_number": issue_number,
                "id": last.get("id"),
                "body": body,
                "user": user,
                "created_at": created_at,
                "url": html_url,
            }

            text_lines = [
                f"Последний комментарий в тикете #{issue_number}",
                f"Автор: {user or 'неизвестный пользователь'}",
                f"Дата: {created_at}",
                f"Ссылка: {html_url}",
                "",
                "Текст комментария:",
                body,
            ]
            text = "\n".join(text_lines)

            await ctx.info("✅ Последний комментарий успешно получен")
            await ctx.report_progress(progress=100, total=100)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"comment": simplified},
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
        except Exception as e:  # noqa: BLE001
            await ctx.error(f"💥 Неожиданная ошибка: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка: {e}",
                )
            ) from e
