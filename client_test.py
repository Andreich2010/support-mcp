import asyncio
import json
import uuid
import httpx

MCP_URL = "http://0.0.0.0:8000/mcp"


def extract_sse_json(raw: str):
    """Вытаскиваем последнее data:{...} из SSE-ответа."""
    last_json = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            candidate = line.removeprefix("data:").strip()
            if candidate:
                last_json = candidate
    return last_json


async def call_tool(name: str, arguments: dict):
    request_id = str(uuid.uuid4())

    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(MCP_URL, headers=headers, json=payload)
        resp.raise_for_status()
        raw = resp.text

    print("\n=== RAW SSE ===")
    print(raw)

    json_text = extract_sse_json(raw)
    if not json_text:
        print("❌ JSON не найден в SSE потоке")
        return

    data = json.loads(json_text)

    print("\n=== PARSED JSON ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    return data


# --- Тестовые вызовы инструмента ---


async def test_all():
    print("\n>>> get_new_tickets")
    await call_tool("get_new_tickets", {"since_minutes": 60})

    print("\n>>> get_ticket_detail")
    await call_tool("get_ticket_detail", {"issue_number": 3})

    print("\n>>> classify_ticket")
    await call_tool("classify_ticket", {"issue_number": 3})

    print("\n>>> request_more_info")
    await call_tool("request_more_info", {"issue_number": 3})

    print("\n>>> analyze_ticket_error")
    await call_tool("analyze_ticket_error", {"issue_number": 3})

    print("\n>>> search_docs")
    await call_tool("search_docs", {"query": "ошибка", "max_results": 3})

    print("\n>>> create_subtasks_from_ticket (dry_run)")
    await call_tool(
        "create_subtasks_from_ticket",
        {"issue_number": 3, "dry_run": True, "max_subtasks": 3},
    )

    print("\n>>> translate_ticket")
    await call_tool(
        "translate_ticket",
        {"issue_number": 3, "target_lang": "en"}
    )

    print("\n>>> generate_support_report")
    await call_tool("generate_support_report", {"period_days": 7})

    print("\n>>> summarize_ticket")
    await call_tool("summarize_ticket", {"issue_number": 3})

    print("\n>>> answer_ticket_question")
    await call_tool("answer_ticket_question", {"issue_number": 3})


if __name__ == "__main__":
    asyncio.run(test_all())
