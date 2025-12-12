"""Инструмент для поиска "застоявшихся" тикетов (давно без активности)."""

import datetime
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
async def get_stale_tickets(
    inactive_days: int = Field(
        ...,
        ge=1,
        le=365,
        description="Сколько дней не должно быть активности, чтобы считать тикет 'застоявшимся'.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    🔍 Ищет открытые тикеты, которые давно не обновлялись.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("get_stale_tickets") as span:
        span.set_attribute("inactive_days", inactive_days)

        try:
            await ctx.info(
                f"🔍 Ищем 'застоявшиеся' тикеты (без активности {inactive_days}+ дней)"
            )
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")

            span.set_attribute("github_repo", repo)

            headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            # Задаём временную границу
            now = datetime.datetime.now(datetime.timezone.utc)
            cutoff = now - datetime.timedelta(days=inactive_days)

            # Для демо возьмём до 100 открытых тикетов
            url = f"https://api.github.com/repos/{repo}/issues"
            params = {
                "state": "open",
                "per_page": 100,
                "sort": "updated",
                "direction": "asc",  # сначала самые старые
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                issues: List[Dict[str, Any]] = resp.json()

            await ctx.report_progress(progress=70, total=100)

            stale: List[Dict[str, Any]] = []
            for issue in issues:
                if "pull_request" in issue:
                    continue  # PR пропускаем

                updated_str = issue.get("updated_at")
                if not updated_str:
                    continue

                try:
                    updated_dt = datetime.datetime.fromisoformat(
                        updated_str.replace("Z", "+00:00")
                    )
                except Exception:
                    continue

                if updated_dt <= cutoff:
                    stale.append(
                        {
                            "number": issue.get("number"),
                            "title": issue.get("title"),
                            "updated_at": updated_str,
                            "url": issue.get("html_url"),
                            "user": issue.get("user", {}).get("login"),
                        }
                    )

            await ctx.report_progress(progress=100, total=100)

            if not stale:
                text = (
                    f"Открытых тикетов без активности дольше {inactive_days} дней не найдено."
                )
            else:
                lines = [
                    f"Открытые тикеты без активности дольше {inactive_days} дней:"
                ]
                lines.extend(
                    f"- #{i['number']} {i['title']} (обновлён {i['updated_at']}) -> {i['url']}"
                    for i in stale[:20]
                )
                if len(stale) > 20:
                    lines.append(f"... и ещё {len(stale) - 20} тикетов.")

                text = "\n".join(lines)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"stale_tickets": stale},
                meta={"repo": repo, "inactive_days": inactive_days},
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
            await ctx.error(f"💥 Неожиданная ошибка при поиске 'застоявшихся' тикетов: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при поиске 'застоявшихся' тикетов: {e}",
                )
            ) from e
