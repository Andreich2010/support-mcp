# server.py
import os
from dotenv import load_dotenv, find_dotenv
from opentelemetry import trace

load_dotenv(find_dotenv())

from mcp_instance import mcp

from tools.get_new_tickets import get_new_tickets  # noqa: F401
from tools.get_ticket_detail import get_ticket_detail  # noqa: F401
from tools.post_ticket_reply import post_ticket_reply  # noqa: F401
from tools.get_ticket_last_comment import get_ticket_last_comment # noqa: F401
from tools.update_ticket_meta import update_ticket_meta # noqa: F401
from tools.classify_ticket import classify_ticket # noqa: F401
from tools.request_more_info import request_more_info # noqa: F401
from tools.get_stale_tickets import get_stale_tickets  # noqa: F401
from tools.analyze_ticket_error import analyze_ticket_error  # noqa: F401
from tools.docs_rag import list_docs, search_docs, answer_from_docs  # noqa: F401
from tools.create_subtasks_from_ticket import create_subtasks_from_ticket  # noqa: F401
from tools.translate_ticket import translate_ticket  # noqa: F401
from tools.generate_support_report import generate_support_report  # noqa: F401
from tools.answer_ticket_question import answer_ticket_question  # noqa: F401
from tools.summarize_ticket import summarize_ticket  # noqa: F401
from tools.close_ticket import close_ticket  # noqa: F401


PORT = int(os.getenv("PORT", "8080"))

tracer = trace.get_tracer(__name__)


def init_tracing() -> None:
    # пока заглушка
    pass


init_tracing()


@mcp.prompt()
def support_prompt(query: str = "") -> str:
    return f"Ты — AI-помощник службы поддержки. Обработай запрос: {query}"


def main() -> None:
    print("=" * 60)
    print("🌐 ЗАПУСК MCP СЕРВЕРА ДЛЯ ТИКЕТОВ ПОДДЕРЖКИ")
    print("=" * 60)
    print(f"🚀 MCP Server: http://0.0.0.0:{PORT}/mcp")
    print("=" * 60)

    # Официальный SDK: транспорт задаём только тут
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
