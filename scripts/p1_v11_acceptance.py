#!/usr/bin/env python3
"""P1 V11 跨 Agent 验收驱动：通过公共 MCP 六操作完成三柱真实验收。

与 V10 驱动（/tmp/v10_acceptance.py，一次性脚本）的差异——本驱动是项目内
可复用资产，并修复 V10 复核（docs/handoff/2026-07-19-p1-v10-review-handoff.md）
指出的全部验收缺口：

- get_report 对三条任务都真实调用（不再只在本地缺包时兜底）；
- ORIG 使用相同幂等键重复提交，验证返回同一 run_id 且不建第二个任务；
- capabilities 真实核对六个公开操作清单；
- 判定全部从 raw/ 下保存的真实公共响应与证据包计算，禁止写死期望值；
- 干净主菜单不只看平台布尔值：核对恢复后完整状态（final_state）与证据包
  内恢复后最终截图，以及 SECOND 旅程首帧主菜单不含旧局入口（不继承残局）。

用法：
    python scripts/p1_v11_acceptance.py                     # 完整真实验收
    python scripts/p1_v11_acceptance.py --verdict-only      # 从已有 raw/ 重算判定

退出码：0 = V11_PASS true；1 = 任一检查失败或环境前置不满足。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIX_OPERATIONS = [
    "capabilities", "submit_run", "get_run", "cancel_run", "resume_run", "get_report",
]
TERMINAL_STATUSES = {
    "PASSED", "FAILED_PRODUCT", "FAILED_PLATFORM", "BLOCKED_ENVIRONMENT", "CANCELLED",
}
# 局内证明：进入这些页面即视为「已真实开局、存档已创建」可执行取消。
# CHARACTER_SELECT 计入：此时存档已创建（V11 实测取消后恢复确认有旧局可放弃），
# 且它是旅程稳定停靠点——等到 EVENT 再取消会与旅程完成（PASSED）竞争（V11c 实测
# SECOND 在取消生效前 PASSED）。取消前画面如实记录在 pre_cancel_screen。
NON_MENU_SCREENS = {
    "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT", "REST", "SHOP", "CHEST",
    "CARD_REWARD", "NEXT_ACT", "GAME_OVER", "BUNDLE_SELECTION",
}


def log(msg: str) -> None:
    print(f"[v11 {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def save_raw(raw_dir: Path, name: str, payload: Any) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_raw(raw_dir: Path, name: str) -> Any | None:
    path = raw_dir / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
        # MCP 内容块协议：文本块内嵌 JSON
        content = result.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except ValueError:
                    return {"text": text}
        return result


def caffeinate_running() -> bool:
    out = subprocess.run(
        ["pgrep", "-fl", "caffeinate -dimsu"], capture_output=True, text=True
    ).stdout.strip()
    return bool(out)


def http_json(url: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def preflight(mcp_url: str, game_url: str) -> list[str]:
    """真实验收前置（交接第六节第 8 步）。返回问题列表，空列表 = 通过。"""
    problems: list[str] = []
    health = http_json(mcp_url.rsplit("/mcp", 1)[0] + "/health")
    if not health or health.get("status") != "ok":
        problems.append("公共任务服务 8090 不在线或健康检查失败")
    game_health = http_json(game_url + "/health")
    if not game_health or game_health.get("data", {}).get("status") != "ready":
        problems.append("游戏控制入口 8080 未就绪（health 非 ready）")
    game_state = http_json(game_url + "/state")
    if game_state is None:
        problems.append("游戏状态不可读取（8080 /state 无响应）")
    if caffeinate_running():
        problems.append("验收开始前已存在防睡眠进程残留")
    return problems


def wait_in_run(
    client: McpClient, run_id: str, label: str, *, timeout: float = 600.0
) -> tuple[dict[str, Any], bool, bool]:
    """轮询直到任务真实进入局内。返回（最后一次 get_run 响应, 是否局内, 期间防睡眠存在）。"""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    caf_seen = False
    while time.monotonic() < deadline:
        if caffeinate_running():
            caf_seen = True
        try:
            last = client.call("get_run", {"run_id": run_id})
        except Exception as exc:  # noqa: BLE001
            log(f"  {label}: get_run 异常（继续等待）: {exc}")
            time.sleep(5.0)
            continue
        status = last.get("status")
        screen = str(last.get("current_screen") or "").upper()
        if status in TERMINAL_STATUSES:
            log(f"  {label}: 未入局部进入终态 {status}")
            return last, False, caf_seen
        if status == "RUNNING" and screen in NON_MENU_SCREENS:
            return last, True, caf_seen
        time.sleep(5.0)
    return last, False, caf_seen


def wait_terminal(
    client: McpClient, run_id: str, label: str, *, timeout: float = 1200.0
) -> tuple[dict[str, Any], bool]:
    """轮询直到终态。返回（终态 get_run 响应, 期间防睡眠存在）。"""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    caf_seen = False
    while time.monotonic() < deadline:
        if caffeinate_running():
            caf_seen = True
        try:
            last = client.call("get_run", {"run_id": run_id})
        except Exception as exc:  # noqa: BLE001
            log(f"  {label}: get_run 异常（继续等待）: {exc}")
            time.sleep(5.0)
            continue
        if last.get("status") in TERMINAL_STATUSES:
            return last, caf_seen
        time.sleep(5.0)
    log(f"  {label}: 等待终态超时（最后状态 {last.get('status')}）")
    return last, caf_seen


def _recovery_of(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    run = report.get("run") or {}
    result = run.get("result") or {}
    recovery = result.get("recovery") or {}
    return recovery if isinstance(recovery, dict) else {}


def _final_state_of(report: dict[str, Any] | None) -> dict[str, Any]:
    recovery = _recovery_of(report)
    final_state = recovery.get("final_state") or {}
    return final_state if isinstance(final_state, dict) else {}


def _zip_members(report: dict[str, Any] | None) -> list[str]:
    """从 get_report 响应定位证据包并列出成员（本地读取仅用于交叉核对）。"""
    if not isinstance(report, dict):
        return []
    path = report.get("evidence_pack_url") or (report.get("artifact_status") or {}).get("path")
    if not path or not Path(str(path)).is_file():
        return []
    try:
        with zipfile.ZipFile(str(path)) as archive:
            return archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return []


def _zip_read_json(report: dict[str, Any] | None, member: str) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    path = report.get("evidence_pack_url") or (report.get("artifact_status") or {}).get("path")
    if not path or not Path(str(path)).is_file():
        return None
    try:
        with zipfile.ZipFile(str(path)) as archive:
            return json.loads(archive.read(member).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """从 JPEG 字节流解析 (width, height)（SOF 标记，纯 stdlib、跨平台）。

    无法解析（非 JPEG、截断、无 SOF、到达 SOS 前未找到）一律返回 None——
    调用方必须据此判失败，禁止把「读不出尺寸」当成通过。
    """
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    offset = 2
    while offset + 3 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker == 0xDA:  # SOS：其后为压缩数据，头部已无 SOF 可找
            return None
        if marker in (0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if offset + 4 > len(data):
            return None
        seg_len = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if seg_len < 2:
            return None
        if marker in (
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        ):
            if offset + 9 > len(data):
                return None
            height = int.from_bytes(data[offset + 5 : offset + 7], "big")
            width = int.from_bytes(data[offset + 7 : offset + 9], "big")
            return (width, height)
        offset += 2 + seg_len
    return None


def _verify_final_screenshot(report: dict[str, Any] | None) -> dict[str, Any]:
    """机器校验恢复后最终截图内容：存在、体积非黑屏级、尺寸可读且为真实窗口。

    失败即判失败：截图缺失、体积 <50KB（黑屏仅数 KB）、JPEG 无法解析尺寸、
    或尺寸 <1280×720，全部判定不通过——禁止「读不出尺寸」假通过（V11 复核）。
    像素级 RGB 校验由平台采集时完成（黑屏/纯色不会通过校验入包），此处为
    独立复核。
    """
    result: dict[str, Any] = {"ok": False, "reason": None, "size_kb": None, "dimensions": None}
    if not isinstance(report, dict):
        result["reason"] = "no report"
        return result
    path = report.get("evidence_pack_url") or (report.get("artifact_status") or {}).get("path")
    if not path or not Path(str(path)).is_file():
        result["reason"] = "artifact missing"
        return result
    try:
        with zipfile.ZipFile(str(path)) as archive:
            members = [n for n in archive.namelist() if "recovery_final" in n]
            if not members:
                result["reason"] = "recovery_final screenshot missing"
                return result
            data = archive.read(members[0])
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        result["reason"] = f"cannot read screenshot: {exc}"
        return result
    size_kb = len(data) / 1024
    result["size_kb"] = round(size_kb, 1)
    if size_kb < 50:
        result["reason"] = f"screenshot too small ({size_kb:.0f}KB), likely blank/black"
        return result
    dims = _jpeg_dimensions(data)
    if dims is None:
        result["reason"] = "cannot parse JPEG dimensions (invalid or unsupported image)"
        return result
    width, height = dims
    result["dimensions"] = f"{width}x{height}"
    if width < 1280 or height < 720:
        result["reason"] = f"unexpected dimensions {width}x{height} (< 1280x720)"
        return result
    result["ok"] = True
    return result


def _cancel_column_checks(
    prefix: str,
    terminal: dict[str, Any] | None,
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    """取消柱（ORIG/SECOND 共用）的逐条判定——全部从真实响应计算。"""
    checks: dict[str, Any] = {}
    recovery = _recovery_of(report)
    final_state = _final_state_of(report)
    members = _zip_members(report)

    checks[f"{prefix}_terminal_cancelled"] = (
        isinstance(terminal, dict) and terminal.get("status") == "CANCELLED"
    )
    checks[f"{prefix}_report_consistent"] = (
        isinstance(report, dict)
        and isinstance(terminal, dict)
        and (report.get("run") or {}).get("status") == terminal.get("status")
    )
    checks[f"{prefix}_restart_count_is_one"] = recovery.get("restart_count") == 1
    checks[f"{prefix}_clean_main_menu"] = recovery.get("clean_main_menu") is True
    checks[f"{prefix}_final_state_no_saved_run"] = _final_state_has_no_saved_run(
        final_state
    )
    checks[f"{prefix}_final_screenshot_in_pack"] = any(
        "recovery_final" in name for name in members
    )
    screenshot_check = _verify_final_screenshot(report)
    checks[f"{prefix}_final_screenshot_valid"] = screenshot_check["ok"]
    checks[f"{prefix}_final_screenshot_detail"] = screenshot_check
    checks[f"{prefix}_evidence_sealed"] = (
        isinstance(terminal, dict) and terminal.get("evidence_sealed") is True
    )
    checks[f"{prefix}_artifact_readable"] = bool(members) and bool(
        (report or {}).get("artifact_status", {}).get("readable")
    )
    checks[f"{prefix}_recovery_detail"] = {
        "restart_count": recovery.get("restart_count"),
        "clean_main_menu": recovery.get("clean_main_menu"),
        "old_run_abandoned": recovery.get("old_run_abandoned"),
        "reason": recovery.get("reason"),
        "final_state": final_state or None,
    }
    return checks


def _final_state_has_no_saved_run(final_state: dict[str, Any]) -> bool:
    """兼容有/无存档内省字段的主菜单干净判定。"""
    if not final_state or final_state.get("has_new_run_action") is not True:
        return False
    has_save = final_state.get("has_run_save")
    if has_save is not None:
        return has_save is False
    actions = [str(action) for action in final_state.get("available_actions") or []]
    return "continue_run" not in actions and "abandon_run" not in actions


def _trace_first_menu_is_clean(report: dict[str, Any] | None) -> bool:
    """旅程轨迹首帧主菜单无旧局（不继承残局）。

    信任层级与平台一致：内省字段 has_run_save=False 即为无旧局（动作列表
    可能含陈旧伪影）；字段缺失时退回动作列表判断。
    """
    trace = _zip_read_json(report, "reports/journey-trace.json")
    if not trace:
        return False
    first_menu = next(
        (
            item.get("state") or {}
            for item in (trace.get("scene_trace") or [])
            if str(item.get("screen") or "").upper() == "MAIN_MENU"
        ),
        {},
    )
    menu = first_menu.get("menu") or {}
    has_save = menu.get("has_run_save", first_menu.get("has_run_save"))
    if has_save is not None:
        return has_save is False
    actions = [str(a) for a in (first_menu.get("available_actions") or [])]
    return "continue_run" not in actions and "abandon_run" not in actions


def compute_verdict(out_dir: Path) -> dict[str, Any]:
    """从 raw/ 保存的真实响应计算 V11 判定（禁止写死期望值）。"""
    raw_dir = out_dir / "raw"
    caps = load_raw(raw_dir, "00-capabilities.json")
    submit1 = load_raw(raw_dir, "01-submit-original.json")
    submit2 = load_raw(raw_dir, "02-submit-idempotent.json")
    orig_running = load_raw(raw_dir, "03-original-running.json")
    orig_terminal = load_raw(raw_dir, "05-original-terminal.json")
    orig_report = load_raw(raw_dir, "06-original-report.json")
    resume_submit = load_raw(raw_dir, "07-resume.json")
    resume_terminal = load_raw(raw_dir, "08-resume-terminal.json")
    resume_report = load_raw(raw_dir, "09-resume-report.json")
    second_submit = load_raw(raw_dir, "10-submit-second.json")
    second_running = load_raw(raw_dir, "11-second-running.json")
    second_terminal = load_raw(raw_dir, "13-second-terminal.json")
    second_report = load_raw(raw_dir, "14-second-report.json")
    anti_sleep = load_raw(raw_dir, "15-anti-sleep.json") or {}

    checks: dict[str, Any] = {}

    # ── 全局：六操作清单真实核对 ──
    operations = (caps or {}).get("operations") or []
    checks["capabilities_lists_six_operations"] = all(op in operations for op in SIX_OPERATIONS)

    # ── 幂等：相同幂等键重复提交返回同一 run_id 且不建第二个任务 ──
    checks["idempotent_resubmit_same_run_id"] = bool(
        isinstance(submit1, dict) and isinstance(submit2, dict)
        and submit1.get("run_id")
        and submit1.get("run_id") == submit2.get("run_id")
        and submit1.get("created_at") == submit2.get("created_at")
    )

    # ── ORIG ──
    checks["orig_reached_in_run_before_cancel"] = bool(
        isinstance(orig_running, dict)
        and str(orig_running.get("current_screen") or "").upper() in NON_MENU_SCREENS
    )
    checks.update(_cancel_column_checks("orig", orig_terminal, orig_report))

    # ── RESUME ──
    orig_run_id = (submit1 or {}).get("run_id")
    resume_run_id = (resume_submit or {}).get("run_id")
    checks["resume_created_new_run"] = bool(
        resume_run_id and resume_run_id != orig_run_id
    )
    checks["resume_resumed_from_points_to_orig"] = bool(
        isinstance(resume_terminal, dict)
        and resume_terminal.get("resumed_from") == orig_run_id
    )
    checks["resume_passed"] = (
        isinstance(resume_terminal, dict) and resume_terminal.get("status") == "PASSED"
    )
    checks["resume_report_consistent"] = bool(
        isinstance(resume_report, dict) and isinstance(resume_terminal, dict)
        and (resume_report.get("run") or {}).get("status") == resume_terminal.get("status")
    )
    checks["resume_evidence_sealed"] = bool(
        isinstance(resume_terminal, dict) and resume_terminal.get("evidence_sealed") is True
    )
    # RESUME 不继承残局：其旅程轨迹首帧主菜单不得有旧局
    checks["resume_started_from_clean_menu"] = _trace_first_menu_is_clean(resume_report)

    # ── SECOND ──
    checks["second_fresh_run_id"] = bool(
        isinstance(second_submit, dict)
        and second_submit.get("run_id")
        and second_submit.get("run_id") not in {orig_run_id, resume_run_id}
    )
    checks["second_reached_in_run_before_cancel"] = bool(
        isinstance(second_running, dict)
        and str(second_running.get("current_screen") or "").upper() in NON_MENU_SCREENS
    )
    checks.update(_cancel_column_checks("second", second_terminal, second_report))
    # SECOND 不继承残局：其旅程轨迹首帧主菜单不得有旧局
    checks["second_started_from_clean_menu"] = _trace_first_menu_is_clean(second_report)

    # ── 防睡眠：运行中存在、结束后无残留 ──
    checks["anti_sleep_seen_during_runs"] = bool(anti_sleep.get("seen_during_runs"))
    checks["anti_sleep_no_residual"] = anti_sleep.get("residual_clear") is True

    # ── 无环境阻塞终态 ──
    terminals = [orig_terminal, resume_terminal, second_terminal]
    checks["no_blocked_environment"] = all(
        isinstance(t, dict) and t.get("status") != "BLOCKED_ENVIRONMENT" for t in terminals
    )

    pass_keys = [key for key in checks if not key.endswith("_recovery_detail") and not key.endswith("_screenshot_detail")]
    v11_pass = all(checks[key] for key in pass_keys)
    return {
        "V11_PASS": v11_pass,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "run_ids": {
            "orig": orig_run_id,
            "resume": resume_run_id,
            "second": (second_submit or {}).get("run_id"),
        },
        "checks": checks,
        "failed_checks": [key for key in pass_keys if not checks[key]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 V11 跨 Agent 三柱验收")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8090/mcp")
    parser.add_argument("--game-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--out-dir",
        default="tests/output/cross-agent-p1/p1-platform-fix-20260719-v11",
    )
    parser.add_argument("--character", default="IRONCLAD")
    parser.add_argument("--run-timeout", type=int, default=600)
    parser.add_argument(
        "--verdict-only", action="store_true",
        help="不执行真实验收，仅从 out-dir/raw 重算判定",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"

    if args.verdict_only:
        verdict = compute_verdict(out_dir)
        save_raw(raw_dir, "16-verdict.json", verdict)
        log(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["V11_PASS"] else 1

    if out_dir.exists():
        log(f"输出目录已存在（不覆盖历史证据）: {out_dir}")
        return 1

    problems = preflight(args.mcp_url, args.game_url)
    if problems:
        for problem in problems:
            log(f"前置失败: {problem}")
        out_dir.mkdir(parents=True, exist_ok=True)
        save_raw(raw_dir, "00-preflight-failed.json", {"problems": problems})
        return 1

    raw_dir.mkdir(parents=True, exist_ok=True)
    client = McpClient(args.mcp_url)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key_orig = f"v11-{stamp}-orig"
    key_second = f"v11-{stamp}-second"
    caf_seen_any = False
    run_ids: dict[str, Any] = {}

    try:
        # 1) capabilities：核对六操作清单
        caps = client.call("capabilities", {})
        save_raw(raw_dir, "00-capabilities.json", caps)
        log(f"capabilities operations={caps.get('operations')}")

        # 2-4) ORIG：提交 + 相同幂等键重复提交
        submit1 = client.call("submit_run", {
            "journey": "new_run", "idempotency_key": key_orig,
            "timeout": args.run_timeout, "evidence": "full",
            "character_id": args.character,
        })
        save_raw(raw_dir, "01-submit-original.json", submit1)
        orig_rid = submit1["run_id"]
        run_ids["orig"] = orig_rid
        log(f"ORIG 提交: {orig_rid}")

        submit2 = client.call("submit_run", {
            "journey": "new_run", "idempotency_key": key_orig,
            "timeout": args.run_timeout, "evidence": "full",
            "character_id": args.character,
        })
        save_raw(raw_dir, "02-submit-idempotent.json", submit2)
        log(f"幂等重复提交: {submit2.get('run_id')}（应与 ORIG 相同）")

        # 5) 等 ORIG 真实进入局内
        running, in_run, caf_seen = wait_in_run(client, orig_rid, "orig")
        caf_seen_any = caf_seen_any or caf_seen
        save_raw(raw_dir, "03-original-running.json", running)
        if not in_run:
            log("ORIG 未真实进入局内，拒绝取消（避免假取消），验收终止")
            save_raw(raw_dir, "15-anti-sleep.json", {
                "seen_during_runs": caf_seen_any, "residual_clear": not caffeinate_running(),
            })
            save_raw(raw_dir, "16-verdict.json", compute_verdict(out_dir))
            return 1

        # 6-8) 取消 ORIG → 终态 → 报告（真实调用 get_report）
        save_raw(raw_dir, "04-cancel-original.json", client.call("cancel_run", {"run_id": orig_rid}))
        terminal, caf_seen = wait_terminal(client, orig_rid, "orig")
        caf_seen_any = caf_seen_any or caf_seen
        save_raw(raw_dir, "05-original-terminal.json", terminal)
        log(f"ORIG 终态: {terminal.get('status')}")
        save_raw(raw_dir, "06-original-report.json", client.call("get_report", {"run_id": orig_rid}))

        # 9-11) RESUME：创建恢复任务 → 等 PASSED → 报告
        resumed = client.call("resume_run", {"run_id": orig_rid})
        save_raw(raw_dir, "07-resume.json", resumed)
        resume_rid = resumed["run_id"]
        run_ids["resume"] = resume_rid
        log(f"RESUME 提交: {resume_rid}（resumed_from 应={orig_rid}）")
        resume_terminal, caf_seen = wait_terminal(client, resume_rid, "resume")
        caf_seen_any = caf_seen_any or caf_seen
        save_raw(raw_dir, "08-resume-terminal.json", resume_terminal)
        log(f"RESUME 终态: {resume_terminal.get('status')}")
        save_raw(raw_dir, "09-resume-report.json", client.call("get_report", {"run_id": resume_rid}))

        # 12-14) SECOND：新幂等键提交 → 入局部 → 取消 → 终态 → 报告
        second = client.call("submit_run", {
            "journey": "new_run", "idempotency_key": key_second,
            "timeout": args.run_timeout, "evidence": "full",
            "character_id": args.character,
        })
        save_raw(raw_dir, "10-submit-second.json", second)
        second_rid = second["run_id"]
        run_ids["second"] = second_rid
        log(f"SECOND 提交: {second_rid}")
        second_running, in_run2, caf_seen = wait_in_run(client, second_rid, "second")
        caf_seen_any = caf_seen_any or caf_seen
        save_raw(raw_dir, "11-second-running.json", second_running)
        if in_run2:
            save_raw(raw_dir, "12-cancel-second.json", client.call("cancel_run", {"run_id": second_rid}))
            second_terminal, caf_seen = wait_terminal(client, second_rid, "second")
            caf_seen_any = caf_seen_any or caf_seen
            save_raw(raw_dir, "13-second-terminal.json", second_terminal)
            log(f"SECOND 终态: {second_terminal.get('status')}")
            save_raw(raw_dir, "14-second-report.json", client.call("get_report", {"run_id": second_rid}))
        else:
            log("SECOND 未真实进入局内，跳过取消（避免假取消）")

        # 15) 防睡眠：运行中存在、结束后无残留（等 worker 完全退出后再判残留）
        time.sleep(5.0)
        save_raw(raw_dir, "15-anti-sleep.json", {
            "seen_during_runs": caf_seen_any,
            "residual_clear": not caffeinate_running(),
        })
    finally:
        verdict = compute_verdict(out_dir)
        save_raw(raw_dir, "16-verdict.json", verdict)

    summary_lines = [
        "# V11 验收判定",
        "",
        f"- V11_PASS: **{verdict['V11_PASS']}**",
        f"- ORIG: {verdict['run_ids'].get('orig')}",
        f"- RESUME: {verdict['run_ids'].get('resume')}",
        f"- SECOND: {verdict['run_ids'].get('second')}",
        "",
        "## 未通过项",
    ]
    failed = verdict["failed_checks"]
    summary_lines.extend([f"- {name}" for name in failed] or ["- （无）"])
    (out_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    (out_dir / "result.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"V11_PASS={verdict['V11_PASS']} 未通过项={failed or '无'}")
    return 0 if verdict["V11_PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
