"""跨 Agent 公共入口契约验收。

这些测试不启动真实游戏，而是验证所有客户端共用的提交、查询和取报告协议。
真实游戏链路另由 requires_game / smoke / regression 测试负责。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sts2_autotest.cli.mcp_server import McpServer
from sts2_autotest.core.run_service import RunStore


def _body(response: bytes) -> dict[str, Any]:
    _, _, raw = response.partition(b"\r\n\r\n")
    return json.loads(raw)


def _call_tool(server: McpServer, request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    response = server._route_http(
        "POST",
        "/mcp",
        {},
        json.dumps(request, ensure_ascii=False).encode("utf-8"),
    )
    parsed = _body(response)
    assert "error" not in parsed
    return parsed["result"]["structuredContent"]


def test_mcp_submit_query_report_contract_is_client_neutral(monkeypatch, tmp_path: Path) -> None:
    """MCP 客户端只依赖稳定 run_id，不依赖 worker 是否仍由当前进程托管。"""
    run_root = tmp_path / "runs"
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("STS2_AUTOTEST_RUN_ROOT", str(run_root))

    server = McpServer(host="127.0.0.1", port=0)
    with patch("sts2_autotest.cli.mcp_tools.spawn_worker") as worker:
        first = _call_tool(
            server,
            1,
            "submit_run",
            {
                "project": "examplemod",
                "suite": "smoke",
                "timeout": 60,
                "evidence": "full",
                "idempotency_key": "examplemod-smoke-contract-1",
            },
        )
        second = _call_tool(
            server,
            2,
            "submit_run",
            {
                "project": "examplemod",
                "suite": "smoke",
                "timeout": 60,
                "evidence": "full",
                "idempotency_key": "examplemod-smoke-contract-1",
            },
        )

    assert first["run_id"] == second["run_id"]
    assert first["status"] == "QUEUED"
    assert "--suite" in first["request"]["argv"]
    assert "--all" not in first["request"]["argv"]
    worker.assert_called_once()

    run_id = first["run_id"]
    queried = _call_tool(server, 3, "get_run", {"run_id": run_id})
    assert queried["run_id"] == run_id
    assert queried["request"]["suite"] == "smoke"

    report_dir = evidence_root / run_id
    (report_dir / "reports").mkdir(parents=True)
    (report_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "PASSED",
                "passed": 1,
                "failed": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = RunStore(run_root)
    store.update(
        run_id,
        status="PASSED",
        phase="COMPLETED",
        evidence_dir=str(report_dir),
        result={"status": "PASSED"},
    )

    final = _call_tool(server, 4, "get_run", {"run_id": run_id})
    report = _call_tool(server, 5, "get_report", {"run_id": run_id})
    assert final["status"] == "PASSED"
    assert report["summary"]["run_id"] == run_id
    assert report["summary"]["status"] == "PASSED"


def test_cli_capabilities_is_machine_readable() -> None:
    """不支持 MCP 的 Agent 也能通过 CLI 发现同一份状态契约。"""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "sts2_autotest.cli.main", "capabilities", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == "1"
    assert payload["operations"] == [
        "submit_run",
        "get_run",
        "cancel_run",
        "resume_run",
        "get_report",
    ]
