"""Инструмент для добавления ответа (комментария) в тикет GitHub."""

import os
from typing import Dict

import httpx
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace
from pydantic import Field

from mcp_instance import mcp
from .utils import ToolResult, require_env

tracer = trace.get_tracer(__name__)


@mcp.tool()
async def post_ticket_reply(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    reply_text: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Текст комментария, который нужно оставить в тикете.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    📝 Добавление комментария к тикету в GitHub.

    Args:
        issue_number: Номер issue (как отображается в GitHub: #1, #2, ...).
        reply_text: Текст комментария.
        ctx: Контекст для логирования и прогресса.

    Returns:
        ToolResult: Краткое описание результата и ссылка на комментарий.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("post_ticket_reply") as span:
        span.set_attribute("issue_number", issue_number)

        try:
            await ctx.info(
                f"📝 Пытаемся оставить комментарий в тикете #{issue_number} в GitHub"
            )
            await ctx.report_progress(progress=0, total=100)

            # 1) Настройки GitHub
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            if not token:
                msg = (
                    "Для добавления комментария требуется GITHUB_TOKEN с правами "
                    "на запись в репозиторий."
                )
                await ctx.error(msg)
                raise McpError(
                    ErrorData(
                        code=-32602,
                        message=msg,
                    )
                )

            span.set_attribute("github_repo", repo)

            # 2) Формируем запрос к GitHub API
            #    POST /repos/{owner}/{repo}/issues/{issue_number}/comments
            url = (
                f"https://api.github.com/repos/{repo}/issues/"
                f"{issue_number}/comments"
            )
            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            }
            payload = {
                "body": reply_text,
            }

            await ctx.info("📡 Отправляем комментарий в GitHub")
            await ctx.report_progress(progress=40, total=100)

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                comment_data = response.json()

            await ctx.info("✅ Комментарий успешно добавлен")
            await ctx.report_progress(progress=100, total=100)

            comment_url = comment_data.get("html_url")
            text = (
                f"Комментарий успешно добавлен в тикет #{issue_number}.\n"
                f"Ссылка на комментарий: {comment_url}"
            )

            simplified = {
                "issue_number": issue_number,
                "comment_url": comment_url,
                "comment_id": comment_data.get("id"),
            }

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
        except Exception as e:
            await ctx.error(f"💥 Неожиданная ошибка: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка: {e}",
                )
            ) from e
