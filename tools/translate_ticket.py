"""Инструмент для автоматического перевода тикета (title/body/комментарии)."""

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
async def translate_ticket(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер issue в GitHub (тот, что после #, например 3).",
    ),
    target_lang: str = Field(
        ...,
        description="Язык перевода, например: 'ru' или 'en'.",
    ),
    include_comments: bool = Field(
        default=True,
        description="Включать ли последние комментарии в перевод.",
    ),
    comments_limit: int = Field(
        default=5,
        ge=0,
        le=20,
        description="Сколько последних комментариев переводить.",
    ),
    post_comment: bool = Field(
        default=True,
        description="Оставить ли переведённый текст в комментарии тикета.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    🌐 Переводит тикет (title/body + N последних комментариев) на target_lang.

    Может использоваться:
    - для перевода обращения клиента для русской команды;
    - для ответа клиенту на его языке.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("translate_ticket") as span:
        span.set_attribute("issue_number", issue_number)
        span.set_attribute("target_lang", target_lang)

        try:
            await ctx.info(
                f"🌐 Переводим тикет #{issue_number} на язык {target_lang!r}"
            )
            await ctx.report_progress(progress=0, total=100)

            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            span.set_attribute("github_repo", repo)

            headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            base_issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"

            async with httpx.AsyncClient(timeout=20.0) as client:
                # 1) сам тикет
                resp_issue = await client.get(base_issue_url, headers=headers)
                resp_issue.raise_for_status()
                issue: Dict[str, Any] = resp_issue.json()

                title: str = issue.get("title") or ""
                body: str = issue.get("body") or ""
                issue_url: str = issue.get("html_url") or ""

                comments_block = ""
                if include_comments and comments_limit > 0:
                    comments_url = f"{base_issue_url}/comments"
                    resp_comments = await client.get(
                        comments_url,
                        headers=headers,
                        params={"per_page": max(comments_limit, 10)},
                    )
                    resp_comments.raise_for_status()
                    comments: List[Dict[str, Any]] = resp_comments.json()

                    # Берём последние comments_limit
                    last_comments = comments[-comments_limit:]
                    parts: List[str] = []
                    for c in last_comments:
                        author = (c.get("user") or {}).get("login") or "unknown"
                        text = c.get("body") or ""
                        parts.append(f"[{author}]: {text}")
                    comments_block = "\n".join(parts)

            await ctx.report_progress(progress=30, total=100)

            # 2) Собираем текст для перевода
            src_text_parts: List[str] = [
                f"Title:\n{title}",
                f"Body:\n{body}",
            ]
            if comments_block:
                src_text_parts.append("Comments:\n" + comments_block)

            src_text = "\n\n".join(src_text_parts)

            # 3) Просим модель перевести
            prompt_text = (
                "Ты — профессиональный переводчик технических текстов.\n"
                f"Переведи следующий текст на язык '{target_lang}'.\n"
                "Сохраняй структуру (заголовки, разделы), но не добавляй пояснений от себя.\n\n"
                "=== ТЕКСТ ДЛЯ ПЕРЕВОДА ===\n"
                f"{src_text}\n"
                "=== КОНЕЦ ТЕКСТА ==="
            )

            await ctx.report_progress(progress=50, total=100)

            ai_answer = await ctx.prompt(prompt_text)
            translated_text = ai_answer if isinstance(ai_answer, str) else str(ai_answer)

            await ctx.report_progress(progress=80, total=100)

            comment_url: Optional[str] = None
            if post_comment:
                reply = (
                    f"🌐 Перевод тикета #{issue_number} на язык {target_lang}:\n\n"
                    f"{translated_text}"
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

            human = (
                f"Перевод тикета #{issue_number} выполнен."
                + (" Результат отправлен в комментарий." if post_comment else "")
            )

            return ToolResult(
                content=[TextContent(type="text", text=human)],
                structured_content={
                    "issue_number": issue_number,
                    "target_lang": target_lang,
                    "translated_text": translated_text,
                    "comment_url": comment_url,
                },
                meta={"repo": repo, "issue_url": issue_url},
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
            await ctx.error(f"💥 Неожиданная ошибка при переводе тикета: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при переводе тикета: {e}",
                )
            ) from e
