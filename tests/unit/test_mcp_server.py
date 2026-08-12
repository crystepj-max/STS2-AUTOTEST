"""Unit tests for MCP server routing and lifecycle."""

import json
import os

from sts2_autotest.cli.mcp_protocol import MCP_PROTOCOL_VERSION
from sts2_autotest.cli.mcp_server import McpServer


def _extract_body(http_bytes: bytes) -> dict:
    """Extract JSON body from HTTP response bytes."""
    text = http_bytes.decode("utf-8")
    # Split on \r\n\r\n to separate headers from body
    parts = text.split("\r\n\r\n", 1)
    body_text = parts[1] if len(parts) > 1 else text
    return json.loads(body_text)


class TestMcpServerRouting:
    def setup_method(self):
        self.server = McpServer(host="127.0.0.1", port=9999)

    def test_health_endpoint_returns_ok(self):
        response = self.server._route_http("GET", "/health", {}, None)
        # Response is HTTP-wrapped; extract JSON body after headers
        _, _, body = response.partition(b"\r\n\r\n")
        parsed = json.loads(body)
        assert parsed["status"] == "ok"

    def test_mcp_endpoint_rejects_non_post(self):
        response = self.server._route_http("GET", "/mcp", {}, None)
        assert b"405" in response

    def test_mcp_endpoint_initialize(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }).encode("utf-8")
        response = self.server._route_http("POST", "/mcp", {}, body)
        parsed = _extract_body(response)
        assert parsed["result"]["serverInfo"]["name"] == "sts2-autotest"
        assert parsed["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION

    def test_mcp_endpoint_tools_list(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        }).encode("utf-8")
        response = self.server._route_http("POST", "/mcp", {}, body)
        parsed = _extract_body(response)
        tools = parsed["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert "health_check" in tool_names
        assert "review_spec" in tool_names
        assert "run_pipeline" in tool_names

    def test_mcp_endpoint_tools_call_health(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        }).encode("utf-8")
        response = self.server._route_http("POST", "/mcp", {}, body)
        parsed = _extract_body(response)
        assert parsed["result"]["structuredContent"]["status"] == "ok"

    def test_mcp_endpoint_invalid_json_returns_error(self):
        response = self.server._route_http("POST", "/mcp", {}, b"not json")
        parsed = _extract_body(response)
        assert "error" in parsed
        assert parsed["error"]["code"] == -32700

    def test_mcp_endpoint_unknown_method(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "unknown/method",
        }).encode("utf-8")
        response = self.server._route_http("POST", "/mcp", {}, body)
        parsed = _extract_body(response)
        assert parsed["error"]["code"] == -32601


class TestTokenAuth:
    def test_no_token_required_when_not_configured(self):
        server = McpServer(host="127.0.0.1", port=9999)
        result = server._check_token({})
        assert result is True

    def test_valid_token_passes(self, monkeypatch):
        monkeypatch.setenv("STS2_MCP_TOKEN", "secret123")
        server = McpServer(host="127.0.0.1", port=9999)
        result = server._check_token({"x-mcp-token": "secret123"})
        assert result is True

    def test_invalid_token_rejected(self, monkeypatch):
        monkeypatch.setenv("STS2_MCP_TOKEN", "secret123")
        server = McpServer(host="127.0.0.1", port=9999)
        result = server._check_token({"x-mcp-token": "wrong"})
        assert result is False

    def test_missing_token_rejected(self, monkeypatch):
        monkeypatch.setenv("STS2_MCP_TOKEN", "secret123")
        server = McpServer(host="127.0.0.1", port=9999)
        result = server._check_token({})
        assert result is False

    def test_mcp_unauthorized_returns_401(self):
        os.environ["STS2_MCP_TOKEN"] = "secret123"
        try:
            server = McpServer(host="127.0.0.1", port=9999)
            body = json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list"}).encode()
            response = server._route_http("POST", "/mcp", {"x-mcp-token": "wrong"}, body)
            assert b"401" in response
        finally:
            del os.environ["STS2_MCP_TOKEN"]
