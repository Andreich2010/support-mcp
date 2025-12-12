"""Инструмент для разбиения тикета на подзадачи (sub-issues) в GitHub."""

import json
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
async def create_subtasks_from_ticket(
    issue_number: int = Field(
        ...,
        ge=1,
        description="Номер родительского issue в GitHub (тот, что после #, например 3).",
    ),
    max_subtasks: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Максимальное количество подзадач, которые можно создать.",
    ),
    dry_run: bool = Field(
        default=False,
        description="Если True — только сгенерировать подзадачи, но не создавать issues в GitHub.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    🧩 Разбиение тикета на подзадачи.

    Шаги:
    1) Берём title/body родительского тикета и несколько последних комментариев.
    2) Просим модель предложить структуру подзадач в JSON.
    3) Если dry_run=False — создаём подзадачи как отдельные issues в GitHub,
       помечаем их ссылкой на родителя и оставляем комментарий в родителе.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("create_subtasks_from_ticket") as span:
        span.set_attribute("issue_number", issue_number)
        span.set_attribute("max_subtasks", max_subtasks)
        span.set_attribute("dry_run", dry_run)

        try:
            await ctx.info(
                f"🧩 Разбиваем тикет #{issue_number} на подзадачи (dry_run={dry_run})"
            )
            await ctx.report_progress(progress=0, total=100)

            # 1) Настройки GitHub
            repo = require_env("GITHUB_REPO")
            token = os.getenv("GITHUB_TOKEN")
            if not token and not dry_run:
                msg = (
                    "Для создания подзадач требуется GITHUB_TOKEN с правами записи. "
                    "Можно использовать dry_run=True для мокового режима."
                )
                await ctx.error(msg)
                raise McpError(
                    ErrorData(
                        code=-32602,
                        message=msg,
                    )
                )

            span.set_attribute("github_repo", repo)

            base_issue_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with httpx.AsyncClient(timeout=20.0) as client:
                # 2) Забираем родительский тикет
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
                parent_url: str = issue.get("html_url") or ""

                # 3) Берём несколько последних комментариев (для контекста)
                comments_url = f"{base_issue_url}/comments"
                resp_comments = await client.get(
                    comments_url, headers=headers, params={"per_page": 10}
                )
                resp_comments.raise_for_status()
                comments: List[Dict[str, Any]] = resp_comments.json()

            await ctx.report_progress(progress=30, total=100)

            comments_text_parts: List[str] = []
            for c in comments[-5:]:
                author = (c.get("user") or {}).get("login") or "unknown"
                text = c.get("body") or ""
                comments_text_parts.append(f"[{author}]: {text}")

            comments_block = "\n".join(comments_text_parts)

            # 4) Формируем промпт для модели
            prompt_text = (
                "Ты — тимлид/архитектор. На основе описания задачи и комментариев "
                "предложи разбиение на подзадачи для разработчиков.\n\n"
                "Нужно:\n"
                "- Разбить задачу на небольшие, независимые шаги (подзадачи).\n"
                "- Каждая подзадача должна иметь: title (кратко) и body (что сделать).\n"
                "- Можно указать label (напр. backend, frontend, docs), если уместно.\n"
                f"- Не более {max_subtasks} подзадач.\n\n"
                "Ответь строго в JSON формата:\n"
                "{\n"
                '  "subtasks": [\n'
                '    {"title": "...", "body": "...", "labels": ["optional", "labels"]},\n'
                "    ...\n"
                "  ]\n"
                "}\n\n"
                "=== РОДИТЕЛЬСКИЙ ТИКЕТ ===\n"
                f"Заголовок: {title}\n\n"
                f"Описание:\n{body}\n\n"
                "=== ПОСЛЕДНИЕ КОММЕНТАРИИ ===\n"
                f"{comments_block}\n"
                "=== КОНЕЦ ===\n"
            )

            await ctx.report_progress(progress=50, total=100)

            ai_raw = await ctx.prompt(prompt_text)
            ai_text = ai_raw if isinstance(ai_raw, str) else str(ai_raw)

            try:
                parsed = json.loads(ai_text)
            except Exception:
                parsed = {}

            subtasks: List[Dict[str, Any]] = parsed.get("subtasks") or []
            if not isinstance(subtasks, list):
                subtasks = []

            # Ограничим количество, если модель разошлась
            subtasks = subtasks[:max_subtasks]

            if not subtasks:
                text = (
                    "Модель не смогла предложить подзадачи или ответ не распознан как JSON."
                )
                return ToolResult(
                    content=[TextContent(type="text", text=text)],
                    structured_content={
                        "issue_number": issue_number,
                        "created_subtasks": [],
                        "dry_run": dry_run,
                    },
                    meta={"repo": repo},
                )

            await ctx.info(f"🧩 Модель предложила подзадач: {len(subtasks)}")
            await ctx.report_progress(progress=70, total=100)

            created: List[Dict[str, Any]] = []

            if not dry_run:
                # 5) Создаём подзадачи как отдельные issues
                async with httpx.AsyncClient(timeout=20.0) as client:
                    for st in subtasks:
                        st_title: str = st.get("title") or "Подзадача без названия"
                        st_body: str = st.get("body") or ""
                        st_labels: List[str] = st.get("labels") or []

                        # Добавим ссылку на родителя в body
                        full_body = (
                            f"{st_body}\n\n"
                            f"---\n"
                            f"Родительский тикет: #{issue_number} ({parent_url})"
                        )

                        payload: Dict[str, Any] = {
                            "title": st_title,
                            "body": full_body,
                        }
                        if st_labels:
                            payload["labels"] = st_labels

                        create_url = f"https://api.github.com/repos/{repo}/issues"
                        resp_create = await client.post(
                            create_url, headers=headers, json=payload
                        )
                        resp_create.raise_for_status()
                        child_issue: Dict[str, Any] = resp_create.json()

                        created.append(
                            {
                                "number": child_issue.get("number"),
                                "title": child_issue.get("title"),
                                "url": child_issue.get("html_url"),
                                "labels": [l.get("name", "") for l in child_issue.get("labels", [])],
                            }
                        )

                # 6) Комментарий в родительском тикете с ссылками на подзадачи
                if created:
                    lines = ["Созданы подзадачи:"]
                    lines.extend(
                        f"- #{ch['number']}: {ch['title']} -> {ch['url']}"
                        for ch in created
                    )
                    comment_text = "\n".join(lines)

                    await post_ticket_reply(
                        issue_number=issue_number,
                        reply_text=comment_text,
                        ctx=ctx,
                    )

            await ctx.report_progress(progress=100, total=100)

            # Формируем человекочитаемый ответ
            if dry_run:
                lines = ["Режим dry_run: подзадачи не создавались, только предложены:"]
                lines.extend(f"- {st.get('title')}" for st in subtasks)
                human_text = "\n".join(lines)
            elif created:
                lines = ["Созданы следующие подзадачи:"]
                lines.extend(
                    f"- #{ch['number']}: {ch['title']} (labels: {', '.join(ch['labels'])})"
                    for ch in created
                )
                human_text = "\n".join(lines)
            else:
                human_text = (
                    "Модель предложила подзадачи, но ни одна не была создана "
                    "(возможно, ошибка при обращении к GitHub API)."
                )

            return ToolResult(
                content=[TextContent(type="text", text=human_text)],
                structured_content={
                    "issue_number": issue_number,
                    "dry_run": dry_run,
                    "suggested_subtasks": subtasks,
                    "created_subtasks": created,
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
            await ctx.error(f"💥 Неожиданная ошибка при создании подзадач: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Неожиданная ошибка при создании подзадач: {e}",
                )
            ) from e
