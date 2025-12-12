"""Инструмент для ответа AI на вопрос пользователя в тикете (/ask-ai)."""

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
async def answer_ticket_question(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    comments_limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Сколько последних комментариев учитывать при формировании ответа.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    💬 Ответ AI-помощника на вопрос пользователя в тикете.

    Предполагается, что пользователь мог написать что-то вроде:
    `/ask-ai ...` в последнем комментарии.

    Шаги:
    1) Читаем тикет (title/body) и последние комментарии.
    2) Фокусируемся на *последнем* комментарии как на текущем вопросе.
    3) Просим модель сформулировать ответ.
    4) Публикуем ответ в GitHub комментарием.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("answer_ticket_question") as span:
        span.set_attribute("issue_number", issue_number)
        span.set_attribute("comments_limit", comments_limit)

        try:
            await ctx.info(
                f"💬 Формируем ответ AI для тикета #{issue_number}"
            )
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            span.set_attribute("github_repo", repo)

            if not token:
                msg = "Для публикации ответа требуется GITHUB_TOKEN с правами записи."
                await ctx.error(msg)
                raise McpError(
                    ErrorData(
                        code=-32602,
                        message=msg,
                    )
                )

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
                author: str = (issue.get("user") or {}).get("login") or "unknown"

                # 2) Последние комментарии
                comments_url = f"{base_issue_url}/comments"
                resp_comments = await client.get(
                    comments_url,
                    headers=headers,
                    params={"per_page": max(10, comments_limit)},
                )
                resp_comments.raise_for_status()
                comments: List[Dict[str, Any]] = resp_comments.json()

            await ctx.report_progress(progress=40, total=100)

            comments_sorted = sorted(
                comments,
                key=lambda c: c.get("created_at") or "",
            )
            last_comments = comments_sorted[-comments_limit:] if comments_sorted else []

            last_comment_body = ""
            last_comment_author = ""
            comments_block_lines: List[str] = []

            for c in last_comments:
                c_author = (c.get("user") or {}).get("login") or "unknown"
                c_body = c.get("body") or ""
                comments_block_lines.append(f"[{c_author}]: {c_body}")

            if last_comments:
                last = last_comments[-1]
                last_comment_body = last.get("body") or ""
                last_comment_author = (last.get("user") or {}).get("login") or "unknown"

            comments_block = "\n".join(comments_block_lines)

            # 3) Формируем промпт для модели
            prompt_text = (
                "Ты — AI-помощник службы технической поддержки.\n"
                "У тебя есть тикет (заголовок, описание) и последние комментарии.\n"
                "Нужно сформулировать ответ на последнее сообщение пользователя.\n\n"
                "Требования к ответу:\n"
                "- пиши по-русски, дружелюбно, но по делу;\n"
                "- если не хватает информации — задай уточняющие вопросы;\n"
                "- предложи конкретные шаги (что проверить, где посмотреть лог, и т.п.);\n"
                "- не пиши лишней воды.\n\n"
                f"Автор тикета: {author}\n"
                f"Заголовок тикета: {title}\n\n"
                f"Описание тикета:\n{body}\n\n"
                "Последние комментарии в тикете:\n"
                f"{comments_block}\n\n"
                "Последнее сообщение пользователя, на которое нужно ответить:\n"
                f"[{last_comment_author}]: {last_comment_body}\n\n"
                "=== ОТВЕТ ДЛЯ ПОЛЬЗОВАТЕЛЯ ==="
            )

            await ctx.report_progress(progress=70, total=100)

            ai_answer = await ctx.prompt(prompt_text)
            answer_text = ai_answer if isinstance(ai_answer, str) else str(ai_answer)

            # 4) Публикуем ответ в GitHub
            reply = (
                "💬 Ответ AI-помощника:\n\n"
                f"{answer_text}"
            )
            comment_result = await post_ticket_reply(
                issue_number=issue_number,
                reply_text=reply,
                ctx=ctx,
            )

            comment_url: Optional[str] = None
            if comment_result.structured_content:
                c = comment_result.structured_content.get("comment") or {}
                comment_url = c.get("comment_url")

            await ctx.report_progress(progress=100, total=100)

            human = (
                f"Сформирован и опубликован ответ AI для тикета #{issue_number}."
            )

            return ToolResult(
                content=[TextContent(type="text", text=human)],
                structured_content={
                    "issue_number": issue_number,
                    "answer": answer_text,
                    "comment_url": comment_url,
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
            await ctx.error(f"💥 Неожиданная ошибка при ответе в тикете: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при ответе в тикете: {e}",
                )
            ) from e
