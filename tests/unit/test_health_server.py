"""Tests for the health check HTTP server (B17)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sts2_autotest.cli.health_server import (
    _handle_health,
    _handle_live,
    _handle_ready,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _mock_check_env_ok() -> dict[str, dict[str, str]]:
    return {
        "python": {"status": "OK", "message": "3.14.3"},
        "sts2_cli_mod": {"status": "OK", "message": "sts2.exe"},
        "game_installed": {"status": "OK", "message": "SlayTheSpire2.exe"},
    }


def _mock_check_env_degraded() -> dict[str, dict[str, str]]:
    return {
        "python": {"status": "OK", "message": "3.14.3"},
        "sts2_cli_mod": {"status": "NOT_FOUND", "message": "sts2 CLI not found"},
        "game_installed": {"status": "NOT_FOUND", "message": "Game not found"},
    }


def _parse_response(response: bytes) -> dict[str, Any]:
    _, _, body = response.partition(b"\r\n\r\n")
    return json.loads(body)


class TestHealthEndpoint:
    """_handle_health — full health check."""

    def test_healthy_response(self) -> None:
        response = _run(_handle_health(_mock_check_env_ok))
        data = _parse_response(response)
        assert data["healthy"] is True
        assert data["status"] == "ok"
        assert "python" in data["checks"]

    def test_degraded_response(self) -> None:
        response = _run(_handle_health(_mock_check_env_degraded))
        data = _parse_response(response)
        assert data["healthy"] is False
        assert data["status"] == "degraded"


class TestLiveEndpoint:
    """_handle_live — liveness probe."""

    def test_live_always_returns_ok(self) -> None:
        response = _run(_handle_live())
        data = _parse_response(response)
        assert data["status"] == "ok"
        assert data["service"] == "sts2-autotest-health"


class TestReadyEndpoint:
    """_handle_ready — readiness probe."""

    def test_ready_when_cli_found(self) -> None:
        response = _run(_handle_ready(_mock_check_env_ok))
        data = _parse_response(response)
        assert data["ready"] is True
        assert data["status"] == "ok"

    def test_not_ready_when_cli_missing(self) -> None:
        response = _run(_handle_ready(_mock_check_env_degraded))
        data = _parse_response(response)
        assert data["ready"] is False
        assert data["status"] == "not_ready"
