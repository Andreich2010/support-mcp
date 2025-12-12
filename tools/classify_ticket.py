import os
from typing import Dict, Any

import httpx
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace

from mcp_instance import mcp
from .utils import ToolResult, require_env

tracer = trace.get_tracer(__name__)


@mcp.tool()
async def classify_ticket(
    issue_number: int,
    ctx: Context | None = None,
) -> ToolResult:
    """
    🧠 Классификация тикета с помощью AI:
    - тип: bug / feature / question / support
    - приоритет: low / medium / high / urgent
    """

    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("classify_ticket") as span:
        span.set_attribute("issue_number", issue_number)

        await ctx.info(f"🤖 Анализируем тикет #{issue_number} для классификации")

        try:
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")

            span.set_attribute("github_repo", repo)

            headers = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = {f"Bearer {token}"}

            # 1. Забираем сам тикет
            async with httpx.AsyncClient(timeout=20.0) as client:
                issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
                resp_issue = await client.get(issue_url, headers=headers)
                resp_issue.raise_for_status()
                issue = resp_issue.json()

            title = issue.get("title", "")
            body = issue.get("body", "")

            # 2. Забираем последний комментарий
            from .get_ticket_last_comment import get_ticket_last_comment

            last_comment_result = await get_ticket_last_comment(issue_number, ctx)
            last_comment = ""
            if last_comment_result.structured_content:
                if c := last_comment_result.structured_content.get("comment"):
                    last_comment = c.get("body", "")

            # 3. Формируем текст для AI
            analysis_text = (
                f"Title: {title}\n"
                f"Body: {body}\n"
                f"Last user comment: {last_comment}\n\n"
                "Определи:\n"
                "- тип тикета (bug, feature, question, support)\n"
                "- приоритет (low, medium, high, urgent)\n"
                "Ответ строго в JSON:\n"
                "{\"type\": \"bug\", \"priority\": \"high\"}"
            )

            ai_response = await ctx.prompt(analysis_text)
            classification = ai_response.get("parsed", {})

            ticket_type = classification.get("type", "question")
            priority = classification.get("priority", "medium")

            # 4. Обновляем метаданные через update_ticket_meta
            from .update_ticket_meta import update_ticket_meta

            update_res = await update_ticket_meta(
                issue_number=issue_number,
                priority=priority,
                labels=[ticket_type],
                ctx=ctx,
            )

            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Тикет #{issue_number} классифицирован как {ticket_type}, приоритет {priority}"
                    )
                ],
                structured_content={
                    "issue_number": issue_number,
                    "type": ticket_type,
                    "priority": priority
                },
            )

        except Exception as e:
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Ошибка классификации тикета: {e}",
                )
            ) from e
