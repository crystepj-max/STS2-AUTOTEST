"""CLI entry points for STS2-AUTOTEST (FR1, FR2, FR24, FR60-62).

Commands: autotest run, autotest doctor, autotest report.
MVP uses minimal argparse (no click) to avoid extra dependencies.
Full click integration deferred to Beta if needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import subprocess
import sys
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
            "goal_scene", "act_traversal",
        ],
        help="Run one reusable game journey instead of a project case suite",
    )
    run.add_argument(
        "--character-id",
        default="IRONCLAD",
        help="Character for new_run/first_battle",
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
        choices=["traversal", "basic"],
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
    agent_test.add_argument("--task-id", required=True, help="Task identifier (e.g. gawain-localization-key-fix)")
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
    gen_report_parser.add_argument("--task-id", help="Task ID (reads from automation/autotest/output/{task-id}/)")
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


def _create_adapter(adapter_type: str) -> GameAdapterProtocol:
    """Create adapter based on type.

    Reads configuration from STS2_ prefixed environment variables,
    mirroring the config/loader.py convention without importing config.

    Args:
        adapter_type: "cli" or "agent" — which adapter to instantiate.

    Returns:
        A GameAdapterProtocol-compliant adapter instance.
    """
    if adapter_type == "agent":
        from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient

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

    generator = CodeGenerator()
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

    payload = serialize_record(_run_store().load(args.run_id))
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if payload.get("status") != "NOT_FOUND" else 1


def cancel_cmd(args: Any) -> int:
    from sts2_autotest.core.run_service import serialize_record

    payload = serialize_record(_run_store().request_cancel(args.run_id))
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") != "NOT_FOUND" else 1


def resume_run_cmd(args: Any) -> int:
    from sts2_autotest.core.run_service import RunRequest

    store = _run_store()
    old = store.load(args.run_id)
    if old is None:
        print(json.dumps({"status": "NOT_FOUND", "run_id": args.run_id}))
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
            result=result_payload,
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


def _write_journey_evidence(
    evidence_root: Path,
    run_id: str | None,
    *,
    journey: str,
    target_scene: str,
    evidence: dict[str, Any],
) -> None:
    """把通用旅程的场景、操作、地图和证据清单写成机器可读文件。"""
    if not run_id:
        return
    report_dir = evidence_root / run_id / "reports"
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        trace = {
            "journey": journey,
            "target_scene": target_scene,
            **evidence,
        }
        (report_dir / "journey-trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        root = evidence_root / run_id
        screenshot_count = len(list((root / "screenshots").glob("*"))) if (root / "screenshots").is_dir() else 0
        log_count = len(list((root / "logs").glob("*"))) if (root / "logs").is_dir() else 0
        files = [
            str(path.relative_to(root))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        (report_dir / "evidence-manifest.json").write_text(
            json.dumps(
                {
                    "declared": [
                        "reports/run-result.json",
                        "reports/journey-trace.json",
                        "reports/evidence-manifest.json",
                        "summary.json",
                        "summary.md",
                        "screenshots/",
                        "logs/",
                    ],
                    "existing_files": files,
                    "evidence_level": os.environ.get("STS2_AUTOTEST_EVIDENCE", "full"),
                    "screenshot_count": screenshot_count,
                    "log_count": log_count,
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
) -> int:
    """执行平台提供的通用游戏旅程。"""
    from sts2_autotest.common.errors import STS2Error
    from sts2_autotest.core.journeys import GenericJourneys, JourneyFailure, _extract_chapter
    from sts2_autotest.core.evidence_hooks import build_evidence_hooks
    from sts2_autotest.core.action_model import TestResult

    case_id = f"journey:{journey}"
    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", DEFAULT_EVIDENCE_DIR)
    )
    evidence = build_evidence_hooks(evidence_root, pack_id=run_id)
    evidence.on_case_start(case_id)

    def capture_key_state(state: dict[str, Any]) -> None:
        screen = str(state.get("screen") or "").upper()
        chapter = _extract_chapter(state)
        if screen in {"EVENT", "COMBAT", "CARD_REWARD"} or (
            screen == "MAP" and chapter == 2
        ):
            capture_state = getattr(evidence, "capture_state", None)
            if callable(capture_state):
                safe_case_id = case_id.replace(":", "_")
                capture_state(
                    f"{safe_case_id}_{screen}_{int(time.monotonic() * 1000)}",
                    state,
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
            (result_dir / "run-result.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

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
        runner = GenericJourneys(
            adapter,
            timeout=timeout,
            target_scene=resolved_target,
            route_policy=route_policy,
            combat_mode=combat_mode,
            progress_callback=publish_progress,
            observation_callback=capture_key_state,
        )
        if journey == "new_run":
            result = loop.run_until_complete(runner.start_new_run(character_id))
        elif journey == "resume_run":
            result = loop.run_until_complete(runner.resume_run())
        elif journey == "first_battle":
            result = loop.run_until_complete(runner.enter_first_battle(character_id=character_id))
        elif journey in {"goal_scene", "act_traversal"} or target_scene:
            result = loop.run_until_complete(
                runner.execute_target(
                    character_id=character_id,
                    target_scene=resolved_target,
                    route_policy=route_policy,
                    combat_mode=combat_mode,
                )
            )
        else:
            result = loop.run_until_complete(runner.finish_interstitials())

        duration_ms = int((time.monotonic() - started) * 1000)
        trajectory = list(runner.trajectory)
        evidence.on_case_end(TestResult(case_id, "pass", json.dumps(result, ensure_ascii=False)))
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=runner.evidence,
        )
        write_result(
            "PASSED",
            extra={
                "duration_ms": duration_ms,
                "status_trajectory": trajectory,
                "final_state": result.get("screen"),
                "target_scene": resolved_target,
                "journey_evidence": runner.evidence,
                "evidence_dir": str(evidence_root / run_id) if run_id else None,
            },
        )
        evidence.on_session_end({
            "total": 1, "passed": 1, "failed": 0, "crashed": 0, "skipped": 0,
            "duration_ms": duration_ms,
        })
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=runner.evidence,
        )
        print(json.dumps({"journey": journey, "status": "PASSED", "state": result}, ensure_ascii=False))
        return 0
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
        evidence.on_crash(case_id, exc)
        evidence.on_case_end(TestResult(case_id, "crash", str(exc)))
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=runner.evidence if runner is not None else {},
        )
        write_result(
            status,
            message=str(exc),
            extra={
                "duration_ms": duration_ms,
                "status_trajectory": trajectory,
                "failure": failure,
                "target_scene": resolved_target,
                "journey_evidence": runner.evidence if runner is not None else {},
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
        _write_journey_evidence(
            evidence_root,
            run_id,
            journey=journey,
            target_scene=resolved_target,
            evidence=runner.evidence if runner is not None else {},
        )
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


def _run_cmd_foreground(args: Any) -> int:
    """Dispatch run command — connects to the real orchestrator with resume support."""
    from sts2_autotest.core.progress import clear_progress, load_progress

    evidence_mode = getattr(args, "evidence", None)
    if evidence_mode:
        os.environ["STS2_AUTOTEST_EVIDENCE"] = str(evidence_mode)

    # Determine adapter type: --adapter flag takes precedence, then env var default
    adapter_type: str = args.adapter or ("agent" if _is_agent_default() else "cli")
    use_agent = adapter_type == "agent"
    adapter = _create_adapter(adapter_type)

    if getattr(args, "journey", None) or getattr(args, "target_scene", None):
        journey_kwargs: dict[str, Any] = {
            "journey": args.journey,
            "character_id": args.character_id,
            "timeout": float(args.timeout),
            "run_id": getattr(args, "internal_run_id", None),
        }
        if getattr(args, "target_scene", None) is not None:
            journey_kwargs["target_scene"] = args.target_scene
        if getattr(args, "route_policy", "leftmost") != "leftmost":
            journey_kwargs["route_policy"] = args.route_policy
        if getattr(args, "combat_mode", "traversal") != "traversal":
            journey_kwargs["combat_mode"] = args.combat_mode
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
    if not summary_path.exists():
        # Try listing available runs
        if evidence_dir.exists():
            runs = sorted(p.name for p in evidence_dir.iterdir() if p.is_dir())
            if runs:
                print(f"[autotest] Run '{run_id}' not found. Available runs:")
                for r in runs:
                    print(f"  - {r}")
            else:
                print(f"[autotest] No runs found in {evidence_dir}")
        else:
            print(f"[autotest] Evidence directory not found: {evidence_dir}")
        return 1

    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        print(json.dumps(data, indent=2))
        return 0
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[autotest] Failed to read report: {exc}")
        return 1



def _resolve_gen_report_dirs(args: Any) -> tuple[str, str]:
    """Resolve config and output paths for gen-report."""
    config_path = args.config
    output_path = args.output

    if config_path and output_path:
        return config_path, output_path

    if args.task_id:
        base = Path("STS2-GAWAIN") / "automation" / "autotest" / "output" / args.task_id
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
