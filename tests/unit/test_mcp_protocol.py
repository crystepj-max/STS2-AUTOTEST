"""Unit tests for MCP protocol encoding/decoding."""

import json

import pytest

from sts2_autotest.cli.mcp_protocol import (
    McpError,
    McpResponse,
    McpTool,
    decode_request,
    encode_response,
    make_error_response,
)


class TestDecodeRequest:
    def test_decode_valid_tools_list(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }).encode("utf-8")
        req = decode_request(body)
        assert req.jsonrpc == "2.0"
        assert req.id == 1
        assert req.method == "tools/list"

    def test_decode_tools_call(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "run_test", "arguments": {"spec_dir": "tests/"}},
        }).encode("utf-8")
        req = decode_request(body)
        assert req.method == "tools/call"
        assert req.params["name"] == "run_test"
        assert req.params["arguments"]["spec_dir"] == "tests/"

    def test_decode_initialize(self):
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "gawain-ci", "version": "1.0"},
            },
        }).encode("utf-8")
        req = decode_request(body)
        assert req.method == "initialize"

    def test_decode_missing_jsonrpc_raises(self):
        body = json.dumps({"id": 1, "method": "tools/list"}).encode("utf-8")
        with pytest.raises(McpError, match="Missing jsonrpc"):
            decode_request(body)

    def test_decode_wrong_jsonrpc_raises(self):
        body = json.dumps({"jsonrpc": "1.0", "id": 1, "method": "tools/list"}).encode("utf-8")
        with pytest.raises(McpError, match="jsonrpc.*2.0"):
            decode_request(body)

    def test_decode_invalid_json_raises(self):
        with pytest.raises(McpError, match="Invalid JSON"):
            decode_request(b"not json")


class TestEncodeResponse:
    def test_encode_success(self):
        resp = McpResponse(
            jsonrpc="2.0",
            id=1,
            result={"status": "ok"},
        )
        data = encode_response(resp)
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 1
        assert parsed["result"]["status"] == "ok"
        assert "error" not in parsed

    def test_encode_error(self):
        resp = McpResponse(
            jsonrpc="2.0",
            id=1,
            error=McpError(code=-32600, message="Invalid Request"),
        )
        data = encode_response(resp)
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["error"]["code"] == -32600
        assert "result" not in parsed


class TestMakeErrorResponse:
    def test_invalid_request_error(self):
        resp = make_error_response(None, -32600, "Invalid Request")
        assert resp.jsonrpc == "2.0"
        assert resp.id is None
        assert resp.error.code == -32600

    def test_method_not_found(self):
        resp = make_error_response(5, -32601, "Method not found: unknown/method")
        assert resp.id == 5
        assert resp.error.code == -32601


class TestMcpTool:
    def test_tool_definition(self):
        tool = McpTool(
            name="health_check",
            description="Check service health",
            input_schema={
                "type": "object",
                "properties": {},
            },
        )
        d = tool.to_dict()
        assert d["name"] == "health_check"
        assert "description" in d
        assert d["inputSchema"]["type"] == "object"
