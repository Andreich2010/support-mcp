"""Инструмент для генерации простого отчёта по тикетам поддержки."""

import datetime
import os
from collections import Counter
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
async def generate_support_report(
    period_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="За сколько дней строить отчёт (по updated_at).",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    📊 Генерирует простой отчёт по тикетам за последние N дней.

    Метрики:
    - количество тикетов (всего, open, closed);
    - распределение по типу (bug/feature/question/support по labels);
    - базовое распределение по приоритету (priority: ...).
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("generate_support_report") as span:
        span.set_attribute("period_days", period_days)

        try:
            await ctx.info(
                f"📊 Генерируем отчёт по тикетам за последние {period_days} дней"
            )
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            span.set_attribute("github_repo", repo)

            headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            now = datetime.datetime.now(datetime.timezone.utc)
            since_dt = now - datetime.timedelta(days=period_days)
            since_iso = since_dt.isoformat().replace("+00:00", "Z")

            url = f"https://api.github.com/repos/{repo}/issues"
            params = {
                "state": "all",
                "since": since_iso,
                "per_page": 100,
            }

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                issues: List[Dict[str, Any]] = resp.json()

            await ctx.report_progress(progress=60, total=100)

            total = 0
            opened = 0
            closed = 0
            types = Counter()
            priorities = Counter()

            for issue in issues:
                if "pull_request" in issue:
                    continue  # PR не считаем

                total += 1
                state = issue.get("state") or "open"
                if state == "open":
                    opened += 1
                else:
                    closed += 1

                labels = [l.get("name", "").lower() for l in issue.get("labels", [])]

                # тип тикета по label (bug/feature/question/support)
                for t in ("bug", "feature", "question", "support"):
                    if t in labels:
                        types[t] += 1
                        break

                # приоритет по label `priority: ...`
                for lbl in labels:
                    if lbl.startswith("priority:"):
                        pr = lbl.split(":", 1)[1].strip()
                        priorities[pr] += 1

            await ctx.report_progress(progress=100, total=100)

            lines = [
                f"📊 Отчёт по тикетам за последние {period_days} дней:",
                f"- Всего тикетов: {total}",
                f"- Открыто: {opened}",
                f"- Закрыто: {closed}",
                "",
                "Распределение по типам:",
            ]
            if types:
                lines.extend(f"- {t}: {c}" for t, c in types.items())
            else:
                lines.append("- (типы по labels не определены)")

            lines.extend(("", "Распределение по приоритетам:"))
            if priorities:
                lines.extend(f"- {p}: {c}" for p, c in priorities.items())
            else:
                lines.append("- (приоритеты по labels не определены)")

            text = "\n".join(lines)

            structured = {
                "period_days": period_days,
                "total": total,
                "opened": opened,
                "closed": closed,
                "types": dict(types),
                "priorities": dict(priorities),
            }

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content=structured,
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
            await ctx.error(f"💥 Неожиданная ошибка при построении отчёта: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при построении отчёта: {e}",
                )
            ) from e
