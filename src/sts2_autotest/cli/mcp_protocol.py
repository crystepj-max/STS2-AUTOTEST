"""MCP (Model Context Protocol) — JSON-RPC 2.0 encoding/decoding.

Implements the MCP wire protocol layer: request decoding, response
encoding, error formatting, and tool definition models. No transport
or I/O -- this module handles data structures only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Stable protocol version used by the cross-agent service. The server keeps
# the old JSON-RPC shape for existing local clients while exposing the newer
# transport/version contract to new clients.
MCP_PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpError(Exception):
    """JSON-RPC 2.0 error object, can be raised or stored in McpResponse."""

    def __init__(
        self, code: int, message: str, data: Any = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclass
class McpRequest:
    """Decoded JSON-RPC 2.0 request."""

    jsonrpc: str
    id: int | str | None
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResponse:
    """JSON-RPC 2.0 response to be encoded."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Any = None
    error: McpError | None = None


@dataclass
class McpTool:
    """MCP tool definition (returned by tools/list)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class McpServerInfo:
    """MCP server identity (returned by initialize)."""

    name: str = "sts2-autotest"
    version: str = "0.1.0"


def decode_request(body: bytes) -> McpRequest:
    """Decode a JSON-RPC 2.0 request from raw bytes.

    Raises McpError on parse or protocol version mismatch.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise McpError(PARSE_ERROR, "Invalid JSON") from exc

    if not isinstance(data, dict):
        raise McpError(INVALID_REQUEST, "Request must be a JSON object")

    jsonrpc = data.get("jsonrpc")
    if not jsonrpc:
        raise McpError(INVALID_REQUEST, "Missing jsonrpc version")
    if jsonrpc != "2.0":
        raise McpError(
            INVALID_REQUEST, f"jsonrpc must be '2.0', got '{jsonrpc}'"
        )

    req_id = data.get("id")

    method = data.get("method", "")
    if not method:
        raise McpError(INVALID_REQUEST, "Missing method")

    params = data.get("params", {})
    if not isinstance(params, dict):
        raise McpError(INVALID_PARAMS, "params must be a JSON object")

    return McpRequest(
        jsonrpc=jsonrpc,
        id=req_id,
        method=method,
        params=params,
    )


def encode_response(response: McpResponse) -> bytes:
    """Encode a JSON-RPC 2.0 response to raw bytes."""
    result: dict[str, Any] = {"jsonrpc": response.jsonrpc, "id": response.id}
    if response.error is not None:
        result["error"] = response.error.to_dict()
    else:
        result["result"] = response.result
    body = json.dumps(result, ensure_ascii=False, indent=2)
    return body.encode("utf-8")


def make_error_response(
    req_id: int | str | None,
    code: int,
    message: str,
    data: Any = None,
) -> McpResponse:
    """Create a standard JSON-RPC 2.0 error response."""
    return McpResponse(
        jsonrpc="2.0",
        id=req_id,
        error=McpError(code=code, message=message, data=data),
    )


def make_initialize_response(
    req_id: int | str | None, server_info: McpServerInfo
) -> McpResponse:
    """Create an MCP initialize response."""
    return McpResponse(
        jsonrpc="2.0",
        id=req_id,
        result={
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {
                "name": server_info.name,
                "version": server_info.version,
            },
            "capabilities": {
                "tools": {},
            },
        },
    )
