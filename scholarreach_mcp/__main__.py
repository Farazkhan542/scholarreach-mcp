"""Run the ScholarReach MCP server over Streamable HTTP: `python -m scholarreach_mcp`.

Environment:
  SCHOLARREACH_API_URL                       ScholarReach backend (default: the hosted one)
  SCHOLARREACH_TOKEN  or  SCHOLARREACH_EMAIL + SCHOLARREACH_PASSWORD
  MCP_AUTH_TOKEN                             if set, clients must send `Authorization: Bearer <it>`
  HOST (default 127.0.0.1), PORT (default 8765)
"""

from __future__ import annotations

import hmac
import os

import uvicorn

from .server import build_server


class BearerAuth:
    """Pure ASGI middleware (doesn't buffer, so SSE streaming keeps working)."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def load_dotenv(path: str = ".env") -> None:
    """Read KEY=value lines from .env without overriding real environment variables."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    app = build_server().streamable_http_app(streamable_http_path="/mcp", host=host)
    if token := os.environ.get("MCP_AUTH_TOKEN"):
        app = BearerAuth(app, token)
    print(f"ScholarReach MCP server on http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
