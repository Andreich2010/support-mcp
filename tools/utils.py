"""Общие утилиты и типы для MCP-инструментов."""

from typing import Any, Dict, List, Optional

from mcp.types import TextContent
from pydantic import BaseModel


class ToolResult(BaseModel):
    """
    Стандартизированный результат MCP-инструмента.

    content:
        Список блоков текста, который увидит пользователь.
    structured_content:
        Структурированные данные (для агента / дальнейшей обработки).
    meta:
        Метаданные — что угодно полезное (например, время, параметры).
    """

    content: List[TextContent]
    structured_content: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None


def require_env(name: str) -> str:
    """
    Обязательная переменная окружения.

    Поднимает понятную ошибку, если переменная не задана.
    """
    import os

    if value := os.getenv(name):
        return value
    else:
        raise ValueError(f"Обязательная переменная окружения {name} не задана")
