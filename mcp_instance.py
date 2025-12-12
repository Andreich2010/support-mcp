# mcp_instance.py
import os
from mcp.server.fastmcp import FastMCP

PORT = int(os.getenv("PORT", "8080"))

# Официальный FastMCP из пакета mcp
mcp = FastMCP(
    name="support-mcp",
    host="0.0.0.0",      # важно для контейнера
    port=PORT,
    stateless_http=True  # рекомендуемый режим для облака
)
