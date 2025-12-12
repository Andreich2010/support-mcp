"""Инструменты для работы с локальной документацией (простейший RAG)."""

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from mcp.server.fastmcp import Context
from mcp.types import TextContent
from opentelemetry import trace
from pydantic import Field

from mcp_instance import mcp
from .utils import ToolResult

tracer = trace.get_tracer(__name__)


# --- Вспомогательные функции (НЕ MCP-инструменты) ---


def _get_docs_dir() -> Path:
    """
    Возвращает корневой каталог документации.

    По умолчанию: ./docs
    Можно переопределить через переменную окружения DOCS_DIR.
    """
    docs_dir = os.getenv("DOCS_DIR", "docs")
    return Path(docs_dir).resolve()


def _iter_doc_files() -> List[Path]:
    """
    Возвращает список файлов документации (md, rst, txt) в каталоге docs.
    """
    base = _get_docs_dir()
    if not base.exists() or not base.is_dir():
        return []

    exts = {".md", ".rst", ".txt"}
    files: List[Path] = []
    files.extend(
        path
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in exts
    )
    return files


def _simple_score(text: str, query: str) -> int:
    """
    Простейший скоринг: считаем вхождения слов запроса в тексте.
    """
    text_lower = text.lower()
    tokens = re.findall(r"\w+", query.lower())
    return sum(text_lower.count(tok) for tok in tokens if tok)


def _search_in_file(path: Path, query: str, max_snippets: int = 3) -> List[Dict[str, Any]]:
    """
    Ищем совпадения в одном файле, возвращаем сниппеты.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    # Разобьём по "абзацам" (пустые строки как разделители)
    raw_paragraphs = re.split(r"\n\s*\n", content)
    snippets: List[Dict[str, Any]] = []

    for para in raw_paragraphs:
        para_stripped = para.strip()
        if not para_stripped:
            continue

        score = _simple_score(para_stripped, query)
        if score <= 0:
            continue

        # Ограничим размер фрагмента
        if len(para_stripped) > 600:
            para_stripped = f"{para_stripped[:600]}..."

        snippets.append(
            {
                "file": str(path),
                "score": score,
                "snippet": para_stripped,
            }
        )

    # Отсортируем фрагменты по релевантности
    snippets.sort(key=lambda x: x["score"], reverse=True)
    return snippets[:max_snippets]


def _search_docs_internal(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Внутренняя функция поиска по всем документам.
    """
    files = _iter_doc_files()
    all_snippets: List[Dict[str, Any]] = []

    for f in files:
        snippets = _search_in_file(f, query, max_snippets=3)
        all_snippets.extend(snippets)

    all_snippets.sort(key=lambda x: x["score"], reverse=True)
    return all_snippets[:max_results]


# --- MCP-инструменты ---


@mcp.tool()
async def list_docs(ctx: Context | None = None) -> ToolResult:
    """
    📚 Список доступных файлов документации.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("list_docs"):
        try:
            docs_dir = _get_docs_dir()
            files = _iter_doc_files()

            if not files:
                text = (
                    f"Каталог документации не найден или пуст: {docs_dir}\n"
                    "Создайте папку docs/ и добавьте туда .md / .rst / .txt файлы."
                )
            else:
                rels = [str(p.relative_to(docs_dir)) for p in files]
                lines = ["Найдены файлы документации:"]
                lines.extend(f"- {r}" for r in rels)
                text = "\n".join(lines)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "docs_dir": str(docs_dir),
                    "files": [str(p) for p in files],
                },
            )

        except Exception as e:  # noqa: BLE001
            await ctx.error(f"💥 Ошибка при перечислении документации: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Ошибка при перечислении документации: {e}",
                )
            ) from e


@mcp.tool()
async def search_docs(
    query: str = Field(
        ...,
        min_length=1,
        description="Поисковый запрос по документации.",
    ),
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Максимальное количество возвращаемых фрагментов.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    🔎 Поиск по локальной документации (простой full-text).
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("search_docs") as span:
        span.set_attribute("query", query)
        span.set_attribute("max_results", max_results)

        try:
            await ctx.info(f"🔎 Ищем по документации: {query!r}")
            await ctx.report_progress(progress=0, total=100)

            snippets = _search_docs_internal(query=query, max_results=max_results)

            if not snippets:
                text = f"По запросу {query!r} ничего не найдено в документации."
            else:
                lines = [f"Результаты поиска по запросу {query!r}:"]
                lines.extend(
                    f"\n📄 {s['file']} (score={s['score']}):\n{s['snippet']}"
                    for s in snippets
                )
                text = "\n".join(lines)

            await ctx.report_progress(progress=100, total=100)

            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={"results": snippets},
                meta={"query": query},
            )

        except Exception as e:  # noqa: BLE001
            await ctx.error(f"💥 Ошибка при поиске по документации: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Ошибка при поиске по документации: {e}",
                )
            ) from e


@mcp.tool()
async def answer_from_docs(
    query: str = Field(
        ...,
        min_length=1,
        description="Вопрос к системе, на который нужно ответить, опираясь на документацию.",
    ),
    max_context_fragments: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Сколько фрагментов документации включать в контекст ответа.",
    ),
    ctx: Context | None = None,
) -> ToolResult:
    """
    📘 Ответ на вопрос на основе документации (простейший RAG).

    ВАЖНО: сам MCP-сервер НЕ вызывает LLM.
    Он подбирает релевантные фрагменты документации и возвращает их,
    а уже модель-хост (агент) формирует финальный ответ.
    """
    from mcp.shared.exceptions import McpError, ErrorData

    if ctx is None:
        ctx = Context()

    with tracer.start_as_current_span("answer_from_docs") as span:
        span.set_attribute("query", query)
        span.set_attribute("max_context_fragments", max_context_fragments)

        try:
            await ctx.info(f"📘 Отвечаем на вопрос по документации: {query!r}")
            await ctx.report_progress(progress=0, total=100)

            snippets = _search_docs_internal(
                query=query,
                max_results=max_context_fragments,
            )

            if not snippets:
                text = (
                    f"В документации не нашлось ничего по запросу {query!r}. "
                    "MCP-сервер не может ответить на основе документов."
                )
                return ToolResult(
                    content=[TextContent(type="text", text=text)],
                    structured_content={
                        "answer": None,
                        "used_snippets": [],
                        "query": query,
                    },
                )

            # Собираем человекочитаемый текст: покажем фрагменты
            lines: List[str] = [
                "Ниже приведены фрагменты документации, которые можно использовать для ответа на вопрос.",
                f"Вопрос: {query!r}",
                "",
            ]
            lines.extend(
                f"### Фрагмент {idx} (score={s['score']}, file={s['file']}):\n{s['snippet']}\n"
                for idx, s in enumerate(snippets, start=1)
            )
            text = "\n".join(lines)

            await ctx.report_progress(progress=100, total=100)

            # 'answer' здесь — это просто агрегированный текст из документации.
            # Модель-хост может на его основе сформировать нормальный ответ.
            return ToolResult(
                content=[TextContent(type="text", text=text)],
                structured_content={
                    "answer": text,
                    "used_snippets": snippets,
                    "query": query,
                },
            )

        except Exception as e:  # noqa: BLE001
            await ctx.error(f"💥 Ошибка при ответе на основе документации: {e}")
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"Ошибка при ответе на основе документации: {e}",
                )
            ) from e
