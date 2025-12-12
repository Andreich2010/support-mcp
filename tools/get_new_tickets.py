"""Инструмент для получения новых тикетов (issues) из GitHub."""

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
async def get_new_tickets(
    since_minutes: int = Field(
        ...,
        ge=1,
        le=1440,
        description="За сколько последних минут смотреть новые тикеты (1–1440).",
    ),
    ctx: Context | None = None,
) -> ToolResult:    # sourcery skip: low-code-quality
    """
    📝 Получение новых тикетов из GitHub Issues.

    Args:
        since_minutes: Интервал в минутах, за который нужно получить тикеты.
        ctx: Контекст для логирования и прогресса.

    Returns:
        ToolResult: Список тикетов и краткое человекочитаемое описание.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("get_new_tickets") as span:
        span.set_attribute("since_minutes", since_minutes)

        try:
            await ctx.info("🚀 Начинаем загрузку тикетов из GitHub")
            await ctx.report_progress(progress=0, total=100)

            # 1) Настройки GitHub из окружения
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")  # может быть пустым

            span.set_attribute("github_repo", repo)

            # 2) Считаем since для GitHub API
            now = datetime.datetime.now(datetime.timezone.utc)
            since_dt = now - datetime.timedelta(minutes=since_minutes)
            since_iso = f"{since_dt.isoformat()}Z"

            await ctx.info(f"📅 Берём тикеты с {since_iso}")
            await ctx.report_progress(progress=20, total=100)

            # 3) Запрос к GitHub Issues API
            url = f"https://api.github.com/repos/{repo}/issues"
            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            params = {
                "since": since_iso,
                "state": "all",
            }

            await ctx.info("📡 Запрашиваем GitHub Issues API")
            await ctx.report_progress(progress=50, total=100)

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                issues: List[Dict[str, Any]] = response.json()

            await ctx.info(f"✅ Получено тикетов: {len(issues)}")
            await ctx.report_progress(progress=80, total=100)

            simplified: List[Dict[str, Any]] = [
                {
                    "id": issue.get("id"),
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "created_at": issue.get("created_at"),
                    "updated_at": issue.get("updated_at"),
                    "url": issue.get("html_url"),
                    "user": issue.get("user", {}).get("login"),
                }
                for issue in issues
                if "pull_request" not in issue
            ]
            await ctx.report_progress(progress=100, total=100)

            if not simplified:
                text = "За указанный период новых тикетов в GitHub не найдено."
            else:
                lines = ["Найденные тикеты в GitHub:"]
                lines.extend(
                    f"- #{item['number']} [{item['state']}] {item['title']} (от {item['user']})"
                    for item in simplified[:10]
                )
                if len(simplified) > 10:
                    lines.append(f"... и ещё {len(simplified) - 10} тикетов.")

                text = "\n".join(lines)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"tickets": simplified},
                meta={"since_minutes": since_minutes, "repo": repo},
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
