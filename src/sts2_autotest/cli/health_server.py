"""Health check HTTP server (B17) + reusable async HTTP server base class.

Minimal async HTTP server exposing liveness/readiness endpoints for
CI/CD orchestration and external monitoring. Uses stdlib asyncio only
-- no additional web framework dependencies.

The _HttpServer base class is shared with mcp_server.py (B11 Phase 2).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

_CheckEnvFn = Callable[[], dict[str, dict[str, str]]]

_RESPONSE_HEADERS: tuple[bytes, ...] = (
    b"content-type: application/json",
    b"access-control-allow-origin: *",
    b"connection: close",
)

_HTML_200 = b"HTTP/1.0 200 OK\r\n"
_HTML_503 = b"HTTP/1.0 503 Service Unavailable\r\n"
_HTML_404 = b"HTTP/1.0 404 Not Found\r\n"
_HTML_405 = b"HTTP/1.0 405 Method Not Allowed\r\n"
_HTML_500 = b"HTTP/1.0 500 Internal Server Error\r\n"


# ── Shared transport utilities ──

def _json_response(http_status: bytes, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    headers = b"\r\n".join(_RESPONSE_HEADERS)
    return b"".join([
        http_status,
        headers,
        b"\r\ncontent-length: ",
        str(len(body)).encode("ascii"),
        b"\r\n\r\n",
        body,
    ])


async def _parse_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes | None]:
    """Read HTTP request, return (method, path, headers_dict, body_bytes_or_None).

    Raises ValueError if request is malformed.
    """
    raw = b""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
    except TimeoutError:
        raise ValueError("Request timeout")

    if not raw:
        raise ValueError("Empty request")

    request_line = raw.decode("utf-8", errors="replace").strip()
    parts = request_line.split(" ")
    if len(parts) < 2:
        raise ValueError(f"Malformed request line: {request_line}")

    method = parts[0].upper()
    path = parts[1]

    # Read headers
    headers: dict[str, str] = {}
    content_length = 0
    while True:
        line = (await reader.readline()).decode("utf-8", errors="replace").strip()
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
            if key.strip().lower() == "content-length":
                content_length = int(value.strip())

    # Read body if present
    body: bytes | None = None
    if content_length > 0:
        body = await reader.readexactly(content_length)

    return method, path, headers, body


class _HttpServer:
    """Minimal async HTTP server base class.

    Subclasses override handle_request() to implement routing.
    Uses stdlib asyncio only -- no web framework.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8766):
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None

    async def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> bytes:
        """Override in subclasses to implement routing logic."""
        return _json_response(_HTML_404, {
            "status": "error",
            "message": f"Not found: {path}",
        })

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single TCP connection."""
        try:
            method, path, headers, body = await _parse_http_request(reader)
            response = await self.handle_request(method, path, headers, body)
        except ValueError:
            response = _json_response(_HTML_500, {
                "status": "error",
                "message": "Bad request",
            })
        except Exception:
            response = _json_response(_HTML_500, {
                "status": "error",
                "message": "Internal server error",
            })
        try:
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def start(self) -> None:
        """Start the server and block until stopped."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        addr = self._server.sockets[0].getsockname()
        print(f"[autotest] Server listening on http://{addr[0]}:{addr[1]}")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the server gracefully."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


# ── Health server (existing B17 functionality) ──

async def _handle_health(
    check_env: _CheckEnvFn,
) -> bytes:
    """Full health check -- runs all diagnostics."""
    try:
        checks = check_env()
    except Exception as exc:
        return _json_response(_HTML_500, {
            "status": "error",
            "message": f"Health check failed: {exc}",
        })

    all_ok = all(
        entry["status"] == "OK" for entry in checks.values()
    )
    return _json_response(
        _HTML_200 if all_ok else _HTML_503,
        {
            "status": "ok" if all_ok else "degraded",
            "healthy": all_ok,
            "checks": checks,
        },
    )


async def _handle_live() -> bytes:
    """Liveness probe -- always returns 200 if the server is running."""
    return _json_response(_HTML_200, {
        "status": "ok",
        "service": "sts2-autotest-health",
    })


async def _handle_ready(
    check_env: _CheckEnvFn,
) -> bytes:
    """Readiness probe -- quick check that core services are available."""
    try:
        checks = check_env()
    except Exception as exc:
        return _json_response(_HTML_503, {
            "status": "not_ready",
            "message": f"Readiness check failed: {exc}",
        })

    cli_found = checks.get("sts2_cli_mod", {}).get("status") == "OK"
    cli_ok = checks.get("sts2_cli_version", {}).get("status") == "OK"
    ready = cli_found and cli_ok
    return _json_response(
        _HTML_200 if ready else _HTML_503,
        {
            "status": "ok" if ready else "not_ready",
            "ready": ready,
            "checks": {
                "sts2_cli_mod": checks.get("sts2_cli_mod", {}),
                "sts2_cli_version": checks.get("sts2_cli_version", {}),
            },
        },
    )


class HealthServer(_HttpServer):
    """Health check HTTP server (B17), built on _HttpServer base."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8766,
        check_env: _CheckEnvFn | None = None,
    ):
        super().__init__(host=host, port=port)
        if check_env is None:
            from sts2_autotest.cli.main import _check_env as check_env
        self._check_env = check_env

    async def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> bytes:
        # _check_env is guaranteed non-None after __init__
        check_env: _CheckEnvFn = self._check_env  # type: ignore[assignment]
        if path in ("/health", "/health/all"):
            return await _handle_health(check_env)
        elif path == "/health/live":
            return await _handle_live()
        elif path == "/health/ready":
            return await _handle_ready(check_env)
        return await super().handle_request(method, path, headers, body)


async def run_server(
    host: str = "127.0.0.1",
    port: int = 8766,
    check_env: _CheckEnvFn | None = None,
) -> None:
    """Start the health check HTTP server. (Backward-compatible wrapper.)"""
    if check_env is None:
        from sts2_autotest.cli.main import _check_env as check_env
    server = HealthServer(host=host, port=port, check_env=check_env)
    await server.start()


def serve_cmd(args: Any) -> int:
    """CLI entry point: start the health server."""
    host: str = args.host or "127.0.0.1"
    port: int = args.port or 8766
    try:
        asyncio.run(run_server(host=host, port=port))
    except KeyboardInterrupt:
        print("\n[autotest] Health server stopped")
    return 0
