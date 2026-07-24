#!/usr/bin/env python3
"""P2-1 三角色短目标验收驱动：证明平台对任意角色无隐藏默认规则。

三个角色（两个原游戏角色 + 一个 Mod 角色）经同一公共 MCP 入口完成
「新局到稳定地图」短目标，平台代码与规则全程不修改，只更换任务输入的
``character_id``。每个角色独立幂等键，并实测相同键重复提交返回同一 run_id。

用法：
    python scripts/p2_character_short_goals.py                # 三角色完整验收
    python scripts/p2_character_short_goals.py --characters IRONCLAD SILENT

退出码：0 = 全部角色 PASSED 且幂等成立；1 = 任一失败或环境前置不满足。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {
    "PASSED", "FAILED_PRODUCT", "FAILED_PLATFORM", "BLOCKED_ENVIRONMENT", "CANCELLED",
}


def log(msg: str) -> None:
    print(f"[p2 {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def save_raw(raw_dir: Path, name: str, payload: Any) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class McpClient:
    """公共 MCP 六操作客户端（JSON-RPC tools/call over HTTP，纯 urllib）。"""

    def __init__(self, url: str) -> None:
        self.url = url
        self._mid = 0

    def call(self, name: str, args: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        self._mid += 1
        body = json.dumps({
            "jsonrpc": "2.0", "id": self._mid,
            "method": "tools/call", "params": {"name": name, "arguments": args},
        }).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(f"MCP {name} error: {data['error']}")
        result = data.get("result") or {}
        content = result.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except ValueError:
                    return {"text": text}
        return result


def http_json(url: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def preflight(mcp_url: str, game_url: str) -> list[str]:
    problems: list[str] = []
    health = http_json(mcp_url.rsplit("/mcp", 1)[0] + "/health")
    if not health or health.get("status") != "ok":
        problems.append("公共任务服务 8090 不在线或健康检查失败")
    game_health = http_json(game_url + "/health")
    if not game_health or game_health.get("data", {}).get("status") != "ready":
        problems.append("游戏控制入口 8080 未就绪（health 非 ready）")
    return problems


def wait_terminal(
    client: McpClient, run_id: str, label: str, *, timeout: float = 900.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.call("get_run", {"run_id": run_id})
        status = str(last.get("status", ""))
        phase = last.get("phase", "")
        screen = (last.get("progress") or {}).get("current_screen", "")
        log(f"{label}: status={status} phase={phase} screen={screen}")
        if status in TERMINAL_STATUSES:
            return last
        time.sleep(10)
    raise TimeoutError(f"{label} 未在 {timeout}s 内到达终态（最后状态 {last.get('status')}）")


def caffeinate_running() -> bool:
    out = subprocess.run(
        ["pgrep", "-fl", "caffeinate -dimsu"], capture_output=True, text=True
    ).stdout.strip()
    return bool(out)


def run_one_character(
    client: McpClient,
    character: str,
    stamp: str,
    out_dir: Path,
    run_timeout: int,
) -> dict[str, Any]:
    """单角色短目标：提交 → 幂等复验 → 等终态 → 取报告。返回该角色结论。"""
    raw_dir = out_dir / "raw"
    key = f"p2-short-{character.lower()}-{stamp}"
    result: dict[str, Any] = {"character": character, "idempotency_key": key}

    submit1 = client.call("submit_run", {
        "journey": "new_run", "idempotency_key": key,
        "timeout": run_timeout, "evidence": "full",
        "character_id": character,
    })
    save_raw(raw_dir, f"{character}-01-submit.json", submit1)
    run_id = submit1["run_id"]
    result["run_id"] = run_id
    log(f"{character} 提交: {run_id}")

    submit2 = client.call("submit_run", {
        "journey": "new_run", "idempotency_key": key,
        "timeout": run_timeout, "evidence": "full",
        "character_id": character,
    })
    save_raw(raw_dir, f"{character}-02-submit-idempotent.json", submit2)
    result["idempotent_same_run"] = (
        submit2.get("run_id") == run_id
        and submit2.get("created_at") == submit1.get("created_at")
    )
    log(f"{character} 幂等复验: same_run={result['idempotent_same_run']}")

    terminal = wait_terminal(client, run_id, character, timeout=run_timeout + 300)
    save_raw(raw_dir, f"{character}-03-terminal.json", terminal)
    result["terminal_status"] = terminal.get("status")
    log(f"{character} 终态: {result['terminal_status']}")

    report = client.call("get_report", {"run_id": run_id})
    save_raw(raw_dir, f"{character}-04-report.json", report)
    result["report_status"] = (report.get("report") or report).get("status")
    result["artifact"] = (report.get("report") or report).get("artifact_zip")
    return result


def compute_verdict(out_dir: Path) -> dict[str, Any]:
    """从 raw/ 原始响应重算结论，禁止写死期望值。"""
    raw_dir = out_dir / "raw"
    results: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("*-03-terminal.json")):
        character = path.name[: -len("-03-terminal.json")]
        terminal = json.loads(path.read_text(encoding="utf-8"))
        submit1 = json.loads((raw_dir / f"{character}-01-submit.json").read_text(encoding="utf-8"))
        submit2 = json.loads((raw_dir / f"{character}-02-submit-idempotent.json").read_text(encoding="utf-8"))
        results.append({
            "character": character,
            "run_id": terminal.get("run_id"),
            "terminal_status": terminal.get("status"),
            "passed": terminal.get("status") == "PASSED",
            "idempotent_same_run": (
                submit2.get("run_id") == submit1.get("run_id")
                and submit2.get("created_at") == submit1.get("created_at")
            ),
        })
    return {
        "P2_SHORT_GOALS_PASS": bool(results) and all(
            r["passed"] and r["idempotent_same_run"] for r in results
        ),
        "characters": results,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8090/mcp")
    parser.add_argument("--game-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--characters", nargs="+",
        default=["IRONCLAD", "SILENT", "GAWAINMOD-GAWAIN"],
        help="角色标识列表（两个原游戏角色 + 一个 Mod 角色）",
    )
    parser.add_argument(
        "--out-dir",
        default=f"tests/output/cross-agent-p2/short-goals-{datetime.now():%Y%m%d-%H%M%S}",
    )
    parser.add_argument("--run-timeout", type=int, default=600)
    parser.add_argument("--verdict-only", action="store_true",
                        help="只从 --out-dir 的 raw/ 重算结论，不跑真实验收")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if args.verdict_only:
        verdict = compute_verdict(out_dir)
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["P2_SHORT_GOALS_PASS"] else 1

    problems = preflight(args.mcp_url, args.game_url)
    if problems:
        for p in problems:
            log(f"前置失败: {p}")
        return 1
    if caffeinate_running():
        log("前置失败: 验收开始前已存在防睡眠进程残留")
        return 1

    client = McpClient(args.mcp_url)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    caps = client.call("capabilities", {})
    save_raw(out_dir / "raw", "00-capabilities.json", caps)
    log(f"capabilities operations={caps.get('operations')}")

    results: list[dict[str, Any]] = []
    try:
        for character in args.characters:
            results.append(run_one_character(
                client, character, stamp, out_dir, args.run_timeout,
            ))
    except Exception as exc:  # noqa: BLE001 — 如实记录失败现场
        log(f"验收中断: {exc}")
    finally:
        save_raw(out_dir / "raw", "99-characters.json", results)

    verdict = compute_verdict(out_dir)
    save_raw(out_dir, "verdict.json", verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["P2_SHORT_GOALS_PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
