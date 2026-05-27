"""Health check HTTP server (B17).

Minimal async HTTP server exposing liveness/readness endpoints for
CI/CD orchestration and external monitoring. Uses stdlib asyncio only
-- no additional web framework dependencies.
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
_HTML_500 = b"HTTP/1.0 500 Internal Server Error\r\n"


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


async def _parse_request(reader: asyncio.StreamReader) -> str:
    """Read HTTP request and return the path."""
    raw = b""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
    except asyncio.TimeoutError:
        return ""
    if not raw:
        return ""
    parts = raw.decode("utf-8", errors="replace").strip().split(" ")
    if len(parts) < 2:
        return ""
    return parts[1]  # path


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    check_env: _CheckEnvFn,
) -> None:
    """Handle a single HTTP request."""
    try:
        path = await _parse_request(reader)
        if path in ("/health", "/health/all"):
            response = await _handle_health(check_env)
        elif path == "/health/live":
            response = await _handle_live()
        elif path == "/health/ready":
            response = await _handle_ready(check_env)
        else:
            response = _json_response(_HTML_404, {
                "status": "error",
                "message": f"Not found: {path}",
                "available": ["/health", "/health/live", "/health/ready"],
            })
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


async def run_server(
    host: str = "127.0.0.1",
    port: int = 8766,
    check_env: _CheckEnvFn | None = None,
) -> None:
    """Start the health check HTTP server.

    Args:
        host: Bind address.
        port: Bind port.
        check_env: Function returning check results. Falls back to
            importing _check_env from cli.main when not provided.
    """
    if check_env is None:
        from sts2_autotest.cli.main import _check_env as check_env

    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, check_env),  # type: ignore[arg-type]
        host=host,
        port=port,
    )

    addr = server.sockets[0].getsockname()
    print(f"[autotest] Health server listening on http://{addr[0]}:{addr[1]}")

    async with server:
        await server.serve_forever()


def serve_cmd(args: Any) -> int:
    """CLI entry point: start the health server."""
    host: str = args.host or "127.0.0.1"
    port: int = args.port or 8766
    try:
        asyncio.run(run_server(host=host, port=port))
    except KeyboardInterrupt:
        print("\n[autotest] Health server stopped")
    return 0
