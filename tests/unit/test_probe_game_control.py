"""Tests for scripts/probe_game_control.py — nightly Phase 0 游戏控制面探针。

探针是独立脚本（Phase 0 尚未 pip install），因此以子进程方式驱动并断言
退出码与输出；同时锁定其 CLI 解析与 adapters.discovery.discover_sts2_cli
单源一致（历史上 bash 手抄解析顺序造成的漂移是本脚本要消灭的债）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "scripts" / "probe_game_control.py"

# 端口 1（tcpmux）在本机几乎必然拒绝连接，用于「控制面不可用」场景。
_DEAD_HEALTH_URL = "http://127.0.0.1:1/health"


def _write_stub_cli(root: Path, *, ping_rc: int, ping_out: str = "pong") -> Path:
    stub = root / "fake-sts2"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "0.9.9"; exit 0; fi\n'
        'if [ "$1" = "ping" ]; then\n'
        f'  echo "{ping_out}"; exit {ping_rc};\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run_probe(
    env_extra: dict[str, str], *, isolate_path: bool = False
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("STS2_AGENT_HEALTH_URL", None)
    if isolate_path:
        # 本机 PATH/home 可能装有真实 sts2（真机开发环境）；无 CLI 场景必须
        # 双重隔离，否则 discovery 会经 PATH 或常见 home 路径找到它，
        # 测试就无法覆盖 BLOCKED 分支。
        env["PATH"] = "/usr/bin:/bin"
        env["HOME"] = "/nonexistent-probe-isolation"
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


def test_probe_succeeds_when_cli_ping_ok(tmp_path: Path) -> None:
    stub = _write_stub_cli(tmp_path, ping_rc=0)
    result = _run_probe({"STS2_CLI_PATH": str(stub)})
    assert result.returncode == 0
    assert f"sts2 CLI: {stub}" in result.stdout


def test_probe_falls_back_to_health_when_ping_fails(tmp_path: Path) -> None:
    stub = _write_stub_cli(tmp_path, ping_rc=1)
    result = _run_probe({
        "STS2_CLI_PATH": str(stub),
        "STS2_AGENT_HEALTH_URL": _DEAD_HEALTH_URL,
    })
    # ping 失败且 health 不可达 → 环境 BLOCKED
    assert result.returncode == 1
    assert "均失败" in result.stdout


def test_probe_succeeds_via_health_when_ping_reports_connection_error(tmp_path: Path) -> None:
    """ping rc=0 但输出 CONNECTION_ERROR 视为不可用——历史行为的回归防线。"""
    import http.server
    import socket
    import threading

    stub = _write_stub_cli(tmp_path, ping_rc=0, ping_out="CONNECTION_ERROR: refused")

    class _Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args: object) -> None:
            pass

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = http.server.HTTPServer(("127.0.0.1", port), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _run_probe({
            "STS2_CLI_PATH": str(stub),
            "STS2_AGENT_HEALTH_URL": f"http://127.0.0.1:{port}/health",
        })
    finally:
        server.shutdown()
    assert result.returncode == 0
    assert "Agent health" in result.stdout


def test_probe_fails_when_no_cli_found(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-sts2"
    result = _run_probe({"STS2_CLI_PATH": str(missing)}, isolate_path=True)
    assert result.returncode == 1
    assert "未找到 sts2 CLI" in result.stdout


def test_probe_cli_resolution_matches_discovery(tmp_path: Path) -> None:
    """单源锁定：探针解析结果与 adapters.discovery 在同一环境下完全一致。"""
    stub = _write_stub_cli(tmp_path, ping_rc=0)
    from sts2_autotest.adapters.discovery import discover_sts2_cli

    env = {**os.environ, "STS2_CLI_PATH": str(stub)}
    os.environ["STS2_CLI_PATH"] = str(stub)
    try:
        expected = discover_sts2_cli()
    finally:
        os.environ.pop("STS2_CLI_PATH", None)
    assert expected == str(stub)
    result = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert str(stub) in result.stdout
