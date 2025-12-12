"""Инструмент для управления приоритетом, лейблами и исполнителем тикета в GitHub."""

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
async def update_ticket_meta(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    priority: Optional[str] = Field(
        default=None,
        description=(
            "Приоритет тикета. Записывается в label вида 'priority: <значение>'. "
            "Примеры: low, medium, high, urgent."
        ),
    ),
    labels: Optional[List[str]] = Field(
        default=None,
        description=(
            "Полный список labels, которые должны быть на тикете. "
            "Если не задан — будем использовать текущие."
        ),
    ),
    assignee: Optional[str] = Field(
        default=None,
        description=(
            "Логин исполнителя GitHub. "
            "None — не менять, пустая строка '' — снять исполнителя."
        ),
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    ⚙️ Обновляет метаданные тикета:

    - приоритет (label `priority: <priority>`);
    - список labels;
    - исполнителя.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("update_ticket_meta") as span:
        span.set_attribute("issue_number", issue_number)
        span.set_attribute("priority", priority or "")
        span.set_attribute("assignee", assignee or "")

        try:
            await ctx.info(f"⚙️ Обновляем метаданные тикета #{issue_number}")
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")

            if not token:
                msg = "Для обновления тикета требуется GITHUB_TOKEN с правами записи."
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
                # 1) забираем текущий issue, чтобы не потерять метки
                resp_issue = await client.get(base_url, headers=headers)
                resp_issue.raise_for_status()
                issue: Dict[str, Any] = resp_issue.json()

                current_labels: List[str] = [
                    l.get("name", "") for l in issue.get("labels", [])
                ]

                new_labels = list(current_labels)

                # если labels переданы — используем их как базу
                if labels is not None:
                    new_labels = list(labels)

                # приоритет через label `priority: ...`
                if priority:
                    new_labels = [
                        l for l in new_labels if not l.lower().startswith("priority:")
                    ]
                    new_labels.append(f"priority: {priority}")

                payload: Dict[str, Any] = {}

                if labels is not None or priority is not None:
                    payload["labels"] = new_labels

                # исполнитель
                if assignee is not None:
                    if assignee == "":
                        payload["assignees"] = []
                    else:
                        payload["assignees"] = [assignee]

                if not payload:
                    text = (
                        f"Для тикета #{issue_number} не передано ни одной настройки. "
                        "Нечего обновлять."
                    )
                    await ctx.info(text)
                    await ctx.report_progress(progress=100, total=100)
                    return ToolResult(
                        content=[TextContent(type="text", text=text)],
                        structured_content={
                            "issue_number": issue_number,
                            "updated": False,
                        },
                        meta={"repo": repo},
                    )

                await ctx.info("📡 Отправляем PATCH в GitHub Issues API")
                await ctx.report_progress(progress=40, total=100)

                resp_update = await client.patch(
                    base_url,
                    headers=headers,
                    json=payload,
                )
                resp_update.raise_for_status()
                updated: Dict[str, Any] = resp_update.json()

            await ctx.report_progress(progress=90, total=100)

            updated_labels = [l.get("name", "") for l in updated.get("labels", [])]
            updated_assignees = [
                a.get("login", "") for a in updated.get("assignees", [])
            ]

            lines = [
                f"Тикет #{issue_number} успешно обновлён.",
                f"Labels: {', '.join(updated_labels) or 'нет'}",
                f"Исполнители: {', '.join(updated_assignees) or 'не назначены'}",
            ]
            if priority:
                lines.append(f"Приоритет: {priority}")
            text = "\n".join(lines)

            await ctx.info("✅ Метаданные тикета обновлены")
            await ctx.report_progress(progress=100, total=100)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "issue_number": issue_number,
                    "labels": updated_labels,
                    "assignees": updated_assignees,
                    "priority": priority,
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
            await ctx.error(f"💥 Неожиданная ошибка: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка: {e}",
                )
            ) from e
