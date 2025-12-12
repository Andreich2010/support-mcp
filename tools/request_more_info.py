"""Инструмент для автоматического запроса дополнительной информации у пользователя."""

from typing import Optional

from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace

from mcp_instance import mcp
from .utils import ToolResult
from .get_ticket_last_comment import get_ticket_last_comment
from .post_ticket_reply import post_ticket_reply

tracer = trace.get_tracer(__name__)


@mcp.tool()
async def request_more_info(
    issue_number: int,
    ctx: Context | None = None,
) -> ToolResult:
    """
    ❓ Запрос дополнительной информации по тикету.

    1) Берём последний комментарий по тикету (обычно от пользователя).
    2) Просим модель сформулировать уточняющие вопросы.
    3) Оставляем комментарий в GitHub от имени агента.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("request_more_info") as span:
        span.set_attribute("issue_number", issue_number)

        try:
            await ctx.info(
                f"❓ Формируем уточняющие вопросы по тикету #{issue_number}"
            )
            await ctx.report_progress(progress=0, total=100)

            # 1) Получаем последний комментарий
            last_comment_result = await get_ticket_last_comment(
                issue_number=issue_number,
                ctx=ctx,
            )

            last_body: str = ""
            if last_comment_result.structured_content:
                if c := last_comment_result.structured_content.get("comment"):
                    last_body = c.get("body") or ""

            if not last_body:
                last_body = "Пользователь пока не оставил подробного описания."

            await ctx.report_progress(progress=30, total=100)

            # 2) Просим модель сформулировать вопросы
            prompt_text = (
                "Ты — вежливый и грамотный специалист 1-й линии поддержки.\n"
                "По тексту обращения пользователя предложи до 5 конкретных "
                "уточняющих вопросов, которые помогут быстрее диагностировать проблему.\n"
                "Пиши по-русски, вежливо, в виде нумерованного списка.\n\n"
                f"Текст последнего сообщения пользователя:\n{last_body}\n\n"
                "Если вопросов нет, напиши одну строку: 'На данный момент уточнений не требуется.'"
            )

            ai_answer = await ctx.prompt(prompt_text)
            questions_text = (
                ai_answer if isinstance(ai_answer, str) else str(ai_answer)
            )

            await ctx.report_progress(progress=60, total=100)

            # 3) Публикуем комментарий в тикете
            reply_text = (
                "Здравствуйте! Спасибо за обращение.\n\n"
                "Чтобы мы могли быстрее помочь, уточните, пожалуйста, несколько моментов:\n\n"
                f"{questions_text}"
            )

            _ = await post_ticket_reply(
                issue_number=issue_number,
                reply_text=reply_text,
                ctx=ctx,
            )

            await ctx.report_progress(progress=100, total=100)

            human_text = (
                f"Уточняющие вопросы отправлены в тикет #{issue_number}."
            )

            return ToolResult(
                content=[TextContent(type="text", text=human_text)],
                structured_content={
                    "issue_number": issue_number,
                    "questions_text": questions_text,
                },
            )

        except Exception as e:  # noqa: BLE001
            await ctx.error(f"💥 Ошибка при формировании уточняющих вопросов: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Ошибка запроса дополнительной информации: {e}",
                )
            ) from e
