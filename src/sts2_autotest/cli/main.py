"""CLI entry points for STS2-AUTOTEST (FR1, FR2, FR24, FR60-62).

Commands: autotest run, autotest doctor, autotest report.
MVP uses minimal argparse (no click) to avoid extra dependencies.
Full click integration deferred to Beta if needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import time
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Sequence, cast

from sts2_autotest.adapters.base import GameAdapterProtocol
from sts2_autotest.cli import mcp_server
from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    OcrProvider,
    ScreenshotHealthDetector,
    TesseractOcrProvider,
    VisualQaEngine,
    build_visual_qa_payload,
)
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_VARIANCE_THRESHOLD,
)
from sts2_autotest.report_html import write_html_report


DEFAULT_EVIDENCE_DIR = "tests/output"


def _create_parser() -> Any:
    import argparse

    p = argparse.ArgumentParser(
        prog="autotest",
        description="STS2-AUTOTEST — Slay the Spire 2 automated testing framework",
    )
    sub = p.add_subparsers(dest="command", help="Available commands")

    # autotest run
    run = sub.add_parser("run", help="Run test cases")
    run.add_argument("--all", action="store_true", help="Run all test cases")
    run.add_argument("--cases", nargs="+", help="Run specific case IDs")
    run.add_argument("--suite", help="Run a named test suite")
    run.add_argument("--failed", action="store_true", help="Re-run failed cases")
    run.add_argument("--resume", action="store_true", help="Resume from last run")
    run.add_argument("--no-resume", action="store_true", help="Ignore existing progress and start fresh")
    run.add_argument("--timeout", type=int, default=30, help="Case timeout (seconds)")
    run.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    run.add_argument("--output-dir", help="Directory for generated test files")
    run.add_argument("--project", help="Project name from workspace config")
    run.add_argument(
        "--adapter",
        choices=["cli", "agent"],
        default=None,
        help="Adapter type (overrides config: cli=STS2-Cli-Mod, agent=STS2-Agent)",
    )
    run.add_argument(
        "--evidence",
        choices=["none", "minimal", "full"],
        default="full",
        help="Evidence collection level",
    )
    run.add_argument(
        "--idempotency-key",
        help="Stable retry key; submitting the same key reuses the existing run",
    )
    run.add_argument(
        "--detach",
        action="store_true",
        help="Submit a persistent background run and return its run ID",
    )
    run.add_argument("--internal-run-id", help=argparse.SUPPRESS)
    run.add_argument(
        "--journey",
        choices=[
            "new_run", "resume_run", "first_battle", "finish_interstitials",
            "goal_scene", "act_traversal", "card_test",
        ],
        help="Run one reusable game journey instead of a project case suite",
    )
    run.add_argument(
        "--character-id",
        default="IRONCLAD",
        help="Character for new_run/first_battle",
    )
    run.add_argument(
        "--card-id",
        default=None,
        help="card_test 旅程要验证的卡牌 ID（运行时控制台格式）",
    )
    run.add_argument(
        "--target-scene",
        choices=[
            "MAIN_MENU", "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT",
            "REST", "SHOP", "CHEST", "CARD_REWARD", "NEXT_ACT",
        ],
        default=None,
        help="目标场景；act_traversal 默认使用 NEXT_ACT",
    )
    run.add_argument(
        "--route-policy",
        choices=["leftmost", "target"],
        default="leftmost",
        help="地图路线规则",
    )
    run.add_argument(
        "--combat-mode",
        choices=["traversal", "basic", "death"],
        default="traversal",
        help="战斗处理规则",
    )

    # autotest review
    review = sub.add_parser("review", help="Review natural language test specs")
    review.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    review.add_argument("--project", help="Project name from workspace config")
    review.add_argument("--output", help="Output path for review report (default: stdout)")
    review.add_argument(
        "--output-dir",
        help="Directory for review artifacts: report, revised drafts, and source map",
    )

    # autotest compile
    compile_cmd_parser = sub.add_parser("compile", help="Compile specs to pytest test files")
    compile_cmd_parser.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    compile_cmd_parser.add_argument("--output-dir", help="Directory for generated test files")
    compile_cmd_parser.add_argument("--project", help="Project name from workspace config")
    compile_cmd_parser.add_argument(
        "--use-revised",
        action="store_true",
        help="Compile confirmed revised drafts instead of the original specs",
    )
    compile_cmd_parser.add_argument(
        "--revised-dir",
        help="Directory containing confirmed revised Markdown drafts",
    )

    # autotest doctor
    doc = sub.add_parser("doctor", help="Check environment readiness")
    doc.add_argument("--json", action="store_true", help="Output structured JSON")
    doc.add_argument("--ci", action="store_true", help="Compact CI-friendly JSON output")

    # autotest report
    rep = sub.add_parser("report", help="Show test run summary")
    rep.add_argument("run_id", nargs="?", help="Run ID to report on")
    rep.add_argument(
        "--evidence-dir",
        default=DEFAULT_EVIDENCE_DIR,
        help="Evidence directory path",
    )
    rep.add_argument("--coverage", action="store_true", help="Show scene coverage report")

    queue = sub.add_parser("queue", help="Manage the local session queue")
    queue.add_argument("queue_action", choices=["pause", "resume", "status"])

    status = sub.add_parser("status", help="Show a persistent run status")
    status.add_argument("run_id")
    status.add_argument("--json", action="store_true", help="Output structured JSON")

    capabilities = sub.add_parser("capabilities", help="Show the stable cross-agent contract")
    capabilities.add_argument("--json", action="store_true", help="Output structured JSON")

    cancel = sub.add_parser("cancel", help="Cancel a persistent run")
    cancel.add_argument("run_id")

    resume = sub.add_parser("resume", help="Resume a persistent run")
    resume.add_argument("run_id")

    sub.add_parser("progress", help="Show saved runtime progress")

    # autotest agent-test (cross-platform Test Agent Runner, replaces run-test-agent.ps1)
    agent_test = sub.add_parser("agent-test", help="Run the full test-agent workflow (build + localization + deploy + smoke)")
    agent_test.add_argument("--mod-project", required=True, help="Path to the mod project root")
    agent_test.add_argument("--task-id", required=True, help="Task identifier (e.g. my-mod-localization-fix)")
    agent_test.add_argument("--infra-path", required=True, help="Path to sts2-dev-infra")
    agent_test.add_argument("--test-plan", help="Path to a test-plan YAML file (reads params from YAML)")
    agent_test.add_argument("--game-mods-path", help="Path to Steam STS2 mods directory (auto-detected if omitted)")
    agent_test.add_argument("--steam-app-id", default="2868840", help="Steam App ID for Slay the Spire 2")
    agent_test.add_argument("--ping-timeout", type=int, default=90, help="Seconds to wait for sts2 ping")
    agent_test.add_argument("--skip-deploy", action="store_true", help="Skip mod deployment step")
    agent_test.add_argument("--skip-launch-game", action="store_true", help="Skip Steam game launch")
    agent_test.add_argument("--skip-game-smoke", action="store_true", help="Skip all in-game smoke tests")
    agent_test.add_argument("--mod-name", help="Mod folder name for deployment (derived from .csproj if omitted)")
    agent_test.add_argument("--require-game-running", action="store_true", default=None,
                            help="Exit with BLOCKED if --skip-game-smoke is used (reads from test-plan if omitted)")

    # autotest serve (B17)
    serve_parser = sub.add_parser("serve", help="Start health check HTTP server (B17)")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    serve_parser.add_argument("--port", type=int, default=8766, help="Bind port")
    serve_parser.set_defaults(func=serve_cmd)

    # autotest serve-mcp (B11 Phase 2)
    serve_mcp_parser = sub.add_parser("serve-mcp", help="Start the MCP test service (B11 Phase 2)")
    serve_mcp_parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    serve_mcp_parser.add_argument("--port", type=int, default=8090, help="Bind port")
    serve_mcp_parser.set_defaults(func=serve_mcp_cmd)

    # autotest gen-report
    gen_report_parser = sub.add_parser("gen-report", help="Generate HTML test report via test-report-html skill")
    gen_report_parser.add_argument("--task-id", help="Task ID (reads from {mod-project}/automation/autotest/output/{task-id}/)")
    gen_report_parser.add_argument("--mod-project", default=".", help="MOD project root used with --task-id (default: current directory)")
    gen_report_parser.add_argument("--config", help="Path to test-results JSON config file")
    gen_report_parser.add_argument("--output", help="Output HTML path (default: auto-detect)")
    gen_report_parser.set_defaults(func=gen_report_cmd)

    visual_qa_parser = sub.add_parser("visual-qa", help="Analyze one screenshot and print structured Visual QA JSON")
    visual_qa_parser.add_argument("--image", required=True, help="Path to screenshot image")
    visual_qa_parser.add_argument(
        "--ocr-provider",
        choices=["disabled", "tesseract"],
        default="disabled",
        help="OCR provider",
    )
    visual_qa_parser.add_argument(
        "--health-provider",
        choices=["disabled", "opencv"],
        default="disabled",
        help="Screenshot health provider",
    )
    visual_qa_parser.add_argument(
        "--tesseract-cmd",
        default="tesseract",
        help="Tesseract command path",
    )
    visual_qa_parser.add_argument(
        "--lang",
        default="chi_sim+eng",
        help="Tesseract language pack string",
    )
    visual_qa_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="OCR timeout in seconds",
    )
    visual_qa_parser.add_argument(
        "--low-variance-threshold",
        type=float,
        default=DEFAULT_LOW_VARIANCE_THRESHOLD,
        help="Low variance threshold for OpenCV health checks",
    )
    visual_qa_parser.add_argument(
        "--low-brightness-threshold",
        type=float,
        default=DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
        help="Low brightness threshold for OpenCV health checks",
    )
    visual_qa_parser.add_argument(
        "--high-brightness-threshold",
        type=float,
        default=DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
        help="High brightness threshold for OpenCV health checks",
    )
    visual_qa_parser.add_argument(
        "--output",
        help="Optional JSON output path",
    )
    visual_qa_parser.set_defaults(func=visual_qa_cmd)

    return p


def _get_env(keys: list[str], default: str) -> str:
    """Look up an env var by trying each STS2_ prefix.

    Tries STS2_ADAPTER__AGENT__<KEY>, STS2_ADAPTER__CLI__<KEY>, and plain <KEY>.
    This mirrors the config loader's env-var convention without importing config.
    """
    for key in keys:
        val = os.environ.get(key)
        if val is not None:
            return val
    return default


def _is_agent_default() -> bool:
    """Check if agent adapter is enabled by default via STS2_ADAPTER__AGENT__ENABLED env var."""
    raw = os.environ.get("STS2_ADAPTER__AGENT__ENABLED", "false")
    return raw.lower() in ("true", "1", "yes")


def _resolve_project_base_dir(project: str | None) -> Path | None:
    """按任务项目名解析项目根目录。

    经当前目录 workspace 配置（sts2-autotest.yaml）中该项目的 manifest
    指针定位；未声明 project 或解析失败时返回 None（调用方回退当前目录）。
    """
    if not project:
        return None
    ws = _load_workspace()
    if ws is None:
        return None
    manifest = ws.mod_manifest_path(project)
    if not manifest:
        return None
    return Path(manifest).resolve().parent


def _create_adapter(adapter_type: str, project: str | None = None) -> GameAdapterProtocol:
    """Create adapter based on type.

    Reads configuration from STS2_ prefixed environment variables,
    mirroring the config/loader.py convention without importing config.

    Args:
        adapter_type: "cli" or "agent" — which adapter to instantiate.
        project: 任务携带的项目名；提供时按该项目自己的配置目录读取
            项目扩展规则（卡牌前缀、种子命令模板），实现按任务隔离。

    Returns:
        A GameAdapterProtocol-compliant adapter instance.
    """
    if adapter_type == "agent":
        from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient
        from sts2_autotest.adapters.project_extension import (
            load_card_id_prefixes,
            load_seed_command_template,
        )

        extension_base_dir = _resolve_project_base_dir(project) or Path.cwd()

        transport_raw = _get_env(["STS2_ADAPTER__AGENT__TRANSPORT"], "http")
        if transport_raw not in ("http", "mcp"):
            raise ValueError(
                "STS2_ADAPTER__AGENT__TRANSPORT must be 'http' or 'mcp'"
            )
        transport = cast(Literal["http", "mcp"], transport_raw)
        agent_endpoint = _get_env(
            ["STS2_ADAPTER__AGENT__ENDPOINT"], "http://127.0.0.1:8080"
        )
        mcp_client = (
            FastMcpAgentClient(
                endpoint=_get_env(
                    ["STS2_ADAPTER__AGENT__MCP_ENDPOINT"],
                    agent_endpoint,
                )
            )
            if transport == "mcp"
            else None
        )

        return AgentAdapter(
            endpoint=agent_endpoint,
            timeout=float(_get_env(["STS2_ADAPTER__AGENT__TIMEOUT"], "30")),
            tool_profile=_get_env(
                ["STS2_ADAPTER__AGENT__TOOL_PROFILE"], "guided"
            ),
            debug_actions=_get_env(
                ["STS2_ADAPTER__AGENT__DEBUG_ACTIONS"], "false"
            ).lower()
            in ("true", "1", "yes"),
            mcp_client=mcp_client,
            transport=transport,
            health_path=_get_env(["STS2_ADAPTER__AGENT__HEALTH_PATH"], "health"),
            state_path=_get_env(
                ["STS2_ADAPTER__AGENT__STATE_PATH"], "state"
            ),
            actions_path=_get_env(
                ["STS2_ADAPTER__AGENT__ACTIONS_PATH"], "actions/available"
            ),
            act_path=_get_env(["STS2_ADAPTER__AGENT__ACT_PATH"], "action"),
            wait_path=_get_env(
                ["STS2_ADAPTER__AGENT__WAIT_PATH"], "wait_until_actionable"
            ),
            card_id_prefixes=load_card_id_prefixes(extension_base_dir),
            seed_command_template=load_seed_command_template(extension_base_dir),
        )
    else:
        from sts2_autotest.adapters.cli_mod import CliModAdapter

        cli_path = os.environ.get("STS2_ADAPTER__CLI__CLI_PATH")
        cli_timeout = float(
            os.environ.get("STS2_ADAPTER__CLI__TIMEOUT", "30")
        )
        return CliModAdapter(cli_path=cli_path, timeout=cli_timeout)


def _run_orchestrator(
    case_ids: list[str],
    timeout: int,
    *,
    progress_path: str | None = None,
    resumed_from: str | None = None,
) -> int:
    """Create an orchestrator and run the given case IDs.

    Delegates to _run_orchestrator_with_adapter with a CliModAdapter.
    Kept for backward compatibility with existing mock-based tests.
    """
    from sts2_autotest.adapters.cli_mod import CliModAdapter

    return _run_orchestrator_with_adapter(
        CliModAdapter(), case_ids, timeout,
        progress_path=progress_path, resumed_from=resumed_from,
    )


def _screen_of(state: Any) -> str | None:
    """Extract the normalized screen name from a state dict or GameState."""
    if state is None:
        return None
    if isinstance(state, dict):
        scr = state.get("screen")
    else:
        scr = getattr(state, "screen", None)
    return str(scr).upper() if scr is not None else None


def _wait_for_main_menu(
    adapter: Any,
    loop: Any,
    *,
    timeout: float = 120.0,
    force_fresh: Any = None,
    sleep: Callable[[float], None] | None = None,
    require_actions: bool = False,
) -> Any | None:
    """Poll until a MAIN_MENU state is readable; return latest state or None.

    require_actions=True 时仅当菜单已发布可执行操作（控制模组加载完成）才
    算到达——V11 实测：游戏重启后画面先显示主菜单，模组仍在加载（界面右下
    角「正在加载模组运行」，可达 1-2 分钟），期间动作列表为空，此时读取
    的状态不可用于干净判定。
    """
    delay = sleep if sleep is not None else time.sleep
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            if callable(force_fresh):
                force_fresh()
            st = loop.run_until_complete(adapter.get_state())
            last = st
            if _screen_of(st) == "MAIN_MENU":
                if not require_actions:
                    return st
                _, actions, _src = _frame_signals(adapter, loop, st)
                if actions:
                    return st
        except Exception:
            last = None
        delay(1.0)
    return last


def _state_view(state: Any) -> dict[str, Any]:
    """把 GameState / dict / 普通对象归一成普通 dict 视图（含 pydantic extras）。

    真实游戏控制接口的主菜单状态里 ``has_run_save`` 嵌套在 ``menu`` 下，
    ``available_actions`` 为字符串列表；判定逻辑统一基于该视图，避免按
    对象形态各写一套取值导致漏判。
    """
    if state is None:
        return {}
    if isinstance(state, dict):
        return state
    model_dump = getattr(state, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:  # noqa: BLE001
            pass
    view: dict[str, Any] = {}
    for key in ("screen", "timestamp", "has_run_save", "menu", "available_actions"):
        value = getattr(state, key, None)
        if value is not None:
            view[key] = value
    return view


def _menu_has_run_save_field(view: dict[str, Any]) -> bool | None:
    """存档内省字段（三态）：True=有旧局；False=明确无旧局；None=字段未发布。

    V11 真实验收证据：游戏控制服务直接内省存档系统，该字段比界面动作列表
    可信——菜单重建期动作列表会短暂摆出陈旧项（放弃成功后仍短暂出现
    continue_run/abandon_run，但 start_new_run 可直接开局且无确认框，
    证明存档已删除、动作是伪影）。
    """
    if "has_run_save" in view:
        value = view.get("has_run_save")
        return value if isinstance(value, bool) else None
    menu = view.get("menu")
    if isinstance(menu, dict) and "has_run_save" in menu:
        value = menu.get("has_run_save")
        return value if isinstance(value, bool) else None
    return None


def _menu_actions(view: dict[str, Any]) -> list[str]:
    return [str(action) for action in (view.get("available_actions") or [])]


def _adapter_actions(adapter: Any, loop: Any) -> list[str]:
    """经适配器协议方法获取可执行动作；失败返回空列表。

    关键差异（V11 实测）：Agent 适配器把真实动作内嵌在状态里；CliMod 适配器
    状态不内嵌动作（菜单动作由屏幕类型静态派生），必须通过协议方法单独获取。
    """
    try:
        actions = loop.run_until_complete(adapter.get_available_actions())
    except Exception:
        return []
    return [str(action) for action in (actions or [])]


def _frame_signals(
    adapter: Any, loop: Any, state: Any
) -> tuple[dict[str, Any], list[str], str]:
    """组合一帧的判定信号：状态视图 + 有效动作列表 + 动作来源。

    有效动作优先取状态内嵌值（state_reported，即状态接口报告值——重建期
    可能为陈旧报告，不保证此刻可点击）；缺失时退回适配器协议方法
    （adapter_derived——CliMod 路径为静态派生名称）；两者皆无则为 none。
    """
    view = _state_view(state)
    actions = _menu_actions(view)
    if actions:
        return view, actions, "state_reported"
    derived = _adapter_actions(adapter, loop)
    if derived:
        return view, derived, "adapter_derived"
    return view, [], "none"


def _frame_dirty(view: dict[str, Any], actions: list[str]) -> bool:
    """该帧是否存在旧局：内省字段优先，字段缺失时退回动作列表。"""
    has_save = _menu_has_run_save_field(view)
    if has_save is not None:
        return has_save
    return "continue_run" in actions


def _frame_clean(view: dict[str, Any], actions: list[str]) -> bool:
    """该帧是否满足干净主菜单：无旧局 + 存在开新局能力。

    has_run_save 显式 False 时忽略动作列表中的陈旧/静态项（V11 实测：
    Agent 菜单重建期会摆出陈旧 continue/abandon；CliMod 动作列表为静态
    派生，同样不代表真实旧局）。
    """
    if _screen_of(view) != "MAIN_MENU":
        return False
    has_save = _menu_has_run_save_field(view)
    if has_save is True:
        return False
    if not any(name in actions for name in _NEW_RUN_ACTIONS):
        return False
    if has_save is False:
        return True
    return "continue_run" not in actions and "abandon_run" not in actions


# 游戏主菜单的开新局能力可能使用其中任一动作名（与 journeys.start_new_run 一致）。
_NEW_RUN_ACTIONS = ("start_new_run", "new_run", "open_character_select")


def _final_state_snapshot(adapter: Any, loop: Any, state: Any) -> dict[str, Any]:
    """恢复后完整状态快照——写入报告供审计独立核对，而非只信平台布尔值。

    has_run_save 为三态权威旧局信号（True/False/None=字段未发布）。
    动作列表标注来源，只描述“谁报告的”，不声称“此刻一定可点击”：
    state_reported 为状态接口报告的动作（菜单重建期可能是陈旧报告）；
    adapter_derived 为适配器协议派生/静态名称——两种来源下的
    continue/abandon 都不单独作为旧局证据，旧局以 has_run_save 为准。
    """
    view, actions, source = _frame_signals(adapter, loop, state)
    snapshot: dict[str, Any] = {
        "screen": _screen_of(state),
        "has_run_save": _menu_has_run_save_field(view),
        "available_actions": actions,
        "actions_source": source,
        "has_continue_run": (
            "continue_run" in actions if source == "state_reported" else None
        ),
        "has_abandon_run": (
            "abandon_run" in actions if source == "state_reported" else None
        ),
        "has_new_run_action": any(name in actions for name in _NEW_RUN_ACTIONS),
        "state_timestamp": view.get("timestamp"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    if source == "state_reported":
        snapshot["actions_note"] = (
            "动作列表为状态接口报告值（菜单重建期可能为陈旧报告）；"
            "旧局以 has_run_save 为准"
        )
    else:
        snapshot["actions_note"] = (
            "动作列表为适配器派生/静态名称，continue/abandon 不代表存在旧局；"
            "旧局以 has_run_save 为准"
        )
    return snapshot


def _settle_main_menu_state(
    adapter: Any,
    loop: Any,
    force_fresh: Any = None,
    *,
    tries: int = 6,
    gap: float = 1.0,
    sleep: Callable[[float], None] | None = None,
    stable_clean_frames: int = 3,
    stable_dirty_frames: int = 3,
) -> tuple[Any | None, str]:
    """连续刷新读取主菜单状态，返回（最近可判定帧, 稳定结论）。

    稳定结论三态：
    - ``"dirty"``：任一帧确认存在旧局（has_run_save 显式 True 或字段缺失时
      动作列表出现 continue_run）。旧局信号一旦出现即锁存——信号可能闪退，
      仍须放弃；连续 stable_dirty_frames 帧确认后提前结束等待。
    - ``"clean"``：连续 stable_clean_frames 个可判定帧均满足干净定义。
      要求连续稳定是为双向防错：既防 V10 的假干净（旧局信号晚到），也防
      V11 实测的反向瞬态（放弃成功后菜单重建摆出陈旧动作/空动作）。
    - ``"undecidable"``：窗口内无法确认（空动作重建帧、信号摇摆、读取中断）。
      调用方必须按失败处理，不得当成干净。

    空操作列表 = 菜单仍在初始化/重建，该帧不可用于判定（不计入稳定计数）。
    V11 实测菜单发布动作可超过 15 秒，调用方应给足 tries 窗口。
    已离开主菜单或读取失败则提前停止（此时返回帧为 None 或非主菜单态）。
    """
    delay = sleep if sleep is not None else time.sleep
    last: Any = None
    decidable: Any = None
    saw_saved_run = False
    clean_streak = 0
    dirty_streak = 0
    for _ in range(tries):
        if callable(force_fresh):
            force_fresh()
        try:
            last = loop.run_until_complete(adapter.get_state())
        except Exception:
            last = None
        if _screen_of(last) != "MAIN_MENU":
            break
        view, actions, _src = _frame_signals(adapter, loop, last)
        if _frame_dirty(view, actions):
            saw_saved_run = True
            clean_streak = 0
            dirty_streak += 1
            decidable = last
        elif _frame_clean(view, actions):
            clean_streak += 1
            dirty_streak = 0
            decidable = last
        else:
            # 可判定但既非脏也非净（如有动作但无开新局能力）→ 重置稳定计数；
            # 空动作重建帧不算可判定帧。
            clean_streak = 0
            dirty_streak = 0
            if actions:
                decidable = last
        if saw_saved_run and dirty_streak >= stable_dirty_frames:
            break
        if not saw_saved_run and clean_streak >= stable_clean_frames:
            break
        delay(gap)
    if saw_saved_run:
        verdict = "dirty"
    elif clean_streak >= stable_clean_frames:
        verdict = "clean"
    else:
        verdict = "undecidable"
    return (decidable if decidable is not None else last), verdict


def _abandon_saved_run(
    adapter: Any, loop: Any, *, force_fresh: Any = None
) -> bool:
    """Abandon the saved run from the main menu and confirm deletion.

    Executes the existing ``abandon_run`` action, handles a possible confirm
    modal, then verifies the saved run is gone. Returns success.
    """
    try:
        loop.run_until_complete(adapter.act("abandon_run"))
    except Exception as exc:  # noqa: BLE001
        print(f"[autotest] abandon_run failed: {exc}")
        return False
    if callable(force_fresh):
        force_fresh()
    try:
        st = loop.run_until_complete(adapter.get_state())
        view, actions, _src = _frame_signals(adapter, loop, st)
        if "confirm_modal" in actions:
            loop.run_until_complete(adapter.act("confirm_modal"))
    except Exception as exc:  # noqa: BLE001
        print(f"[autotest] abandon confirm handling failed: {exc}")
    if callable(force_fresh):
        force_fresh()
    try:
        st2 = loop.run_until_complete(adapter.get_state())
        view2, actions2, _src2 = _frame_signals(adapter, loop, st2)
        return not _frame_dirty(view2, actions2)
    except Exception:
        return False


def _wait_game_gone(lifecycle: Any, *, timeout: float = 30.0) -> None:
    """轮询直到游戏进程消失（best-effort；超时即返回，交由后续启动兜底）。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not lifecycle._game_process_present():
                return
        except Exception:
            pass
        time.sleep(0.5)


def _recover_main_menu_via_restart(
    lifecycle: Any,
    adapter: Any,
    loop: Any,
    *,
    sleep: Callable[[float], None] | None = None,
    settle_tries: int = 10,
    post_abandon_tries: int = 45,
    menu_timeout: float = 120.0,
    operational_timeout: float = 360.0,
) -> dict[str, Any]:
    """审查结论（P1 正式通过方式）：取消后通过「受控重启」恢复到干净主菜单。

    这是*结果目标*（target=MAIN_MENU），不要求走 ESC→放弃 的普通界面退出。
    流程（复用生命周期有界恢复，最多重启一次，禁止无限重启）：
      1. 停止继续操作（取消已生效，JourneyCancelled 已中断旅程）。
      2. 仅结束已确认的 STS2 游戏进程（lifecycle 按进程名/game_exe 兜底）。
      3. 等待游戏进程完全消失、8080 端口释放。
      4. 只启动一次游戏（ensure_environment_ready 内部单次 relaunch）。
      5. 验证健康 + 游戏状态 + 可执行操作全部可读。
      6. 确认到达主菜单。
      7. 若主菜单仍显示旧局：执行现有 abandon_run；处理确认框；确认旧局删除。
      8. 最终确认（MAIN_MENU / 不再提供 continue_run / 可开新局）。

    V10 复核修复：
    - P0-1/P0-2：干净判定只用「放弃后连续稳定读取的最后一帧完整状态」；
      ``ok`` 要求满足干净主菜单全部条件，不再只看 screen=MAIN_MENU。
    - P0-3：全函数只允许一次启动——首启动后未达主菜单即判环境阻塞返回，
      禁止第二次启动；``restart_count`` 记录真实启动次数。
    - P1-1：``final_state`` 保存恢复后完整状态快照供审计独立核对。
    返回结构化 dict；blocked=True → BLOCKED_ENVIRONMENT；
    ok=False 且 blocked=False → FAILED_PLATFORM；ok=True → CANCELLED。
    """
    result: dict[str, Any] = {
        "target": "MAIN_MENU",
        "recovery_method": "controlled_restart",
        "normal_menu_abandon": False,
        "restart_count": 0,
        "final_screen": None,
        "clean_main_menu": False,
        "old_run_abandoned": False,
        "ok": False,
        "blocked": False,
        "reason": None,
        "final_state": None,
    }

    def _force_fresh() -> None:
        # cli_mod 适配器有状态缓存，重启后强制刷新以免读到旧画面。
        if hasattr(adapter, "_cache_stale"):
            try:
                adapter._cache_stale = True
            except Exception:
                pass

    try:
        if hasattr(adapter, "reset_http_client"):
            adapter.reset_http_client()

        # 步骤2-3：无条件结束当前游戏（局内 EVENT/MAP/COMBAT 或已就绪都终止）。
        # 关键：ensure_environment_ready 在「游戏仍可控制」时会*跳过*重启、沿用
        # 原会话；而取消恢复的目标是干净主菜单，必须先把仍在跑的局内实例杀掉，
        # 否则游戏永远停在原屏到不了 MAIN_MENU。因此先显式 terminate + 等进程消失。
        lifecycle.terminate()
        _wait_game_gone(lifecycle, timeout=30.0)
        # 步骤4-5：唯一一次启动（terminate 已在步骤2-3 完成；此处 relaunch→probe）。
        # V10 复核 P0-3：首启动后只允许等待状态稳定，绝不再调用启动恢复——
        # 第二次启动会重新引入 Steam 多弹窗、旧实例冲突和 WindowServer 事故风险。
        # 注意：ensure_environment_ready 的「就绪」判定只看「端口通 + 首帧 screen
        # 非 UNKNOWN」。重启后游戏常需更久才真正可操控（首帧常为 UNKNOWN），因此
        # 这里不因它「未就绪」就立刻判阻塞——先 relaunch，再用 _wait_for_main_menu
        # 轮询（容忍 UNKNOWN 瞬态、最长 menu_timeout）确认真达主菜单。
        readiness = loop.run_until_complete(lifecycle.ensure_environment_ready())
        # restart_count 记录真实启动次数：本函数仅在此处启动一次。
        result["restart_count"] = 1
        _force_fresh()
        if hasattr(adapter, "reset_http_client"):
            adapter.reset_http_client()

        # 步骤6：轮询直到主菜单（启动后需转场时间，给游戏充分初始化窗口）。
        st = _wait_for_main_menu(
            adapter, loop, timeout=menu_timeout, force_fresh=_force_fresh, sleep=sleep
        )
        if st is None or _screen_of(st) != "MAIN_MENU":
            # 一次启动后仍不可操作 → 环境阻塞，禁止再次启动。
            result["blocked"] = True
            reason = getattr(readiness, "reason", None) or "unknown"
            result["reason"] = (
                f"main menu not reached after single controlled restart: {reason}"
            )
            return result

        # 步骤6b：等待控制模组加载完成——V11 实测游戏重启后画面先显示主菜单，
        # 模组仍在加载（「正在加载模组运行」，可达 1-2 分钟），期间动作列表为空，
        # 任何干净/旧局判定都不可信。到达帧已带动作则无需再等；否则等菜单真正
        # 可操作（有效动作非空：状态内嵌或适配器协议方法派生）。
        _, st_actions, _src = _frame_signals(adapter, loop, st)
        if not st_actions:
            st = _wait_for_main_menu(
                adapter, loop, timeout=operational_timeout, force_fresh=_force_fresh,
                sleep=sleep, require_actions=True,
            )
            _, st_actions, _src = _frame_signals(adapter, loop, st)
            if st is None or not st_actions:
                result["blocked"] = True
                result["reason"] = (
                    "main menu not operational after single controlled restart "
                    "(control mod did not finish loading)"
                )
                return result

        # 步骤7：多次稳定读取；确认旧局即执行 abandon_run（含确认框），放弃后
        # 再连续稳定读取——放弃可能触发菜单重建，单帧瞬态干净/瞬态陈旧动作都不
        # 可信（V10 假阳性 + V11 实测空动作重建窗）。读取失败（None）= 控制入口
        # 失联 → 环境阻塞。
        last, verdict = _settle_main_menu_state(
            adapter, loop, _force_fresh, tries=settle_tries, sleep=sleep
        )
        if last is None:
            result["blocked"] = True
            result["reason"] = "game control lost while settling main menu state"
            return result
        if verdict == "dirty":
            _abandon_saved_run(adapter, loop, force_fresh=_force_fresh)
            last, verdict = _settle_main_menu_state(
                adapter, loop, _force_fresh, tries=post_abandon_tries, sleep=sleep
            )
            if last is None:
                result["blocked"] = True
                result["reason"] = "game control lost after abandon_run"
                return result
            result["old_run_abandoned"] = verdict == "clean"

        # 步骤8：以稳定结论做干净判定并留存最近可判定帧快照（P1-1）。
        result["final_screen"] = _screen_of(last)
        result["final_state"] = _final_state_snapshot(adapter, loop, last)
        result["clean_main_menu"] = verdict == "clean"
        if not result["clean_main_menu"]:
            if verdict == "dirty":
                result["reason"] = "saved run still present after abandon"
            else:
                result["reason"] = "main menu reached but not verifiably clean"
            return result
        result["ok"] = True
        return result
    except Exception as exc:  # noqa: BLE001
        result["blocked"] = True
        result["reason"] = f"controlled restart recovery exception: {exc}"
        print(f"[autotest] controlled restart recovery failed: {exc}")
        return result


def _ensure_clean_main_menu(
    adapter: Any, lifecycle: Any, loop: Any
) -> bool:
    """开工前确保游戏处于干净主菜单（审查结论：连续任务不继承残局）。

    跨 Agent / 连续任务场景下，上一任务可能在游戏内留下 COMBAT/MAP/EVENT 等残局
    （例如 resume 任务 PASSED 后游戏留在战斗中）。first_battle / new_run 旅程开局
    必须经主菜单 ``start_new_run`` 动作，而 ``reset_to_main_menu`` 只能处理菜单态、
    无法把战斗态退回——若当前屏非 MAIN_MENU 会抛 JourneyFailure，导致新局起不来。

    此处复用受控重启能力把游戏拉回干净主菜单，保证每个新任务都从主菜单重新开局。
    lifecycle 不可用时（环境非本框架管理）返回 True 交由后续旅程自行判定，不退化行为。
    """
    if lifecycle is None:
        return True
    try:
        st = loop.run_until_complete(adapter.get_state())
    except Exception:
        st = None
    scr = ""
    if st is not None:
        if hasattr(st, "screen"):
            scr = str(st.screen).upper()
        elif isinstance(st, dict):
            scr = str(st.get("screen", "")).upper()
    if scr == "MAIN_MENU":
        # 即便已在主菜单，若残留旧局也要放弃——否则后续 new_run 点 start_new_run
        # 会触发「放弃旧局」确认框，扰乱整个旅程状态（松哥 8 步流程第 7 步）。
        # 放弃后以最后一帧稳定状态复核；仍未干净则经受控重启兜底（V11：
        # 复用已修复的干净判定，避免把脏菜单当成开局起点）。
        def _ff() -> None:
            if hasattr(adapter, "_cache_stale"):
                try:
                    adapter._cache_stale = True
                except Exception:
                    pass

        # 等待控制模组加载完成（菜单可操作：有效动作非空）。模组加载时长波动大
        # （V11 实测 60s～>180s），给足窗口；超时则走受控重启兜底。
        op = _wait_for_main_menu(
            adapter, loop, timeout=300.0, force_fresh=_ff, require_actions=True
        )
        _, op_actions, _src = _frame_signals(adapter, loop, op)
        if op is None or not op_actions:
            recovered = _recover_main_menu_via_restart(lifecycle, adapter, loop)
            return bool(recovered.get("ok"))
        last, verdict = _settle_main_menu_state(adapter, loop, _ff, tries=30)
        if verdict == "dirty":
            _abandon_saved_run(adapter, loop, force_fresh=_ff)
            last, verdict = _settle_main_menu_state(adapter, loop, _ff, tries=30)
        if verdict != "clean":
            # 旧局清不掉、或菜单迟迟不发布可执行操作（无法确认干净）→
            # 经受控重启兜底，保证后续任务从可验证的干净起点开局（V11）。
            recovered = _recover_main_menu_via_restart(lifecycle, adapter, loop)
            return bool(recovered.get("ok"))
        return True
    print(
        f"[autotest] pre-journey screen={scr or 'UNKNOWN'}; "
        f"performing controlled restart to recover clean MAIN_MENU"
    )
    recovered = _recover_main_menu_via_restart(lifecycle, adapter, loop)
    return bool(recovered.get("ok"))


def _run_orchestrator_with_adapter(
    adapter: GameAdapterProtocol,
    case_ids: list[str],
    timeout: int,
    *,
    progress_path: str | None = None,
    resumed_from: str | None = None,
    adapter_factory: Callable[[], GameAdapterProtocol] | None = None,
    run_id: str | None = None,
) -> int:
    """Create an orchestrator with the given adapter and run the given case IDs.

    Lifecycle is owned by run_all() (start_session + stop_session inside).
    """
    from sts2_autotest.core.orchestrator import TestOrchestrator
    from sts2_autotest.core.recovery import DefaultRecoveryStrategy
    from sts2_autotest.core.steam import SteamController
    from sts2_autotest.core.runtime_factory import build_lifecycle_manager
    from sts2_autotest.core.evidence_hooks import build_evidence_hooks

    steam = SteamController(startup_timeout=60.0)
    recreate_factory = adapter_factory or (lambda: _create_adapter("cli"))
    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR)
    )
    try:
        lifecycle = build_lifecycle_manager(adapter, steam, evidence_root)
    except (OSError, ValueError) as exc:
        print(f"[autotest] lifecycle manager unavailable: {exc}")
        lifecycle = None
    recovery = DefaultRecoveryStrategy(
        adapter_factory=recreate_factory,
        game_startup_timeout=60.0,
        steam_controller=steam,
        lifecycle_manager=lifecycle,
    )
    evidence = build_evidence_hooks(evidence_root, pack_id=run_id)
    status_callback = None
    if run_id:
        store = _run_store()

        def status_callback(status: str) -> None:
            store.update(run_id, phase=status, status=status)

    orch = TestOrchestrator(
        adapter=adapter,
        recovery=recovery,
        evidence=evidence,
        progress_path=progress_path,
        resumed_from=resumed_from,
        lock_path=str(evidence_root / ".sts2-autotest.lock"),
        lifecycle=lifecycle,
        status_callback=status_callback,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        summary = loop.run_until_complete(orch.run_all(case_ids))
        if summary.session_id == "failed-start":
            print("[autotest] ERROR: Failed to start session — adapter not ready.")
            return 1
        print(f"[autotest] {summary.passed} passed, {summary.failed} failed, "
              f"{summary.crashed} crashed, {summary.skipped} skipped")
        return 1 if summary.is_failed else 0
    finally:
        loop.close()


def _find_game(steam_roots: list[Path] | None = None) -> Path | None:
    """Locate Slay the Spire 2 game directory.

    Search order:
    1. STS2_GAME_PATH environment variable (manual override)
    2. Each Steam library root → steamapps/common/Slay the Spire 2
    """
    env_game = os.environ.get("STS2_GAME_PATH")
    if env_game:
        p = Path(env_game)
        if p.is_dir():
            return p

    from sts2_autotest.adapters.discovery import find_game_dir
    return find_game_dir(steam_roots)


def _get_progress_path() -> Path:
    """Get default progress file path."""
    return Path(DEFAULT_EVIDENCE_DIR) / ".progress" / "session-progress.json"


def _load_workspace() -> Any | None:
    """Try to load workspace config from default locations."""
    from sts2_autotest.core.workspace import Workspace
    candidates = ["sts2-autotest.yaml", "sts2-autotest.yml"]
    for fname in candidates:
        if os.path.isfile(fname):
            try:
                return Workspace.from_yaml(fname)
            except Exception:
                return None
    return None


def _load_character_aliases() -> dict[str, str]:
    """Load project-provided character aliases.

    统一经 adapters.project_extension 读取：项目配置文件
    （sts2-autotest.yaml 或 sts2-mod.yaml 指向的配置）为基，
    ``STS2_PROJECT__CHARACTER_ALIASES`` 环境变量覆盖。
    两者皆无时返回空映射（平台默认仅认识原游戏角色）。
    """
    from sts2_autotest.adapters.project_extension import load_character_aliases

    return load_character_aliases()


def _resolve_spec_dir(args: Any) -> str | None:
    """Resolve spec directory from args or workspace config."""
    spec_dir = getattr(args, "spec_dir", None)
    if isinstance(spec_dir, str) and spec_dir:
        return spec_dir
    if spec_dir:
        return str(spec_dir)
    project_name = getattr(args, "project", None)
    if project_name:
        ws = _load_workspace()
        if ws:
            project = ws.resolve_project(project_name)
            project_spec_dir = getattr(project, "spec_dir", None) if project else None
            if isinstance(project_spec_dir, str):
                return project_spec_dir
            if project_spec_dir:
                return str(project_spec_dir)
    # Fall back to default spec directory
    default_spec = "docs/process/specs"
    if os.path.isdir(default_spec):
        return default_spec
    return None


def _resolve_output_dir(args: Any, spec_dir: str) -> str:
    """Resolve output directory for generated test files."""
    output_dir = getattr(args, "output_dir", None)
    if isinstance(output_dir, str) and output_dir:
        return output_dir
    if output_dir:
        return str(output_dir)
    project_name = getattr(args, "project", None)
    if project_name:
        ws = _load_workspace()
        if ws:
            project = ws.resolve_project(project_name)
            project_output_dir = getattr(project, "output_dir", None) if project else None
            if isinstance(project_output_dir, str) and project_output_dir:
                return project_output_dir
            if project_output_dir:
                return str(project_output_dir)
    return "tests/generated"


def _resolve_compile_input_dir(args: Any, spec_dir: str) -> str:
    """Resolve original vs revised spec input for compile."""
    if not getattr(args, "use_revised", False):
        return spec_dir

    revised_dir = getattr(args, "revised_dir", None)
    if isinstance(revised_dir, str) and revised_dir:
        return revised_dir

    candidate = Path(spec_dir) / "revised"
    if candidate.is_dir():
        return str(candidate)

    print(
        "[autotest] --use-revised requires --revised-dir or a revised/ "
        "directory under --spec-dir"
    )
    return ""


def _write_review_artifacts(
    output_dir: str,
    report_markdown: str,
    drafts: list[Any],
) -> None:
    """Persist review report and revised draft candidates."""
    root = Path(output_dir)
    revised_dir = root / "revised"
    revised_dir.mkdir(parents=True, exist_ok=True)
    (root / "review-report.md").write_text(report_markdown + "\n", encoding="utf-8")

    source_map: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        draft_path = revised_dir / f"{draft.spec_id}.md"
        draft_path.write_text(draft.markdown_content, encoding="utf-8")
        source_map[draft.spec_id] = {
            "original_path": draft.original_path,
            "revised_path": str(draft_path),
            "changes_summary": draft.changes_summary,
        }
    (revised_dir / ".source-map.json").write_text(
        json.dumps(source_map, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _looks_like_generated_output_dir(path: Path) -> bool:
    """Return True when the directory only contains generated pytest artifacts."""
    if not path.exists():
        return True
    for child in path.iterdir():
        if child.is_dir():
            if child.name != "__pycache__":
                return False
            continue
        if not (child.name.startswith("test_") and child.suffix == ".py"):
            return False
    return True


def _ensure_output_dir_writable(output_dir: str) -> None:
    """Ensure the compile output directory is writable.

    If the directory looks like a pure generated-artifacts directory and the
    probe file cannot be created, recreate the directory once.
    """
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".autotest-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return
    except PermissionError:
        if not _looks_like_generated_output_dir(path):
            raise
        shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def review_cmd(args: Any) -> int:
    """Review natural language test specs and print report."""
    from sts2_autotest.core.markdown_parser import MarkdownParser
    from sts2_autotest.core.spec_reviewer import SpecReviewer

    spec_dir = _resolve_spec_dir(args)
    if not spec_dir:
        print("[autotest] Specify --spec-dir or configure workspace in sts2-autotest.yaml")
        return 1

    if not os.path.isdir(spec_dir):
        print(f"[autotest] Spec directory not found: {spec_dir}")
        return 1

    parser = MarkdownParser()
    cases, suites = parser.discover_specs(spec_dir)

    if not cases and not suites:
        print(f"[autotest] No spec files found in {spec_dir}")
        return 0

    reviewer = SpecReviewer()
    all_passed = True
    revised_drafts: list[Any] = []

    output_lines: list[str] = []
    def _out(line: str = "") -> None:
        output_lines.append(line)

    _out(f"[autotest] Reviewing {len(cases)} case(s), {len(suites)} suite(s) in {spec_dir}\n")

    for spec in cases:
        report = reviewer.review(spec)
        status = "PASS" if report.passed else "ISSUES"
        _out(f"  [{status}] {spec.id}: {spec.title}")
        if not report.passed:
            all_passed = False
            for issue in report.issues:
                _out(f"         - [{issue.category.value}] {issue.location}: {issue.description}")
        draft = reviewer.generate_revised_draft(spec, report)
        revised_drafts.append(draft)
        if draft.changes_summary and draft.changes_summary != ["No issues found — spec is already clean"]:
            for change in draft.changes_summary:
                _out(f"           draft: {change}")

    for suite in suites:
        report = reviewer.review_suite(suite)
        status = "PASS" if report.passed else "ISSUES"
        _out(f"  [{status}] {suite.id}: {suite.title}")
        if not report.passed:
            for issue in report.issues:
                _out(f"         - [{issue.category.value}] {issue.location}: {issue.description}")

    _out(f"\n[autotest] Review complete. {'All passed' if all_passed else 'Some issues found'}.")

    output = "\n".join(output_lines)
    output_path = getattr(args, "output", None)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output + "\n")
    else:
        print(output)

    output_dir = getattr(args, "output_dir", None)
    if isinstance(output_dir, str) and output_dir:
        _write_review_artifacts(output_dir, output, revised_drafts)

    return 0 if all_passed else 1


def compile_cmd(args: Any) -> int:
    """Compile specs to pytest test files."""
    from sts2_autotest.core.markdown_parser import MarkdownParser
    from sts2_autotest.core.code_generator import CodeGenerator

    spec_dir = _resolve_spec_dir(args)
    if not spec_dir:
        print("[autotest] Specify --spec-dir or configure workspace in sts2-autotest.yaml")
        return 1

    if not os.path.isdir(spec_dir):
        print(f"[autotest] Spec directory not found: {spec_dir}")
        return 1

    compile_input_dir = _resolve_compile_input_dir(args, spec_dir)
    if not compile_input_dir:
        return 1

    output_dir = _resolve_output_dir(args, spec_dir)
    _ensure_output_dir_writable(output_dir)
    parser = MarkdownParser()
    cases, suites = parser.discover_specs(compile_input_dir)
    if compile_input_dir != spec_dir:
        _, original_suites = parser.discover_specs(spec_dir)
        existing_suite_ids = {suite.id for suite in suites}
        suites.extend(
            suite for suite in original_suites if suite.id not in existing_suite_ids
        )

    if not cases and not suites:
        print(f"[autotest] No spec files found in {compile_input_dir}")
        return 0

    generator = CodeGenerator(character_aliases=_load_character_aliases())
    generated: list[str] = []
    specs_by_id = {s.id: s for s in cases}

    for spec in cases:
        out_path = generator.generate_to_file(spec, output_dir)
        generated.append(out_path)
        print(f"  [GENERATED] {out_path}")

    for suite in suites:
        suite_code = generator.generate_suite_test(suite, specs_by_id)
        suite_path = os.path.join(output_dir, f"test_{suite.id.lower().replace('-', '_')}.py")
        with open(suite_path, "w", encoding="utf-8") as f:
            f.write(suite_code)
        generated.append(suite_path)
        print(f"  [GENERATED] {suite_path}")

    print(f"\n[autotest] Generated {len(generated)} test file(s) in {output_dir}")
    return 0


def _suite_test_path(output_dir: str, suite_id: str) -> str:
    file_name = f"test_{suite_id.lower().replace('-', '_')}.py"
    return str(Path(output_dir) / file_name)


def _pytest_targets_for_compiled_specs(spec_dir: str, output_dir: str) -> list[str]:
    """Return pytest targets for compiled pipeline runs.

    Suites with sequential shared sessions own their included cases, so pipeline
    mode runs suite files when any suite specs exist. This avoids also running
    state-dependent included cases as independent pytest tests.
    """
    from sts2_autotest.core.markdown_parser import MarkdownParser

    _, suites = MarkdownParser().discover_specs(spec_dir)
    if suites:
        return [_suite_test_path(output_dir, suite.id) for suite in suites]
    return [output_dir]


def _dispatch_orchestrator(
    adapter: GameAdapterProtocol | None,
    case_ids: list[str],
    timeout: int,
    *,
    progress_path: str | None = None,
    resumed_from: str | None = None,
    use_agent: bool = False,
    run_id: str | None = None,
) -> int:
    """Route to the correct orchestrator with the given adapter.

    Always uses _run_orchestrator_with_adapter so that the adapter
    instance from _create_adapter() (which respects STS2_ env vars)
    is actually used. When adapter is None (from --resume branches
    that haven't built it yet), creates it from default env config.
    """
    if adapter is None:
        use_agent = _is_agent_default()
        adapter = _create_adapter("agent") if use_agent else _create_adapter("cli")

    kwargs: dict[str, str | None] = {"progress_path": progress_path}
    if resumed_from is not None:
        kwargs["resumed_from"] = resumed_from

    return _run_orchestrator_with_adapter(
        adapter,
        case_ids,
        timeout=timeout,
        adapter_factory=lambda: _create_adapter("agent" if use_agent else "cli"),
        run_id=run_id,
        **kwargs,
    )


def queue_cmd(args: Any) -> int:
    """Handle the persistent single-game queue."""
    store = _run_store()
    pause_marker = store.root / "queue.paused"
    action = args.queue_action
    if action == "pause":
        pause_marker.parent.mkdir(parents=True, exist_ok=True)
        pause_marker.write_text("paused\n", encoding="utf-8")
        print(json.dumps({"queue": "local", "paused": True, "action": "pause"}))
    elif action == "resume":
        pause_marker.unlink(missing_ok=True)
        print(json.dumps({"queue": "local", "paused": False, "action": "resume"}))
    else:
        records = store.list(include_terminal=False)
        print(json.dumps({
            "queue": "local",
            "paused": pause_marker.exists(),
            "depth": len(records),
            "runs": [record.run_id for record in records],
        }, ensure_ascii=False))
    return 0


def capabilities_cmd(args: Any) -> int:
    """Print the stable capability contract for non-MCP Agent clients."""
    from sts2_autotest.cli.mcp_tools import handle_capabilities

    print(json.dumps(handle_capabilities({}), ensure_ascii=False, indent=2 if args.json else None))
    return 0


def progress_cmd(args: Any) -> int:
    """Print the last saved runtime progress snapshot."""
    from sts2_autotest.core.progress import load_progress

    record = load_progress(_get_progress_path())
    if record is None:
        print("[autotest] Progress file missing or corrupted.")
        return 1

    print(json.dumps({
        "session_id": record.session_id,
        "current_case": record.current_case,
        "current_step": record.current_step,
        "game_screen": record.game_screen,
        "recovery_status": record.recovery_status,
        "paused": record.paused,
        "completed_cases": record.completed_cases,
        "pending_cases": record.pending_cases,
        "last_updated": record.last_updated,
    }, ensure_ascii=False))
    return 0


def _run_store() -> Any:
    from sts2_autotest.core.run_service import RunStore

    return RunStore(os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs"))


def _child_argv(args: Any, run_id: str) -> list[str]:
    """Rebuild a stable foreground command for a detached worker."""
    argv = ["run"]
    if getattr(args, "all", False):
        argv.append("--all")
    if getattr(args, "cases", None):
        argv.extend(["--cases", *args.cases])
    if getattr(args, "suite", None):
        argv.extend(["--suite", args.suite])
    if getattr(args, "failed", False):
        argv.append("--failed")
    if getattr(args, "resume", False):
        argv.append("--resume")
    if getattr(args, "no_resume", False):
        argv.append("--no-resume")
    for name, flag in (
        ("timeout", "--timeout"),
        ("project", "--project"),
        ("spec_dir", "--spec-dir"),
        ("output_dir", "--output-dir"),
        ("adapter", "--adapter"),
        ("evidence", "--evidence"),
        ("idempotency_key", "--idempotency-key"),
    ):
        value = getattr(args, name, None)
        if value is not None:
            argv.extend([flag, str(value)])
    if getattr(args, "journey", None):
        argv.extend(["--journey", str(args.journey)])
    if getattr(args, "character_id", None):
        argv.extend(["--character-id", str(args.character_id)])
    if getattr(args, "target_scene", None):
        argv.extend(["--target-scene", str(args.target_scene)])
    if getattr(args, "route_policy", None):
        argv.extend(["--route-policy", str(args.route_policy)])
    if getattr(args, "combat_mode", None):
        argv.extend(["--combat-mode", str(args.combat_mode)])
    if getattr(args, "card_id", None):
        argv.extend(["--card-id", str(args.card_id)])
    argv.extend(["--internal-run-id", run_id])
    return argv


def _submit_detached_run(args: Any, *, request_override: Any | None = None) -> int:
    from sts2_autotest.core.run_service import RunRequest, spawn_worker

    store = _run_store()
    request = request_override or RunRequest(
        project=getattr(args, "project", None),
        suite=getattr(args, "suite", None),
        cases=list(getattr(args, "cases", None) or []),
        mode="resume" if getattr(args, "resume", False) else "new",
        timeout=int(getattr(args, "timeout", 30)),
        adapter=getattr(args, "adapter", None),
        spec_dir=getattr(args, "spec_dir", None),
        evidence=getattr(args, "evidence", "full"),
        idempotency_key=getattr(args, "idempotency_key", None),
        metadata={
            **({"journey": getattr(args, "journey")} if getattr(args, "journey", None) else {}),
            **({"character_id": getattr(args, "character_id")} if getattr(args, "character_id", None) else {}),
            **({"target_scene": getattr(args, "target_scene")} if getattr(args, "target_scene", None) else {}),
            "route_policy": getattr(args, "route_policy", "leftmost"),
            "combat_mode": getattr(args, "combat_mode", "traversal"),
        },
    )
    record = store.create(request)
    if record.request is not request:
        print(json.dumps({"run_id": record.run_id, "status": record.status}, ensure_ascii=False))
        return 0
    request.argv = _child_argv(args, record.run_id)
    store.update(record.run_id, request=request)
    # 修复四：恢复任务在新 run_id 上显式记录继承来源。
    resumed_from = request.metadata.get("resumed_from")
    if resumed_from:
        store.update(record.run_id, resumed_from=str(resumed_from))
    try:
        spawn_worker(store, record, request.argv)
    except OSError as exc:
        store.update(
            record.run_id,
            status="FAILED_PLATFORM",
            phase="COMPLETED",
            finished_at=datetime_now_iso(),
            message=f"Cannot start detached worker: {exc}",
        )
        print(json.dumps({"run_id": record.run_id, "status": "FAILED_PLATFORM"}))
        return 1
    print(json.dumps({"run_id": record.run_id, "status": "QUEUED"}, ensure_ascii=False))
    return 0


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def status_cmd(args: Any) -> int:
    from sts2_autotest.core.run_service import serialize_record

    store = _run_store()
    store.reap_if_worker_gone(args.run_id)
    payload = serialize_record(store.load(args.run_id))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if payload.get("status") != "NOT_FOUND" else 1


def cancel_cmd(args: Any) -> int:
    from sts2_autotest.core.run_service import serialize_record

    payload = serialize_record(_run_store().request_cancel(args.run_id))
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") != "NOT_FOUND" else 1


def resume_run_cmd(args: Any) -> int:
    from sts2_autotest.core.run_service import RunRequest, resume_precheck

    store = _run_store()
    old = store.load(args.run_id)
    if old is None:
        print(json.dumps({"status": "NOT_FOUND", "run_id": args.run_id}))
        return 1
    # 修复四：只有原任务的取消/失败收尾完全结束（终态 + 证据已封存）才允许恢复。
    ok, reason = resume_precheck(old)
    if not ok:
        print(json.dumps({
            "status": "NOT_RESUMABLE",
            "run_id": args.run_id,
            "original_status": old.status,
            "evidence_sealed": old.evidence_sealed,
            "reason": reason,
        }, ensure_ascii=False))
        return 1
    argv = [item for item in old.request.argv if item not in ("--resume", "--detach")]
    # The old request can predate persistent runs; use its structured fields then.
    if not argv:
        argv = ["run", "--all"]
    request = RunRequest(
        project=old.request.project,
        suite=old.request.suite,
        cases=list(old.request.cases),
        mode="resume",
        timeout=old.request.timeout,
        adapter=old.request.adapter,
        spec_dir=old.request.spec_dir,
        evidence=old.request.evidence,
        metadata={**old.request.metadata, "resumed_from": old.run_id},
    )
    from argparse import Namespace

    # Reuse the normal detached submission while preserving the original request.
    ns = Namespace(
        project=request.project,
        suite=request.suite,
        cases=request.cases,
        all="--all" in argv,
        failed=False,
        resume=True,
        no_resume=False,
        timeout=request.timeout,
        spec_dir=request.spec_dir,
        output_dir=None,
        adapter=request.adapter,
        journey=request.metadata.get("journey"),
        character_id=request.metadata.get("character_id", "IRONCLAD"),
        target_scene=request.metadata.get("target_scene"),
        route_policy=request.metadata.get("route_policy", "leftmost"),
        combat_mode=request.metadata.get("combat_mode", "traversal"),
        evidence=request.evidence,
        idempotency_key=None,
    )
    return _submit_detached_run(ns, request_override=request)


def run_cmd(args: Any) -> int:
    """Submit or execute a run through the common persistent run service."""
    if getattr(args, "detach", False) and not getattr(args, "internal_run_id", None):
        return _submit_detached_run(args)

    internal_run_id = getattr(args, "internal_run_id", None)
    if not internal_run_id:
        return _run_cmd_foreground(args)

    from sts2_autotest.core.run_service import RunCancelled, complete_record, wait_for_turn

    store = _run_store()
    try:
        wait_for_turn(store, internal_run_id, timeout=float(getattr(args, "timeout", 30)) * 10)
        store.update(internal_run_id, phase="PRECHECK", status="PRECHECK")
        store.update(internal_run_id, phase="PREPARING", status="PREPARING")
        store.update(internal_run_id, phase="STARTING", status="STARTING")
        store.update(internal_run_id, phase="RUNNING", status="RUNNING")
        rc = _run_cmd_foreground(args)
        # 若旅程内部已处理取消并把任务标为终态（finish_cancel），不要用
        # complete_record 覆盖它——取消收尾的终态与原因码必须保留。
        finalized = store.load(internal_run_id)
        if finalized is not None and finalized.is_terminal:
            return rc
        store.update(internal_run_id, phase="COLLECTING", status="COLLECTING")
        evidence_root = Path(
            os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR)
        )
        run_evidence_dir = evidence_root / internal_run_id
        result_payload: dict[str, Any] = {}
        result_path = run_evidence_dir / "reports" / "run-result.json"
        if result_path.is_file():
            try:
                loaded = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    result_payload = loaded
            except (OSError, ValueError):
                pass
        complete_record(
            store,
            internal_run_id,
            exit_code=rc,
            result=_compact_persistent_result(result_payload, run_evidence_dir),
            evidence_dir=str(run_evidence_dir if run_evidence_dir.exists() else evidence_root),
        )
        return rc
    except RunCancelled:
        return 1
    except Exception as exc:
        complete_record(
            store,
            internal_run_id,
            exit_code=1,
            message=str(exc),
        )
        print(f"[autotest] persistent run failed: {exc}")
        return 1


def _write_journey_failure(
    pack_dir: Path,
    *,
    journey: str,
    failure: dict[str, Any],
    duration_ms: int,
) -> None:
    """把失败留证单独落盘：卡在哪个页面、最后执行了什么、状态轨迹与原因。"""
    try:
        reports = pack_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        payload = {
            "journey": journey,
            "duration_ms": duration_ms,
            "stuck_screen": failure.get("stuck_screen"),
            "last_action": failure.get("last_action"),
            "reason_code": failure.get("reason_code"),
            "reason": failure.get("reason"),
            "status_trajectory": failure.get("status_trajectory"),
            "last_state": failure.get("last_state"),
            "evidence_hint": "崩溃截图见 ../screenshots，游戏日志见 ../logs",
        }
        (reports / "journey-failure.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _compact_persistent_result(payload: dict[str, Any], evidence_dir: Path) -> dict[str, Any]:
    """任务记录只保存摘要；完整状态轨迹留在证据文件中。"""
    compact = {
        key: value
        for key, value in payload.items()
        if key not in {"journey_evidence", "last_state"}
    }
    if "journey_evidence" in payload:
        compact["journey_trace_path"] = str(
            (evidence_dir / "reports" / "journey-trace.json").resolve()
        )
    failure = compact.get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("last_state"), dict):
        failure = dict(failure)
        failure.pop("last_state", None)
        failure["last_state_path"] = str(
            (evidence_dir / "reports" / "journey-failure.json").resolve()
        )
        compact["failure"] = failure
    return compact


def _write_journey_evidence(
    evidence_root: Path,
    run_id: str | None,
    *,
    journey: str,
    target_scene: str,
    evidence: dict[str, Any],
    duration_ms: int | None = None,
) -> None:
    """把通用旅程的场景、操作、地图和证据清单写成机器可读文件。"""
    if not run_id:
        return
    report_dir = evidence_root / run_id / "reports"
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        trace_evidence = dict(evidence)
        if duration_ms is not None:
            trace_evidence["duration_ms"] = duration_ms
        trace = {
            "journey": journey,
            "target_scene": target_scene,
            **trace_evidence,
        }
        (report_dir / "journey-trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        root = evidence_root / run_id
        screenshot_paths = sorted(
            path for path in (root / "screenshots").glob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ) if (root / "screenshots").is_dir() else []
        log_paths = sorted(
            path for path in (root / "logs").glob("*")
            if path.is_file() and path.suffix.lower() == ".log"
        ) if (root / "logs").is_dir() else []
        screenshot_count = len(screenshot_paths)
        log_count = len(log_paths)
        files = [
            str(path.relative_to(root))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        declared_files = [
            "summary.json",
            "summary.md",
            "reports/run-result.json",
            "reports/journey-trace.json",
            "reports/evidence-manifest.json",
            "reports/junit.xml",
        ]
        missing_files = [name for name in declared_files if not (root / name).is_file()]

        def image_dimensions(path: Path) -> tuple[int, int] | None:
            try:
                with path.open("rb") as handle:
                    data = handle.read(1024 * 1024)
            except OSError:
                return None
            if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
                return (
                    int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"),
                )
            if not data.startswith(b"\xff\xd8"):
                return None
            offset = 2
            while offset + 9 < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                while offset < len(data) and data[offset] == 0xFF:
                    offset += 1
                if offset >= len(data):
                    break
                marker = data[offset]
                offset += 1
                if marker in {0xD8, 0xD9}:
                    continue
                if offset + 2 > len(data):
                    break
                segment_length = int.from_bytes(data[offset:offset + 2], "big")
                if segment_length < 2 or offset + segment_length > len(data):
                    break
                if marker in {
                    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                } and segment_length >= 7:
                    height = int.from_bytes(data[offset + 3:offset + 5], "big")
                    width = int.from_bytes(data[offset + 5:offset + 7], "big")
                    return width, height
                offset += segment_length
            return None

        screenshot_details = []
        for path in screenshot_paths:
            dimensions = image_dimensions(path)
            screenshot_details.append({
                "path": str(path.relative_to(root)),
                "format": path.suffix.lower().lstrip("."),
                "width": dimensions[0] if dimensions else None,
                "height": dimensions[1] if dimensions else None,
                "bytes": path.stat().st_size,
            })
        log_details = [
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
            }
            for path in log_paths
        ]

        artifact_candidates = sorted(
            path for path in (evidence_root / "artifacts").glob(f"{run_id}_*.zip")
            if path.is_file()
        ) if (evidence_root / "artifacts").is_dir() else []
        artifact_path = artifact_candidates[-1].resolve() if artifact_candidates else None
        archive_counts: dict[str, Any] = {"status": "unavailable"}
        if artifact_path is not None:
            try:
                with zipfile.ZipFile(artifact_path) as archive:
                    archive_screenshots = sum(
                        name.startswith("screenshots/")
                        and Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"}
                        for name in archive.namelist()
                    )
                    archive_logs = sum(
                        name.startswith("logs/") and Path(name).suffix.lower() == ".log"
                        for name in archive.namelist()
                    )
                archive_counts = {
                    "status": "verified",
                    "path": str(artifact_path),
                    "screenshot_count": archive_screenshots,
                    "log_count": archive_logs,
                    "counts_match": (
                        archive_screenshots == screenshot_count
                        and archive_logs == log_count
                    ),
                }
                if not archive_counts["counts_match"]:
                    archive_counts["status"] = "mismatch"
            except (OSError, zipfile.BadZipFile) as exc:
                archive_counts = {
                    "status": "unreadable",
                    "path": str(artifact_path),
                    "reason": str(exc),
                }

        if platform.system() == "Darwin":
            log_source_candidates = [
                Path.home() / "Library/Application Support/SlayTheSpire2/logs",
                Path.home() / "Library/Application Support/Godot/app_userdata/Slay the Spire 2/logs",
            ]
        elif platform.system() == "Windows":
            log_source_candidates = [
                Path(os.environ.get("APPDATA", "")) / "Godot/app_userdata/Slay the Spire 2/logs",
            ]
        else:
            log_source_candidates = [
                Path.home() / ".local/share/godot/app_userdata/Slay the Spire 2/logs",
            ]
        log_source = os.environ.get("STS2_GODOT_LOG_DIR") or next(
            (str(path) for path in log_source_candidates if path.is_dir()),
            str(log_source_candidates[0]),
        )
        (report_dir / "evidence-manifest.json").write_text(
            json.dumps(
                {
                    "evidence_root": str(root.resolve()),
                    "declared": declared_files,
                    "existing_files": files,
                    "missing_files": missing_files,
                    "evidence_level": os.environ.get("STS2_AUTOTEST_EVIDENCE", "full"),
                    "screenshot_count": screenshot_count,
                    "log_count": log_count,
                    "screenshots": screenshot_details,
                    "logs": log_details,
                    "log_source": log_source,
                    "artifact_path": str(artifact_path) if artifact_path else None,
                    "archive": archive_counts,
                    "capture_status": {
                        "screenshots": (
                            {"status": "available", "count": screenshot_count}
                            if screenshot_count
                            else {
                                "status": "unavailable",
                                "count": 0,
                                "reason": "运行结束时截图目录为空；未将空目录当作已截图。",
                            }
                        ),
                        "logs": (
                            {"status": "available", "count": log_count}
                            if log_count
                            else {
                                "status": "unavailable",
                                "count": 0,
                                "reason": "本次运行没有可复制的游戏日志文件。",
                            }
                        ),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


async def _wait_for_stable_api_state(
    adapter: GameAdapterProtocol,
    initial: dict[str, Any],
    *,
    timeout: float = 4.0,
    interval: float = 0.5,
    settle: float = 0.5,
) -> dict[str, Any]:
    """截图前等待 API 状态连续一致，并给窗口渲染留出 settle 时间。

    游戏画面切换滞后于状态接口（曾出现第二章 MAP 截图仍显示卡牌选择
    界面）；连续两次读取指纹一致后再等一个 settle 间隔，可显著降低
    截图拍到上一页的概率。超时仍未稳定时用最后一次读取，不阻塞任务。
    """
    from sts2_autotest.core.journeys import _fingerprint

    latest = initial
    previous_fp = _fingerprint(initial)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            payload = (await adapter.get_state()).model_dump()
            payload["available_actions"] = await adapter.get_available_actions()
        except Exception:
            break
        latest = payload
        current_fp = _fingerprint(payload)
        if current_fp == previous_fp:
            break
        previous_fp = current_fp
    await asyncio.sleep(settle)
    return latest


def _classify_cancel_cleanup_error(exc: BaseException) -> str:
    """把取消收尾阶段（恢复主菜单）的异常归类为取消失败原因码。

    - 游戏控制入口不可用（连接被拒/掉线）→ GAME_CONTROL_UNAVAILABLE（环境阻塞，可 resume）
    - 其它清理失败 → CANCEL_CLEANUP_FAILED（平台失败）
    """
    from sts2_autotest.common.errors import CancelFailureReason

    text = str(exc).lower()
    control_signals = (
        "connection refused",
        "connection error",
        "cannot connect",
        "game not running",
        "mod not loaded",
        "connection reset",
    )
    if any(signal in text for signal in control_signals):
        return CancelFailureReason.GAME_CONTROL_UNAVAILABLE.value
    return CancelFailureReason.CANCEL_CLEANUP_FAILED.value


def _resolve_combat_mode_with_debug_check(
    adapter: GameAdapterProtocol,
    combat_mode: str,
    loop: asyncio.AbstractEventLoop,
) -> tuple[str, str | None]:
    """启动真实任务前再验证调试能力，据此决定有效战斗模式（修复二运行期双真）。

    traversal（快速结束战斗）需"配置要求启用 AND 实际探测确认可用"双真才可启用；
    未验证时降级为 basic，避免对不可用的 win_combat 反复空试。death 模式是刻意
    只结束回合的死亡测试，绝不降级。探测非破坏性、绝不抛错。

    Returns:
        (有效战斗模式, 降级原因)。未降级时原因为 None。
    """
    if combat_mode != "traversal":
        return combat_mode, None
    verify = getattr(adapter, "verify_debug_actions", None)
    if verify is None:
        return "basic", "DEBUG_VERIFY_UNSUPPORTED"
    try:
        verification = loop.run_until_complete(verify())
    except Exception:
        return "basic", "DEBUG_VERIFY_ERROR"
    if getattr(verification, "verified", False):
        return "traversal", None
    return "basic", getattr(verification, "reason", None) or "DEBUG_CONSOLE_UNAVAILABLE"


def _run_environment_precheck(adapter: GameAdapterProtocol) -> str | None:
    """启动旅程前验证游戏可控；不可控则做有界自恢复（修复一串接）。

    仅当项目提供了游戏可执行文件/目录（本框架可自行拉起）时才启用预检——否则
    跳过，因为不去恢复不归我们管理的环境。就绪或跳过返回 None；未就绪返回阻塞
    原因字符串（调用方据此判 BLOCKED_ENVIRONMENT）。预检绝不抛错冒泡。

    Returns:
        None 表示环境就绪或预检不适用；非空字符串为阻塞原因。
    """
    from sts2_autotest.core.runtime_factory import build_lifecycle_manager
    from sts2_autotest.core.steam import SteamController

    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR)
    )
    try:
        lifecycle = build_lifecycle_manager(
            adapter, SteamController(), evidence_root
        )
    except (OSError, ValueError):
        lifecycle = None
    if lifecycle is None:
        return None  # 环境非本框架管理，跳过预检

    # 恢复拉起改用 ``open <bundle>``（见 lifecycle.GameLifecycleManager.launch），
    # macOS 上可稳定拉起、约 18s 到达可控主菜单；因此放宽等待窗口，给刚拉起的
    # 游戏留出初始化时间（首帧常为 UNKNOWN，wait_for_controllable 会等到真正屏幕）。
    try:
        lifecycle.api_timeout = min(float(getattr(lifecycle, "api_timeout", 150.0)), 180.0)
    except (TypeError, ValueError):
        lifecycle.api_timeout = 180.0

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        readiness = loop.run_until_complete(lifecycle.ensure_environment_ready())
    except Exception as exc:  # noqa: BLE001 - 预检失败不得中断，归类为环境阻塞
        return f"PRECHECK_ERROR:{exc!r}"
    finally:
        loop.close()
        asyncio.set_event_loop(None)
        # 预检在临时循环上经共享适配器发起了 HTTP 请求，连接已绑定到刚关闭的循环。
        # 丢弃适配器缓存的 httpx 客户端，避免后续旅程复用中毒连接、在响应关闭阶段
        # 抛出 "Event loop is closed"。用 getattr 兼容非 Agent 适配器。
        reset = getattr(adapter, "reset_http_client", None)
        if callable(reset):
            reset()

    if getattr(readiness, "ready", False):
        return None
    reason = getattr(readiness, "reason", None)
    reason_str = getattr(reason, "value", None) or (str(reason) if reason else None)
    return reason_str or "ENVIRONMENT_NOT_READY"


def _run_journey_foreground(
    adapter: GameAdapterProtocol,
    *,
    journey: str,
    character_id: str,
    timeout: float,
    run_id: str | None = None,
    target_scene: str | None = None,
    route_policy: str = "leftmost",
    combat_mode: str = "traversal",
    card_id: str | None = None,
    precheck: bool = False,
) -> int:
    """执行平台提供的通用游戏旅程。

    precheck: 仅真实 CLI 入口传 True，启动旅程前做环境预检（游戏可控性 + 有界
    自恢复）。内部单元测试直接调用本函数时默认 False，只验旅程执行逻辑，避免在
    仅设置了 STS2_GAME_DIR（无实际游戏进程）的环境里被预检误拦。
    """
    from sts2_autotest.common.errors import STS2Error
    from sts2_autotest.core.journeys import (
        GenericJourneys,
        JourneyCancelled,
        JourneyFailure,
        _extract_chapter,
    )
    from sts2_autotest.core.evidence_hooks import build_evidence_hooks
    from sts2_autotest.core.action_model import TestResult

    case_id = f"journey:{journey}"
    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR)
    )
    evidence = build_evidence_hooks(evidence_root, pack_id=run_id)
    evidence.on_case_start(case_id)

    # 取消收尾（审查结论 #5）在局内取消且 reset 失败时需要 lifecycle 的受控
    # 重启能力回到主菜单。此处构建管理器（仅封装，不启进程）；若环境无法定位
    # 游戏目录则置 None，取消收尾会据此跳过受控重启并归类为清理失败。
    from sts2_autotest.core.steam import SteamController
    from sts2_autotest.core.runtime_factory import build_lifecycle_manager

    lifecycle: Any = None
    steam = SteamController(startup_timeout=60.0)
    try:
        lifecycle = build_lifecycle_manager(adapter, steam, evidence_root)
    except (OSError, ValueError) as exc:
        print(f"[autotest] lifecycle manager unavailable: {exc}")
        lifecycle = None

    # P0-3：防睡眠守护覆盖整个真实任务生命周期（预检 → 运行 → 取消清理 → 证据封存）。
    # macOS 上经 caffeinate 防止显示器/系统睡眠中断渲染与游戏控制 API；非 macOS 为
    # no-op。无论通过/失败/取消/阻塞，均在函数 finally 中关闭，避免孤儿进程。
    from sts2_autotest.core.anti_sleep import AntiSleepGuard

    anti_sleep = AntiSleepGuard()
    anti_sleep_started = anti_sleep.start()

    async def capture_key_state(state: dict[str, Any]) -> None:
        screen = str(state.get("screen") or "").upper()
        chapter = _extract_chapter(state)
        if screen in {"EVENT", "COMBAT", "CARD_REWARD", "GAME_OVER"} or (
            screen == "MAP" and chapter == 2
        ):
            capture_state = getattr(evidence, "capture_state", None)
            if callable(capture_state):
                stable_state = await _wait_for_stable_api_state(adapter, state)
                safe_case_id = case_id.replace(":", "_")
                capture_state(
                    f"{safe_case_id}_{screen}_{int(time.monotonic() * 1000)}",
                    stable_state,
                )

    def write_result(
        status: str,
        message: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not run_id:
            return
        result_dir = evidence_root / run_id / "reports"
        try:
            result_dir.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "run_id": run_id,
                "task_id": run_id,
                "status": status,
            }
            if message:
                payload["message"] = message
            if extra:
                payload.update(extra)
            # P0-3：记录防睡眠守护是否在任务开始时成功拉起，便于无人值守审计。
            payload["anti_sleep_started"] = bool(anti_sleep_started)
            (result_dir / "run-result.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 兼容 RunStore 布局：detach 任务状态存于 .runs/{run_id}/run.json，
            # 但报告层（report 命令 / run-result.json）默认只认顶层
            # tests/output/{run_id}/reports/。两处不协同会导致取消类任务的
            # run-result.json 落盘位置与 run 记录脱节（审查结论：四处一致缺口）。
            # 此处把 run-result.json 同步镜像进 .runs/{run_id}/reports/，
            # 让证据与任务记录同源同址，report 命令也能据此发现并合成报告。
            store_dir = evidence_root / ".runs" / run_id / "reports"
            try:
                store_dir.mkdir(parents=True, exist_ok=True)
                (store_dir / "run-result.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        except OSError:
            pass

    def persist_artifact_path() -> None:
        """把已生成的实际压缩包路径写回任务结果，拒绝伪造默认路径。

        P1-4：同时更新顶层 run-result.json 与 .runs 镜像，避免顶层目录被保留策略
        清理后内部报告丢失证据包地址。两处必须由同一个动作原子更新。
        """
        if not run_id:
            return
        candidates = sorted(
            path for path in (evidence_root / "artifacts").glob(f"{run_id}_*.zip")
            if path.is_file()
        ) if (evidence_root / "artifacts").is_dir() else []
        if not candidates:
            return
        artifact_path = str(candidates[-1].resolve())
        targets = [
            evidence_root / run_id / "reports" / "run-result.json",
            evidence_root / ".runs" / run_id / "reports" / "run-result.json",
        ]
        for result_path in targets:
            try:
                if not result_path.is_file():
                    continue
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                payload["artifact_path"] = artifact_path
                payload["evidence_pack_url"] = artifact_path
                result_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except (OSError, ValueError, TypeError):
                pass

    # 修复一串接：真正启动旅程前做环境预检（游戏可控性 + 有界自恢复）。
    # 环境不就绪时直接判 BLOCKED_ENVIRONMENT，绝不带病进入旅程执行。
    # 仅真实 CLI 入口（precheck=True）执行；内部单测直接调用时跳过。
    if precheck:
        # v9 修复①：连续任务场景下上一任务可能把游戏留在战斗/地图/结算残局
        # （#6「留在战斗中」），预检会把残留进程判成 GAME_PROCESS_STALE 直接 BLOCK，
        # 导致旅程派发层的受控重启（_ensure_clean_main_menu）根本没机会执行。
        # 故在预检之前先确保干净主菜单：非主菜单态即经受控重启（走可靠的 Steam
        # 重拉路径）回到 MAIN_MENU，再进预检，避免预检在发起恢复前就拦截。
        # 首任务游戏本就在 MAIN_MENU 时此处仅一次 get_state 空转，不会误重启。
        try:
            if lifecycle is not None:
                _pc_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_pc_loop)
                try:
                    _ensure_clean_main_menu(adapter, lifecycle, _pc_loop)
                finally:
                    _pc_loop.close()
                    asyncio.set_event_loop(None)
                    reset = getattr(adapter, "reset_http_client", None)
                    if callable(reset):
                        reset()
            precheck_reason = _run_environment_precheck(adapter)
        except Exception:
            # 启动前恢复或环境预检自身抛异常（而非返回 reason）：
            # 先停防睡眠守护再上抛，避免遗留孤儿 caffeinate（松哥 P1 复查要点）。
            try:
                anti_sleep.stop()
            except Exception:
                pass
            raise
        if precheck_reason is not None:
            write_result(
                "BLOCKED_ENVIRONMENT",
                message=f"environment precheck failed: {precheck_reason}",
                extra={"reason": precheck_reason, "phase": "precheck"},
            )
            evidence.on_session_end({
                "total": 1, "passed": 0, "failed": 0, "crashed": 0, "skipped": 1,
                "duration_ms": 0, "status": "BLOCKED_ENVIRONMENT",
            })
            print(
                json.dumps(
                    {
                        "journey": journey,
                        "status": "BLOCKED_ENVIRONMENT",
                        "reason": precheck_reason,
                        "phase": "precheck",
                    },
                    ensure_ascii=False,
                )
            )
            # v9 修复②：预检失败提前 return 会跳过函数末尾 finally（含
            # anti_sleep.stop），导致遗留孤儿 caffeinate。此处显式释放防睡眠守护。
            try:
                anti_sleep.stop()
            except Exception:
                pass
            return 2

    started = time.monotonic()
    runner: GenericJourneys | None = None

    def publish_progress(progress: dict[str, Any]) -> None:
        if not run_id:
            return
        try:
            from sts2_autotest.core.run_service import RunStore

            RunStore(os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs")).update(
                run_id,
                progress=progress,
            )
        except (OSError, RuntimeError, ValueError):
            pass

    def cancel_requested() -> bool:
        """读取本任务是否被请求取消。旅程据此在发起下一步操作前停止。"""
        if not run_id:
            return False
        try:
            from sts2_autotest.core.run_service import RunStore

            record = RunStore(
                os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs")
            ).load(run_id)
            return bool(record and record.cancel_requested)
        except (OSError, RuntimeError, ValueError):
            return False

    resolved_target = (target_scene or "").upper()
    if journey == "act_traversal":
        resolved_target = "NEXT_ACT"
    elif not resolved_target:
        resolved_target = {
            "new_run": "MAP",
            "resume_run": "MAP",
            "first_battle": "COMBAT",
            "finish_interstitials": "MAP",
        }.get(journey, "MAP")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # 修复二运行期双真：启动旅程前再次验证调试能力。traversal（快速结束战斗）
        # 仅在"配置启用 AND 实际探测确认"双真时才真正启用，否则降级为 basic，
        # 避免对不可用的 win_combat 反复空试拖垮任务。
        effective_combat_mode, debug_downgrade = _resolve_combat_mode_with_debug_check(
            adapter, combat_mode, loop
        )
        if debug_downgrade:
            print(
                json.dumps(
                    {
                        "debug_actions_downgrade": debug_downgrade,
                        "requested_combat_mode": combat_mode,
                        "combat_mode": effective_combat_mode,
                    },
                    ensure_ascii=False,
                )
            )
        runner = GenericJourneys(
            adapter,
            timeout=timeout,
            target_scene=resolved_target,
            route_policy=route_policy,
            combat_mode=effective_combat_mode,
            progress_callback=publish_progress,
            observation_callback=capture_key_state,
            cancel_check=cancel_requested,
        )
        if journey == "new_run":
            # 连续任务场景下上一任务可能把游戏留在战斗/地图残局；开局前先确保
            # 干净主菜单，避免 start_new_run 在 COMBAT 态因无法回菜单而失败。
            _ensure_clean_main_menu(adapter, lifecycle, loop)
            result = loop.run_until_complete(runner.start_new_run(character_id))
        elif journey == "resume_run":
            result = loop.run_until_complete(runner.resume_run())
        elif journey == "first_battle":
            # 同上：第二任务（不继承残局）必须从干净主菜单重新开局。
            _ensure_clean_main_menu(adapter, lifecycle, loop)
            result = loop.run_until_complete(runner.enter_first_battle(character_id=character_id))
        elif journey == "card_test":
            if not card_id:
                raise JourneyFailure("card_test 旅程需要 --card-id 参数")
            result = loop.run_until_complete(
                runner.card_test(character_id, card_id)
            )
        elif journey in {"goal_scene", "act_traversal"} or target_scene:
            result = loop.run_until_complete(
                runner.execute_target(
                    character_id=character_id,
                    target_scene=resolved_target,
                    route_policy=route_policy,
                    combat_mode=effective_combat_mode,
                )
            )
        else:
            result = loop.run_until_complete(runner.finish_interstitials())

        duration_ms = int((time.monotonic() - started) * 1000)
        # 终态视觉凭证：API 状态可能先于画面翻页（曾出现第二章 MAP 命名截图
        # 仍显示事件页）。旅程结束后不再有任何操作，延长 settle 让渲染追上，
        # 保证至少一张截图与最终状态一致。凭证失败不影响任务结果。
        capture_final = getattr(evidence, "capture_state", None)
        if callable(capture_final):
            try:
                stable_final = loop.run_until_complete(
                    _wait_for_stable_api_state(
                        adapter, result, timeout=6.0, interval=0.5, settle=2.5
                    )
                )
                safe_case_id = case_id.replace(":", "_")
                final_screen = str(stable_final.get("screen") or "UNKNOWN").upper()
                capture_final(
                    f"{safe_case_id}_FINAL_{final_screen}_{int(time.monotonic() * 1000)}",
                    stable_final,
                )
            except Exception as exc:
                print(f"[autotest] WARNING: final-state screenshot failed (non-blocking): {exc}")
        trajectory = list(runner.trajectory)
        journey_evidence = runner.evidence
        journey_evidence["duration_ms"] = duration_ms
        evidence.on_case_end(TestResult(case_id, "pass", json.dumps(result, ensure_ascii=False)))
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=journey_evidence,
            duration_ms=duration_ms,
        )
        write_result(
            "PASSED",
            extra={
                "duration_ms": duration_ms,
                "status_trajectory": trajectory,
                "final_state": result.get("screen"),
                "target_scene": resolved_target,
                "journey_evidence": journey_evidence,
                "evidence_dir": str(evidence_root / run_id) if run_id else None,
            },
        )
        evidence.on_session_end({
            "total": 1, "passed": 1, "failed": 0, "crashed": 0, "skipped": 0,
            "duration_ms": duration_ms,
        })
        persist_artifact_path()
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=journey_evidence,
            duration_ms=duration_ms,
        )
        refresh_artifact = getattr(evidence, "refresh_artifact", None)
        if callable(refresh_artifact):
            refresh_artifact()
        # 松哥 P1 复查：PASSED 收尾后须把证据封存状态回写 RunStore，
        # 否则恢复任务已有完整压缩包但记录仍显示 evidence_sealed=false。
        if run_id:
            try:
                from sts2_autotest.core.run_service import RunStore

                RunStore(
                    os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs")
                ).update(
                    run_id,
                    evidence_sealed=True,
                    evidence_dir=str(evidence_root / run_id),
                )
            except (OSError, RuntimeError, ValueError):
                pass
        print(json.dumps({"journey": journey, "status": "PASSED", "state": result}, ensure_ascii=False))
        return 0
    except JourneyCancelled as exc:
        # 修复三：取消收尾是完整生命周期，不是简单终止。
        # 顺序：停止新操作(已在旅程内停) → 存取消前状态 → 恢复干净主菜单 →
        # 校验 → 写报告 → 封存证据 → 才把任务标为终态 → 释放名额。
        duration_ms = int((time.monotonic() - started) * 1000)
        pre_cancel_state = getattr(exc, "last_state", None)
        last_action = getattr(exc, "last_action", None)
        cleanup_reason: str | None = None
        recovered_state: dict[str, Any] | None = None
        # JourneyCancelled 只可能在 runner 创建后由其执行过程抛出，此处 runner 必非 None。
        assert runner is not None

        # 1) 受控重启恢复到干净主菜单（P1 正式通过方式：结果目标=MAIN_MENU，
        #    不要求 ESC 正常退出）。仅重启一次；失败按 BLOCKED_ENVIRONMENT 归类，
        #    禁止无限重启引发 Steam 弹窗或系统事故。
        recovery: dict[str, Any] = (
            _recover_main_menu_via_restart(lifecycle, adapter, loop)
            if lifecycle is not None
            else {
                "ok": False,
                "blocked": False,
                "final_screen": None,
                "recovery_method": None,
                "normal_menu_abandon": False,
                "restart_count": 0,
                "clean_main_menu": False,
                "old_run_abandoned": False,
                "target": "MAIN_MENU",
                "reason": "lifecycle unavailable",
                "final_state": None,
            }
        )
        # P1-1：报告保留恢复后完整状态快照（含 has_run_save/可执行操作/时间戳），
        # 审计可独立核对，而非只能相信平台自己计算的 clean_main_menu 布尔值。
        recovered_state = recovery.get("final_state") or {
            "screen": recovery.get("final_screen")
        }
        if recovery.get("blocked"):
            from sts2_autotest.common.errors import CancelFailureReason

            cleanup_reason = CancelFailureReason.GAME_CONTROL_UNAVAILABLE.value
        elif recovery.get("ok"):
            cleanup_reason = None
        else:
            from sts2_autotest.common.errors import CancelFailureReason

            cleanup_reason = CancelFailureReason.CANCEL_CLEANUP_FAILED.value

        # 终态判定：受控重启失败→BLOCKED_ENVIRONMENT；清理失败→FAILED_PLATFORM；
        # 干净恢复→CANCELLED。report 必须如实写 recovery_method=controlled_restart，
        # 不得写成 normal_game_menu（正常 ESC 放弃已降级为后续增强，不阻塞 P1）。
        from sts2_autotest.common.errors import CancelFailureReason as _CFR

        if cleanup_reason is None:
            terminal_status = "CANCELLED"
        elif str(cleanup_reason) == _CFR.GAME_CONTROL_UNAVAILABLE.value:
            terminal_status = "BLOCKED_ENVIRONMENT"
        else:
            terminal_status = "FAILED_PLATFORM"

        trajectory = list(runner.trajectory) if runner is not None else []
        journey_evidence = runner.evidence if runner is not None else {}
        journey_evidence["duration_ms"] = duration_ms

        # 2) 写取消报告 + 落盘证据 + 封存。证据封存失败单独归类。
        sealed = False
        cancel_result = {
            "duration_ms": duration_ms,
            "status_trajectory": trajectory,
            "pre_cancel_state": pre_cancel_state,
            "pre_cancel_screen": (pre_cancel_state or {}).get("screen")
            if isinstance(pre_cancel_state, dict) else None,
            "recovered_screen": (recovered_state or {}).get("screen")
            if isinstance(recovered_state, dict) else None,
            "last_action": last_action,
            "journey_evidence": journey_evidence,
            "evidence_dir": str(evidence_root / run_id) if run_id else None,
            # 受控重启恢复结果标记（如实写，不得伪装 normal_game_menu）。
            "recovery": {
                "target": recovery.get("target"),
                "recovery_method": recovery.get("recovery_method"),
                "normal_menu_abandon": recovery.get("normal_menu_abandon"),
                "restart_count": recovery.get("restart_count"),
                "final_screen": recovery.get("final_screen"),
                "clean_main_menu": recovery.get("clean_main_menu"),
                "old_run_abandoned": recovery.get("old_run_abandoned"),
                "reason": recovery.get("reason"),
                "final_state": recovery.get("final_state"),
            },
        }
        # V11 复核要点：最终截图必须在恢复收尾（干净确认）之后采集，
        # 与报告中的 final_state 互为独立证据，随证据包一并封存。
        try:
            capture_state = getattr(evidence, "capture_state", None)
            if callable(capture_state) and recovery.get("final_state") is not None:
                capture_state(
                    f"{case_id}_recovery_final".replace(":", "_"),
                    recovery["final_state"],
                )
        except Exception as shot_exc:  # noqa: BLE001
            print(f"[autotest] recovery final screenshot failed: {shot_exc}")
        try:
            evidence.on_case_end(TestResult(case_id, "skip", "cancelled"))
            # P0-1：先落盘 run-result.json，再生成证据清单，保证清单统计时文件已存在。
            write_result(
                terminal_status,
                message="Run cancelled by request",
                extra=cancel_result,
            )
            # 第一次清单：run-result.json 已存在，summary.md/summary.json 随后由
            # on_session_end 落盘；此处清单的 missing_files 可能仍含这两者，但会在
            # 下方「最终清单」重算时消除。
            _write_journey_evidence(
                evidence_root,
                run_id,
                journey=journey,
                target_scene=resolved_target,
                evidence=journey_evidence,
                duration_ms=duration_ms,
            )
            evidence.on_session_end({
                "total": 1, "passed": 0, "failed": 0, "crashed": 0, "skipped": 1,
                "duration_ms": duration_ms,
                "status": terminal_status,
            })
            persist_artifact_path()
            # 最终清单：此时 run-result.json 已含 artifact_path、压缩包已生成，
            # archive_counts 可校验、missing_files 应为空。
            _write_journey_evidence(
                evidence_root,
                run_id,
                journey=journey,
                target_scene=resolved_target,
                evidence=journey_evidence,
                duration_ms=duration_ms,
            )
            refresh_artifact = getattr(evidence, "refresh_artifact", None)
            if callable(refresh_artifact):
                # 最终封存：必须排在「最终清单生成」之后，确保压缩包内的证据清单
                # 与落盘版本完全一致（修复 P0：压缩包内清单声称文件缺失的矛盾）。
                refresh_artifact()
            sealed = True
        except Exception as evi_exc:  # noqa: BLE001
            from sts2_autotest.common.errors import CancelFailureReason
            if cleanup_reason is None:
                cleanup_reason = CancelFailureReason.CANCEL_EVIDENCE_FAILED.value
            print(f"[autotest] cancel cleanup (evidence sealing) failed: {evi_exc}")

        # 3) 只有收尾全部走完，才把任务标为终态并释放名额。
        if run_id:
            try:
                from sts2_autotest.core.run_service import RunStore

                RunStore(
                    os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs")
                ).finish_cancel(
                    run_id,
                    reason=cleanup_reason,
                    evidence_dir=str(evidence_root / run_id),
                    sealed=sealed,
                    result=cancel_result,
                )
            except (OSError, RuntimeError, ValueError) as store_exc:
                print(f"[autotest] finish_cancel persist failed: {store_exc}")
        print(json.dumps(
            {"journey": journey, "status": terminal_status, "reason": cleanup_reason},
            ensure_ascii=False,
        ))
        return 0 if cleanup_reason is None else 1
    except (STS2Error, JourneyFailure) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        last_state = getattr(exc, "last_state", None)
        last_action = getattr(exc, "last_action", None)
        trajectory = list(runner.trajectory) if runner is not None else []
        stuck_screen = (
            str((last_state or {}).get("screen"))
            if isinstance(last_state, dict) else None
        )
        failure = {
            "stuck_screen": stuck_screen,
            "last_action": last_action,
            "last_state": last_state,
            "reason_code": getattr(exc, "reason_code", None),
            "reason": str(exc),
            "status_trajectory": trajectory,
        }
        # 通用旅程的导航、动作、证据和目标达成失败均属于平台执行失败；
        # 只有项目断言才允许归类为 FAILED_PRODUCT。
        environment_signals = (
            "connection refused",
            "connection error",
            "game not running",
            "mod not loaded",
            "cannot connect",
        )
        is_environment_blocked = isinstance(exc, STS2Error) and any(
            signal in str(exc).lower() for signal in environment_signals
        )
        status = "BLOCKED_ENVIRONMENT" if is_environment_blocked else "FAILED_PLATFORM"
        journey_evidence = runner.evidence if runner is not None else {}
        journey_evidence["duration_ms"] = duration_ms
        evidence.on_crash(case_id, exc)
        evidence.on_case_end(TestResult(case_id, "crash", str(exc)))
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=journey_evidence,
            duration_ms=duration_ms,
        )
        write_result(
            status,
            message=str(exc),
            extra={
                "duration_ms": duration_ms,
                "status_trajectory": trajectory,
                "failure": failure,
                "target_scene": resolved_target,
                "journey_evidence": journey_evidence,
                "evidence_dir": str(evidence_root / run_id) if run_id else None,
            },
        )
        if run_id:
            _write_journey_failure(evidence_root / run_id, journey=journey, failure=failure, duration_ms=duration_ms)
        evidence.on_session_end({
            "total": 1, "passed": 0, "failed": 0, "crashed": 1, "skipped": 0,
            "duration_ms": duration_ms,
            "status": status,
        })
        persist_artifact_path()
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=journey_evidence,
            duration_ms=duration_ms,
        )
        refresh_artifact = getattr(evidence, "refresh_artifact", None)
        if callable(refresh_artifact):
            refresh_artifact()
        print(json.dumps({"journey": journey, "status": status, "message": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        evidence.on_crash(case_id, exc)
        evidence.on_case_end(TestResult(case_id, "fail", str(exc)))
        write_result(
            "FAILED_PRODUCT",
            message=str(exc),
            extra={
                "duration_ms": duration_ms,
                "evidence_dir": str(evidence_root / run_id) if run_id else None,
            },
        )
        evidence.on_session_end({
            "total": 1, "passed": 0, "failed": 1, "crashed": 0, "skipped": 0,
            "duration_ms": duration_ms,
        })
        print(json.dumps({"journey": journey, "status": "FAILED_PRODUCT", "message": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        try:
            loop.run_until_complete(adapter.cleanup())
        except Exception:
            pass
        loop.close()
        # P0-3：无论任务以何种方式结束，都释放防睡眠守护，避免留下孤儿 caffeinate。
        try:
            anti_sleep.stop()
        except Exception:
            pass


def _run_cmd_foreground(args: Any) -> int:
    """Dispatch run command — connects to the real orchestrator with resume support."""
    from sts2_autotest.core.progress import clear_progress, load_progress

    evidence_mode = getattr(args, "evidence", None)
    if evidence_mode:
        os.environ["STS2_AUTOTEST_EVIDENCE"] = str(evidence_mode)

    # Determine adapter type: --adapter flag takes precedence, then env var default
    adapter_type: str = args.adapter or ("agent" if _is_agent_default() else "cli")
    use_agent = adapter_type == "agent"
    adapter = _create_adapter(adapter_type, project=getattr(args, "project", None))

    if getattr(args, "journey", None) or getattr(args, "target_scene", None):
        journey_kwargs: dict[str, Any] = {
            "journey": args.journey,
            "character_id": args.character_id,
            "timeout": float(args.timeout),
            "run_id": getattr(args, "internal_run_id", None),
            "precheck": True,
        }
        if getattr(args, "target_scene", None) is not None:
            journey_kwargs["target_scene"] = args.target_scene
        if getattr(args, "route_policy", "leftmost") != "leftmost":
            journey_kwargs["route_policy"] = args.route_policy
        if getattr(args, "combat_mode", "traversal") != "traversal":
            journey_kwargs["combat_mode"] = args.combat_mode
        if getattr(args, "card_id", None):
            journey_kwargs["card_id"] = args.card_id
        return _run_journey_foreground(
            adapter,
            **journey_kwargs,
        )

    progress_path = _get_progress_path()
    use_progress = str(progress_path)  # Enable progress persistence for all runs (AC1)
    resumed_from: str | None = None
    _progress_handled: bool = False  # Set True after failed resume to skip auto-detect

    # --no-resume: clean up old progress, then normal fresh run
    if args.no_resume:
        clear_progress(progress_path)

    # --resume: load pending cases from existing progress file
    if args.resume:
        record = load_progress(progress_path)
        if record is None:
            print("[autotest] WARNING: Progress file missing or corrupted — running full suite.")
            _progress_handled = True  # Degrade to full run (AC4)
        else:
            pending = record.pending_cases
            if not pending:
                print("[autotest] All cases already completed. Nothing to resume.")
                return 0
            print(f"[autotest] Resuming session {record.session_id} — "
                  f"{len(pending)} cases remaining")
            resumed_from = record.session_id
            return _dispatch_orchestrator(
                adapter, pending, timeout=args.timeout,
                progress_path=use_progress, resumed_from=resumed_from,
                use_agent=use_agent,
                run_id=getattr(args, "internal_run_id", None),
            )

    # Auto-detect: progress file exists but no explicit flag
    if progress_path.exists() and not _progress_handled:
        print(
            "[autotest] Found incomplete progress file. "
            "Use --resume to continue or --no-resume to start fresh."
        )
        return 1

    # Normal run paths (all with progress_path set for AC1)
    if args.all:
        # Pipeline mode: review -> compile -> run, only if spec dir available
        pipeline_spec_dir = _resolve_spec_dir(args)
        if pipeline_spec_dir:
            print("[autotest] Running full pipeline: review -> compile -> run")
            from argparse import Namespace
            review_rc = review_cmd(Namespace(
                command="review", spec_dir=pipeline_spec_dir,
                project=getattr(args, "project", None), output=None,
            ))
            if review_rc != 0:
                print("[autotest] Review failed - aborting pipeline")
                return 1
            output_dir = _resolve_output_dir(args, pipeline_spec_dir)
            compile_rc = compile_cmd(Namespace(
                command="compile", spec_dir=pipeline_spec_dir,
                output_dir=output_dir, project=getattr(args, "project", None),
            ))
            if compile_rc != 0:
                print("[autotest] Compile failed - aborting pipeline")
                return 1
            print("[autotest] Running compiled tests through the common runner...")
            pytest_targets = _pytest_targets_for_compiled_specs(
                pipeline_spec_dir,
                output_dir,
            )
            from sts2_autotest.cli.mcp_tools import run_tests_in_dir

            run_result = run_tests_in_dir(
                pipeline_spec_dir,
                timeout=args.timeout,
                targets=[Path(target) for target in pytest_targets],
                output_dir=output_dir,
                run_id=getattr(args, "internal_run_id", None),
            )
            return 0 if run_result.get("status") == "OK" else 1
        print("[autotest] Running all cases (no spec pipeline)...")
        return _dispatch_orchestrator(
            adapter, ["all"], timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
            run_id=getattr(args, "internal_run_id", None),
        )
    elif args.cases:
        print(f"[autotest] Running cases: {', '.join(args.cases)}")
        return _dispatch_orchestrator(
            adapter, args.cases, timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
            run_id=getattr(args, "internal_run_id", None),
        )
    elif args.suite:
        print(f"[autotest] Running suite: {args.suite}")
        return _dispatch_orchestrator(
            adapter, [args.suite], timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
            run_id=getattr(args, "internal_run_id", None),
        )
    elif args.failed:
        print("[autotest] Re-running failed cases...")
        return _dispatch_orchestrator(
            adapter, ["failed"], timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
            run_id=getattr(args, "internal_run_id", None),
        )
    else:
        print("[autotest] No run option specified. "
              "Use --all, --cases, --suite, --failed, or --resume.")
        return 1


def _check_steam_login_state(
    roots: list[Path],
    steam_exe: Path | None,
) -> dict[str, str]:
    """Check Steam remembered login state using local loginusers.vdf."""
    if steam_exe is None:
        return {
            "status": "NOT_FOUND",
            "message": "Steam not found; install Steam and log in before running tests",
        }

    candidates: list[Path] = [steam_exe.parent / "config" / "loginusers.vdf"]
    candidates.extend(root / "config" / "loginusers.vdf" for root in roots)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "AccountName" in content or "MostRecent" in content:
            return {
                "status": "OK",
                "message": f"Steam remembered login found: {candidate}",
            }
        return {
            "status": "FAIL",
            "message": f"Steam login file has no remembered account: {candidate}",
        }

    return {
        "status": "FAIL",
        "message": "Steam login file not found; open Steam and sign in once",
    }


def _check_cli_version(cli_path: str | None) -> dict[str, str]:
    """Check STS2-Cli-Mod version output without requiring the game pipe."""
    if cli_path is None:
        return {
            "status": "NOT_FOUND",
            "message": "sts2 CLI not found; install STS2-Cli-Mod or set STS2_ADAPTER__CLI__CLI_PATH",
        }

    try:
        result = subprocess.run(
            [cli_path, "--version"],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except FileNotFoundError:
        return {
            "status": "NOT_FOUND",
            "message": f"sts2 CLI executable not found: {cli_path}",
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "FAIL",
            "message": f"sts2 --version timed out after 5s: {cli_path}",
        }
    except OSError as exc:
        return {
            "status": "FAIL",
            "message": f"cannot execute sts2 --version: {exc}",
        }

    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    version_output = stdout or stderr
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "message": f"sts2 --version exited {result.returncode}: {version_output[:200]}",
        }

    from sts2_autotest.adapters.cli_mod import CliModAdapter

    try:
        CliModAdapter(cli_path=cli_path, version_output=version_output)
    except Exception as exc:
        return {
            "status": "FAIL",
            "message": f"incompatible sts2 CLI version: {exc}",
        }

    return {
        "status": "OK",
        "message": version_output,
    }


def _check_env() -> dict[str, dict[str, str]]:
    """Run real environment checks and return structured status dict.

    Each entry: {name: {"status": "OK"|"FAIL"|"NOT_FOUND", "message": str}}
    """
    checks: dict[str, dict[str, str]] = {}

    # Mutual exclusion check — both adapters cannot be enabled simultaneously
    agent_enabled = os.environ.get("STS2_ADAPTER__AGENT__ENABLED", "false").lower() in ("true", "1", "yes")
    cli_enabled = os.environ.get("STS2_ADAPTER__CLI__ENABLED", "true").lower() in ("true", "1", "yes")
    if agent_enabled and cli_enabled:
        checks["adapter_mutual_exclusion"] = {
            "status": "FAIL",
            "message": "Mutual exclusion: both CLI and Agent adapters are enabled",
        }
    else:
        checks["adapter_mutual_exclusion"] = {
            "status": "OK",
            "message": "No mutual exclusion conflict",
        }

    # Python version
    pv = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 11):
        checks["python"] = {"status": "OK", "message": pv}
    else:
        checks["python"] = {"status": "FAIL", "message": f"need >=3.11, got {pv}"}

    # Steam installed — check default paths + libraryfolders.vdf
    from sts2_autotest.adapters.discovery import steam_roots
    roots = steam_roots()
    steam_exe = next(
        (r / "steam.exe" for r in roots if (r / "steam.exe").exists()),
        None,
    )
    checks["steam_installed"] = {
        "status": "OK" if steam_exe else "NOT_FOUND",
        "message": str(steam_exe) if steam_exe else "Steam not found",
    }
    checks["steam_login_state"] = _check_steam_login_state(roots, steam_exe)

    # Game installed — scan all Steam library folders
    game_found = _find_game(roots)
    checks["game_installed"] = {
        "status": "OK" if game_found else "NOT_FOUND",
        "message": str(game_found) if game_found else "Game not found",
    }

    # sts2 CLI tool
    from sts2_autotest.adapters.discovery import discover_sts2_cli
    cli_path = discover_sts2_cli()
    checks["sts2_cli_mod"] = {
        "status": "OK" if cli_path else "NOT_FOUND",
        "message": str(cli_path) if cli_path else "sts2 CLI not found",
    }
    checks["sts2_cli_version"] = _check_cli_version(cli_path)

    # Disk space (C: drive)
    try:
        usage = shutil.disk_usage("C:/")
        free_gb = usage.free // (1024**3)
        checks["disk_space"] = {
            "status": "OK",
            "message": f"{free_gb} GB free (threshold: 100 MB)",
        }
    except OSError:
        checks["disk_space"] = {"status": "FAIL", "message": "cannot check disk usage"}

    # Screenshot directory writable — use os.access for a pure-stat check
    ss_dir = Path(DEFAULT_EVIDENCE_DIR) / "screenshots"
    try:
        ss_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(str(ss_dir), os.W_OK)
        checks["screenshot_dir_writable"] = {
            "status": "OK" if writable else "FAIL",
            "message": "writable" if writable else "not writable",
        }
    except OSError:
        checks["screenshot_dir_writable"] = {"status": "FAIL", "message": "cannot create directory"}

    # Session lock check
    try:
        from sts2_autotest.core.lock_manager import LockManager
        lock_path = Path(DEFAULT_EVIDENCE_DIR) / ".sts2-autotest.lock"
        lm = LockManager(str(lock_path))
        locked = lm.is_locked()
        checks["session_locked"] = {
            "status": "OK" if not locked else "FAIL",
            "message": "lock not held" if not locked else "another session is running",
        }
    except Exception:
        checks["session_locked"] = {"status": "FAIL", "message": "cannot check lock"}

    return checks


def agent_test_cmd(args: Any) -> int:
    """Run the full test-agent workflow (build + localization + deploy + smoke).

    Cross-platform replacement for run-test-agent.ps1.  Supports reading
    parameters from a test-plan YAML file with CLI overrides taking precedence.
    """
    from sts2_autotest.core.test_agent_runner import (
        TestAgentRunner,
        TestPlanConfig,
        _load_test_plan,
        _merge_config,
    )

    cli_cfg = TestPlanConfig(
        task_id=args.task_id,
        mod_project=args.mod_project,
        mod_name=getattr(args, "mod_name", "") or "",
        infra_path=args.infra_path,
        test_plan_path=getattr(args, "test_plan", "") or "",
        game_mods_path=getattr(args, "game_mods_path", "") or "",
        steam_app_id=getattr(args, "steam_app_id", "2868840"),
        ping_timeout_seconds=getattr(args, "ping_timeout", 90),
        skip_deploy=getattr(args, "skip_deploy", False),
        skip_launch_game=getattr(args, "skip_launch_game", False),
        skip_game_smoke=getattr(args, "skip_game_smoke", False),
    )

    # C2: merge test-plan YAML if provided
    plan = None
    if cli_cfg.test_plan_path:
        from pathlib import Path as _Path
        plan = _load_test_plan(_Path(cli_cfg.test_plan_path))
        if plan:
            print(f"[agent-test] Loaded test plan: {cli_cfg.test_plan_path}")
    cfg = _merge_config(cli_cfg, plan)

    # Issue 2: resolve require_game_running — CLI flag > plan > default
    cli_require = getattr(args, "require_game_running", None)
    if cli_require is not None:
        require_game = cli_require
    else:
        require_game = cfg.require_game_running

    runner = TestAgentRunner(
        mod_project=cfg.mod_project,
        task_id=cfg.task_id,
        infra_path=cfg.infra_path,
        mod_name=cfg.mod_name,
        test_plan_path=cfg.test_plan_path,
        game_mods_path=cfg.game_mods_path,
        steam_app_id=cfg.steam_app_id,
        ping_timeout_seconds=cfg.ping_timeout_seconds,
        skip_deploy=cfg.skip_deploy,
        skip_launch_game=cfg.skip_launch_game,
        skip_game_smoke=cfg.skip_game_smoke,
        require_game_running=require_game,
        character_ids=cfg.character_ids,
        character_names=cfg.character_names,
        starter_relic=cfg.starter_relic,
        starter_cards=cfg.starter_cards,
        localization_key_prefixes=cfg.localization_key_prefixes,
    )

    result = runner.run()
    conclusion = result.conclusion
    print(f"\n[agent-test] {conclusion}")
    for r in result.results:
        print(f"  [{r.status}] {r.name}")
    if result.failure_details:
        print(f"\n[agent-test] Failure: {result.failure_details[:300]}")
    if result.blocked_details:
        print(f"\n[agent-test] Blocked: {result.blocked_details[:300]}")
    print(f"[agent-test] Report: {result.artifact_dir}/test-report.md")
    return result.exit_code


def serve_cmd(args: Any) -> int:
    """Start the health check HTTP server."""
    from sts2_autotest.cli.health_server import serve_cmd as _serve
    return _serve(args)


def serve_mcp_cmd(args: Any) -> int:
    """Start the MCP test service (B11 Phase 2)."""
    return mcp_server.serve_cmd(args)


def doctor_cmd(args: Any) -> int:
    """Check environment readiness with real checks."""
    checks = _check_env()

    # Agent endpoint check when enabled via env var
    if _is_agent_default():
        from sts2_autotest.adapters.agent import AgentAdapter

        agent_endpoint = os.environ.get(
            "STS2_ADAPTER__AGENT__ENDPOINT", "http://127.0.0.1:8080"
        )

        async def _probe_agent() -> bool:
            adapter = AgentAdapter(endpoint=agent_endpoint, timeout=5.0)
            try:
                health = await adapter.health_check()
                return health.healthy
            except Exception:
                return False
            finally:
                await adapter.cleanup()

        agent_healthy = asyncio.run(_probe_agent())
        checks["sts2_agent"] = {
            "status": "OK" if agent_healthy else "FAIL",
            "message": (
                f"Agent endpoint: {agent_endpoint}"
                if agent_healthy
                else "Agent not responding"
            ),
        }

    has_failure = any(v["status"] != "OK" for v in checks.values())

    if args.ci:
        failed_checks = [k for k, v in checks.items() if v["status"] != "OK"]
        print(json.dumps({"healthy": not has_failure, "failed_checks": failed_checks}))
        return 1 if has_failure else 0

    if args.json:
        print(json.dumps(checks, indent=2, ensure_ascii=False))
    else:
        for k, v in checks.items():
            if v["status"] == "OK":
                status_marker = "[OK]"
            elif v["status"] == "FAIL":
                status_marker = "[FAIL]"
            else:
                status_marker = "[WARN]"
            print(f"  {status_marker} {k}: {v['status']} - {v['message']}")
    return 1 if has_failure else 0


def _report_from_store(evidence_dir: Path, run_id: str, store_run: Path) -> int:
    """从 RunStore 任务记录（.runs/{run_id}/run.json）合成报告。

    detach 任务的顶层 summary.json / reports/ 缺失时回退到此路径：把 run.json
    的状态与结果落盘为 tests/output/{run_id}/reports/run-result.json，使公共契约
    的「四处一致」对 .runs 任务同样可验证。
    """
    try:
        rec = json.loads(store_run.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[autotest] Failed to read run record {store_run}: {exc}")
        return 1
    status = rec.get("status")
    result = rec.get("result") or {}
    # 由压缩包路径派生证据包地址（真实存在，可核验），但不臆造运行期字段。
    artifact_candidates = sorted(
        path for path in (evidence_dir / "artifacts").glob(f"{run_id}_*.zip")
        if path.is_file()
    ) if (evidence_dir / "artifacts").is_dir() else []
    artifact_path = str(artifact_candidates[-1].resolve()) if artifact_candidates else None
    payload: dict[str, Any] = {
        "run_id": run_id,
        "task_id": run_id,
        "status": status,
        # 运行期字段无法从任务记录重建：防睡眠仅在真实运行时记录，此处如实置空，
        # 并显式标注这是历史恢复报告，绝不可当作最终六操作验收报告。
        "anti_sleep_started": None,
        "anti_sleep_note": "not recorded in run store; only available from a real run",
        "artifact_path": artifact_path,
        "evidence_pack_url": artifact_path,
        "source": "synthesized_from_run_store",
        "historical_recovery": True,
    }
    if isinstance(result, dict):
        for key in ("duration_ms", "status_trajectory", "pre_cancel_state",
                    "pre_cancel_screen", "recovered_screen", "last_action",
                    "evidence_dir", "reason"):
            if key in result:
                payload[key] = result[key]
    out_dir = evidence_dir / run_id / "reports"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "run-result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[autotest] Failed to persist synthesized run-result.json: {exc}")
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def report_cmd(args: Any) -> int:
    """Show test run summary from evidence directory."""
    evidence_dir = Path(args.evidence_dir)
    run_id = args.run_id or "latest"

    if getattr(args, "coverage", False):
        coverage_path = evidence_dir / run_id / "reports" / "scene-coverage.md"
        if not coverage_path.is_file():
            print(
                "[autotest] Scene coverage report not found: "
                f"{coverage_path}"
            )
            print("[autotest] Generate it with EvidencePackager.write_scene_coverage_report().")
            return 1
        try:
            print(coverage_path.read_text(encoding="utf-8"))
            return 0
        except OSError as exc:
            print(f"[autotest] Failed to read coverage report: {exc}")
            return 1

    summary_path = evidence_dir / run_id / "summary.json"
    # 兼容 RunStore 布局：detach 任务状态存于 .runs/{run_id}/run.json，
    # 顶层 summary.json / reports/ 可能不存在。回退顺序：
    #   summary.json → run-result.json → .runs 任务记录（合成并落盘）→ 列可用任务
    # 确保 .runs 任务的「四处一致」报告契约同样可验证。
    run_result_path = evidence_dir / run_id / "reports" / "run-result.json"
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            print(json.dumps(data, indent=2))
            return 0
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[autotest] Failed to read report: {exc}")
            return 1
    if run_result_path.exists():
        try:
            data = json.loads(run_result_path.read_text(encoding="utf-8"))
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[autotest] Failed to read run-result.json: {exc}")
            # 继续回退到 .runs 合成
    store_run = evidence_dir / ".runs" / run_id / "run.json"
    if store_run.is_file():
        return _report_from_store(evidence_dir, run_id, store_run)
    # Try listing available runs（含 .runs 内任务）
    if evidence_dir.exists():
        top = sorted(p.name for p in evidence_dir.iterdir() if p.is_dir())
        store = (
            sorted(p.name for p in (evidence_dir / ".runs").iterdir() if p.is_dir())
            if (evidence_dir / ".runs").is_dir()
            else []
        )
        runs = sorted(set(top) | set(store))
        if runs:
            print(f"[autotest] Run '{run_id}' not found. Available runs:")
            for r in runs:
                print(f"  - {r}")
        else:
            print(f"[autotest] No runs found in {evidence_dir}")
    else:
        print(f"[autotest] Evidence directory not found: {evidence_dir}")
    return 1



def _resolve_gen_report_dirs(args: Any) -> tuple[str, str]:
    """Resolve config and output paths for gen-report."""
    config_path = args.config
    output_path = args.output

    if config_path and output_path:
        return config_path, output_path

    if args.task_id:
        base = Path(args.mod_project) / "automation" / "autotest" / "output" / args.task_id
        resolved_config = base / "test-results.json"
        if not resolved_config.exists():
            resolved_config = base / "test-results.json"
        config_path = str(resolved_config) if config_path is None else config_path
        output_path = output_path or str(base / "test-report.html")
        return config_path or str(resolved_config), output_path

    print("[autotest] Specify --task-id or --config")
    return "", ""


def gen_report_cmd(args: Any) -> int:
    """Generate HTML test report from a structured AUTOTEST JSON config."""
    config_path, output_path = _resolve_gen_report_dirs(args)
    if not config_path or not output_path:
        return 1

    if not os.path.isfile(config_path):
        print(f"[autotest] Config file not found: {config_path}")
        return 1

    try:
        write_html_report(config_path, output_path)
        print(f"[autotest] HTML test report generated: {output_path}")
        return 0
    except Exception as exc:
        print(f"[autotest] Report generation failed: {exc}")
        return 1


def _build_visual_qa_engine(args: Any) -> VisualQaEngine:
    provider: OcrProvider
    if args.ocr_provider == "tesseract":
        provider = TesseractOcrProvider(
            command=args.tesseract_cmd,
            lang=args.lang,
            timeout_seconds=args.timeout_seconds,
        )
    else:
        provider = DisabledOcrProvider()

    health_detector = ScreenshotHealthDetector(
        cv2_module="auto" if args.health_provider == "opencv" else None,
        low_variance_threshold=args.low_variance_threshold,
        low_brightness_threshold=args.low_brightness_threshold,
        high_brightness_threshold=args.high_brightness_threshold,
    )
    return VisualQaEngine(provider, health_detector=health_detector)


def _write_json_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_suffix(path.suffix + ".tmp")
    tmp_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(str(tmp_file), str(path))


def visual_qa_cmd(args: Any) -> int:
    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"[autotest] Image file not found: {image_path}")
        return 1

    engine = _build_visual_qa_engine(args)
    analysis = engine.analyze_screenshot(image_path)
    payload = build_visual_qa_payload(
        test_run_id=image_path.name,
        analyses_by_path={str(image_path): analysis},
    )
    if args.output:
        _write_json_output(Path(args.output), payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0

def cli(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point. Used by pyproject.toml [project.scripts]."""
    # Ensure UTF-8 output in Windows terminal (fix GBK encoding issues)
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[union-attr]
    except Exception:
        pass
    parser = _create_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        sys.exit(run_cmd(args))
    elif args.command == "review":
        sys.exit(review_cmd(args))
    elif args.command == "compile":
        sys.exit(compile_cmd(args))
    elif args.command == "doctor":
        sys.exit(doctor_cmd(args))
    elif args.command == "report":
        sys.exit(report_cmd(args))
    elif args.command == "queue":
        sys.exit(queue_cmd(args))
    elif args.command == "status":
        sys.exit(status_cmd(args))
    elif args.command == "capabilities":
        sys.exit(capabilities_cmd(args))
    elif args.command == "cancel":
        sys.exit(cancel_cmd(args))
    elif args.command == "resume":
        sys.exit(resume_run_cmd(args))
    elif args.command == "progress":
        sys.exit(progress_cmd(args))
    elif args.command == "agent-test":
        sys.exit(agent_test_cmd(args))
    elif args.command == "serve":
        sys.exit(serve_cmd(args))
    elif args.command == "gen-report":
        sys.exit(gen_report_cmd(args))
    elif args.command == "visual-qa":
        sys.exit(visual_qa_cmd(args))
    elif args.command == "serve-mcp":
        sys.exit(serve_mcp_cmd(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()
