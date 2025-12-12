"""Инструмент для анализа ошибок / логов в тикете GitHub."""

import os
import textwrap
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
async def analyze_ticket_error(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    comments_limit: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Сколько последних комментариев включать в анализ.",
    ),
    post_comment: bool = Field(
        default=True,
        description="Оставить ли комментарий с результатами анализа в тикете.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    🧠 Разбор ошибок / логов по тикету:

    1) Берёт title + body тикета.
    2) Подтягивает последние N комментариев (обычно там логи/стектрейсы).
    3) Просит модель проанализировать ошибки и предложить:
       - вероятную причину;
       - шаги диагностики;
       - возможные фиксы.
    4) (опционально) оставляет комментарий с анализом в GitHub.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("analyze_ticket_error") as span:
        span.set_attribute("issue_number", issue_number)
        span.set_attribute("comments_limit", comments_limit)
        span.set_attribute("post_comment", post_comment)

        try:
            await ctx.info(f"🧠 Анализируем ошибку / логи по тикету #{issue_number}")
            await ctx.report_progress(progress=0, total=100)

            # 1) Настройки GitHub
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")  # для публичных реп может быть пустым
            span.set_attribute("github_repo", repo)

            headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with httpx.AsyncClient(timeout=20.0) as client:
                # 2) Забираем сам issue
                issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
                resp_issue = await client.get(issue_url, headers=headers)
                resp_issue.raise_for_status()
                issue: Dict[str, Any] = resp_issue.json()

                title: str = issue.get("title") or ""
                body: str = issue.get("body") or ""

                # 3) Последние комментарии
                comments_text_parts: List[str] = []
                if comments_limit > 0:
                    comments_url = f"{issue_url}/comments"
                    # GitHub не сортирует по updated, поэтому берём побольше и сами режем
                    resp_comments = await client.get(
                        comments_url,
                        headers=headers,
                        params={"per_page": max(comments_limit, 10)},
                    )
                    resp_comments.raise_for_status()
                    comments: List[Dict[str, Any]] = resp_comments.json()

                    # Берём последние N (по created_at)
                    comments_sorted = sorted(
                        comments,
                        key=lambda c: c.get("created_at") or "",
                    )[-comments_limit:]

                    for c in comments_sorted:
                        author = (c.get("user") or {}).get("login") or "unknown"
                        text = c.get("body") or ""
                        comments_text_parts.append(
                            f"[Комментарий от {author}]\n{text}"
                        )

            await ctx.report_progress(progress=30, total=100)

            # 4) Собираем контекст для модели
            issue_context_parts: List[str] = [
                f"Тикет #{issue_number}",
                f"Заголовок:\n{title}",
                "",
                f"Описание тикета:\n{body}",
            ]
            if comments_text_parts:
                issue_context_parts.append("\nПоследние комментарии:")
                issue_context_parts.extend(comments_text_parts)

            issue_context = "\n\n".join(issue_context_parts)

            # На всякий случай ограничим размер (чтобы не убить модель)
            max_chars = 8000
            if len(issue_context) > max_chars:
                issue_context = issue_context[-max_chars:]

            # 5) Промпт для анализа
            prompt_text = textwrap.dedent(
                f"""
                Ты — опытный инженер поддержки и разработчик (backend/devops).

                По приведённому ниже тикету (описание + комментарии) проанализируй:
                1. Какую проблему описывает пользователь.
                2. Какие ошибки/логи/стектрейсы присутствуют.
                3. Что, с высокой вероятностью, является причиной проблемы.
                4. Какие шаги диагностики можно предложить (пошагово).
                5. Какие варианты решения можно предложить (конкретные действия).
                6. Если не хватает данных — явно укажи, что нужно уточнить.

                Пиши по-русски, структурировано, с подзаголовками и списками.

                === НАЧАЛО ТИКЕТА ===
                {issue_context}
                === КОНЕЦ ТИКЕТА ===
                """
            ).strip()

            await ctx.report_progress(progress=50, total=100)

            ai_answer = await ctx.prompt(prompt_text)
            analysis_text = ai_answer if isinstance(ai_answer, str) else str(ai_answer)

            await ctx.report_progress(progress=80, total=100)

            # 6) (опционально) публикуем комментарий в тикете
            comment_url: Optional[str] = None
            if post_comment:
                reply = (
                    "🔍 Предварительный анализ ошибки / логов от AI-агента:\n\n"
                    f"{analysis_text}"
                )
                comment_result = await post_ticket_reply(
                    issue_number=issue_number,
                    reply_text=reply,
                    ctx=ctx,
                )
                if comment_result.structured_content:
                    c = comment_result.structured_content.get("comment") or {}
                    comment_url = c.get("comment_url")

            await ctx.report_progress(progress=100, total=100)

            human_text = (
                f"Анализ логов/ошибки по тикету #{issue_number} выполнен."
                + (" Результат отправлен в комментарий." if post_comment else ""
            )
            )

            return ToolResult(
                content=[TextContent(type="text", text=human_text)],
                structured_content={
                    "issue_number": issue_number,
                    "analysis": analysis_text,
                    "comment_url": comment_url,
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
            await ctx.error(f"💥 Неожиданная ошибка при анализе ошибки/логов: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при анализе ошибки/логов: {e}",
                )
            ) from e
