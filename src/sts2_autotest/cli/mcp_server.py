"""MCP Server -- entry point for the STS2-AUTOTEST MCP service (B11 Phase 2).

Extends _HttpServer from health_server.py. Runs as a launchd daemon on
macOS, exposing 7 MCP tools to MOD project CI pipelines.

Usage:
    python -m sts2_autotest.cli.mcp_server --host 127.0.0.1 --port 8090
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, cast

from sts2_autotest.cli.health_server import (
    _HttpServer,
    _json_response,
    _HTML_200,
    _HTML_404,
    _HTML_405,
    _HTML_500,
)
from sts2_autotest.cli.mcp_protocol import (
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    McpError,
    McpResponse,
    McpServerInfo,
    decode_request,
    encode_response,
    make_error_response,
    make_initialize_response,
)
from sts2_autotest.cli.mcp_tools import ToolRegistry

# 401 status line (not exported from health_server)
_HTML_401 = b"HTTP/1.0 401 Unauthorized\r\n"


class McpServer(_HttpServer):
    """MCP (Model Context Protocol) test service.

    Routes:
      GET  /health      -> health check (backward compat)
      POST /mcp          -> MCP JSON-RPC 2.0 endpoint
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8090):
        super().__init__(host=host, port=port)
        self._registry = ToolRegistry()

    def _check_token(self, headers: dict[str, str]) -> bool:
        """Validate X-MCP-Token if STS2_MCP_TOKEN is configured."""
        expected = os.environ.get("STS2_MCP_TOKEN", "")
        if not expected:
            return True  # No token configured -> allow all
        provided = headers.get("x-mcp-token", "")
        return provided == expected

    def _route_http(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> bytes:
        """HTTP routing with MCP dispatch."""
        # Health endpoints (backward compatible)
        if path in ("/health", "/health/live"):
            return _json_response(_HTML_200, {
                "status": "ok",
                "service": "sts2-autotest-mcp",
            })

        # MCP endpoint
        if path == "/mcp":
            if method != "POST":
                return _json_response(_HTML_405, {
                    "status": "error",
                    "message": "MCP endpoint requires POST",
                })
            if not self._check_token(headers):
                return _json_response(_HTML_401, {
                    "status": "error",
                    "message": "Unauthorized: invalid or missing X-MCP-Token",
                })
            mcp_result = self._handle_mcp(body or b"{}")
            http_status = _HTML_500 if "error" in mcp_result else _HTML_200
            return _json_response(http_status, mcp_result)

        # Fallback
        return _json_response(_HTML_404, {
            "status": "error",
            "message": f"Not found: {path}",
            "available": ["/health", "/mcp"],
        })

    def _handle_mcp(self, body: bytes) -> dict[str, Any]:
        """Process an MCP JSON-RPC 2.0 request and return parsed result."""
        try:
            req = decode_request(body)
        except McpError as exc:
            err_resp = make_error_response(None, exc.code, exc.message, exc.data)
            return cast(dict[str, Any], json.loads(encode_response(err_resp)))

        method = req.method
        try:
            if method == "initialize":
                resp = make_initialize_response(req.id, McpServerInfo())
            elif method == "tools/list":
                tools = [t.to_dict() for t in self._registry.list_tools()]
                resp = McpResponse(jsonrpc="2.0", id=req.id, result={"tools": tools})
            elif method == "tools/call":
                tool_name = req.params.get("name", "")
                tool_args = req.params.get("arguments", {})
                result = self._registry.dispatch(tool_name, tool_args)
                resp = McpResponse(
                    jsonrpc="2.0",
                    id=req.id,
                    result={
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                        "structuredContent": result,
                    },
                )
            else:
                resp = make_error_response(req.id, METHOD_NOT_FOUND, f"Method not found: {method}")
        except McpError as exc:
            resp = make_error_response(req.id, exc.code, exc.message, exc.data)
        except Exception as exc:
            resp = make_error_response(req.id, INTERNAL_ERROR, f"Internal error: {exc}")

        return cast(dict[str, Any], json.loads(encode_response(resp)))

    # -- Public HTTP interface used by _HttpServer --

    async def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> bytes:
        return self._route_http(method, path, headers, body)


async def serve_mcp(host: str = "127.0.0.1", port: int = 8090) -> None:
    """Start the MCP server and block."""
    server = McpServer(host=host, port=port)
    await server.start()


def serve_cmd(args: Any) -> int:
    """CLI entry point: start the MCP server."""
    host: str = getattr(args, "host", None) or "127.0.0.1"
    port: int = getattr(args, "port", None) or 8090

    import asyncio

    try:
        asyncio.run(serve_mcp(host=host, port=port))
    except KeyboardInterrupt:
        print("\n[autotest] MCP server stopped")
    return 0


# -- Main (for launchd / direct run) --

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="STS2-AUTOTEST MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8090, help="Bind port")
    args = parser.parse_args()

    sys.exit(serve_cmd(args))
