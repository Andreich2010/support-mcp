"""Инструмент для аккуратного закрытия тикета в GitHub."""

import os
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace
from pydantic import Field

from mcp_instance import mcp
from .utils import ToolResult, require_env
from .post_ticket_reply import post_ticket_reply

tracer = trace.get_tracer(__name__)


@mcp.tool()
async def close_ticket(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    final_comment: Optional[str] = Field(
        default=None,
        description="Финальный комментарий перед закрытием тикета (опционально).",
    ),
    resolution_label: Optional[str] = Field(
        default="resolved",
        description="Label, который будет добавлен при закрытии (например, 'resolved').",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    ✅ Закрывает тикет в GitHub:

    - при необходимости оставляет финальный комментарий;
    - добавляет resolution label;
    - переводит state=closed.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("close_ticket") as span:
        span.set_attribute("issue_number", issue_number)

        try:
            await ctx.info(f"✅ Закрываем тикет #{issue_number}")
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")

            if not token:
                msg = "Для закрытия тикета требуется GITHUB_TOKEN с правами записи."
                await ctx.error(msg)
                raise McpError(
                    ErrorData(
                        code=-32602,
                        message=msg,
                    )
                )

            span.set_attribute("github_repo", repo)

            base_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                # 1) Получаем текущий issue, чтобы не потерять labels
                resp_issue = await client.get(base_url, headers=headers)
                resp_issue.raise_for_status()
                issue: Dict[str, Any] = resp_issue.json()

                current_labels: List[str] = [
                    l.get("name", "") for l in issue.get("labels", [])
                ]

                # 2) Добавляем финальный комментарий (если есть)
                if final_comment:
                    await ctx.info("📝 Оставляем финальный комментарий")
                    await post_ticket_reply(
                        issue_number=issue_number,
                        reply_text=final_comment,
                        ctx=ctx,
                    )

                await ctx.report_progress(progress=50, total=100)

                new_labels = list(current_labels)
                if resolution_label and resolution_label not in new_labels:
                    new_labels.append(resolution_label)

                payload: Dict[str, Any] = {
                    "state": "closed",
                    "labels": new_labels,
                }

                await ctx.info("📡 Отправляем PATCH для закрытия тикета")
                resp_update = await client.patch(
                    base_url,
                    headers=headers,
                    json=payload,
                )
                resp_update.raise_for_status()
                updated: Dict[str, Any] = resp_update.json()

            await ctx.report_progress(progress=100, total=100)

            text = (
                f"Тикет #{issue_number} закрыт.\n"
                f"Labels: {', '.join([l.get('name', '') for l in updated.get('labels', [])])}"
            )

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "issue_number": issue_number,
                    "state": updated.get("state"),
                    "labels": [l.get("name", "") for l in updated.get("labels", [])],
                },
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
            await ctx.error(f"💥 Неожиданная ошибка при закрытии тикета: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при закрытии тикета: {e}",
                )
            ) from e
