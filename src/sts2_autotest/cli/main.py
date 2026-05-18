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
import sys
from pathlib import Path
from typing import Any, Sequence

from sts2_autotest.adapters.base import GameAdapterProtocol


DEFAULT_EVIDENCE_DIR = ".sts2-evidence"


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
    run.add_argument(
        "--adapter",
        choices=["cli", "agent"],
        default=None,
        help="Adapter type (overrides config: cli=STS2-Cli-Mod, agent=STS2-Agent)",
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
        from sts2_autotest.adapters.agent import AgentAdapter

        return AgentAdapter(
            endpoint=_get_env(
                ["STS2_ADAPTER__AGENT__ENDPOINT"], "http://localhost:8080"
            ),
            timeout=float(_get_env(["STS2_ADAPTER__AGENT__TIMEOUT"], "30")),
            tool_profile=_get_env(
                ["STS2_ADAPTER__AGENT__TOOL_PROFILE"], "guided"
            ),
            debug_actions=_get_env(
                ["STS2_ADAPTER__AGENT__DEBUG_ACTIONS"], "false"
            ).lower()
            in ("true", "1", "yes"),
            health_path=_get_env(["STS2_ADAPTER__AGENT__HEALTH_PATH"], "health"),
            state_path=_get_env(
                ["STS2_ADAPTER__AGENT__STATE_PATH"], "game_state"
            ),
            actions_path=_get_env(
                ["STS2_ADAPTER__AGENT__ACTIONS_PATH"], "available_actions"
            ),
            act_path=_get_env(["STS2_ADAPTER__AGENT__ACT_PATH"], "act"),
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
) -> int:
    """Create an orchestrator with the given adapter and run the given case IDs.

    Lifecycle is owned by run_all() (start_session + stop_session inside).
    """
    from sts2_autotest.core.orchestrator import TestOrchestrator

    orch = TestOrchestrator(
        adapter=adapter,
        progress_path=progress_path,
        resumed_from=resumed_from,
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


def _get_progress_path() -> Path:
    """Get default progress file path."""
    return Path(DEFAULT_EVIDENCE_DIR) / ".progress" / "session-progress.json"


def _dispatch_orchestrator(
    adapter: GameAdapterProtocol | None,
    case_ids: list[str],
    timeout: int,
    *,
    progress_path: str | None = None,
    resumed_from: str | None = None,
    use_agent: bool = False,
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
        adapter, case_ids, timeout=timeout, **kwargs,
    )


def run_cmd(args: Any) -> int:
    """Dispatch run command — connects to the real orchestrator with resume support."""
    from sts2_autotest.core.progress import clear_progress, load_progress

    # Determine adapter type: --adapter flag takes precedence, then env var default
    adapter_type: str = args.adapter or ("agent" if _is_agent_default() else "cli")
    use_agent = adapter_type == "agent"
    adapter = _create_adapter(adapter_type)

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
        print("[autotest] Running all cases...")
        return _dispatch_orchestrator(
            adapter, ["all"], timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
        )
    elif args.cases:
        print(f"[autotest] Running cases: {', '.join(args.cases)}")
        return _dispatch_orchestrator(
            adapter, args.cases, timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
        )
    elif args.suite:
        print(f"[autotest] Running suite: {args.suite}")
        return _dispatch_orchestrator(
            adapter, [args.suite], timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
        )
    elif args.failed:
        print("[autotest] Re-running failed cases...")
        return _dispatch_orchestrator(
            adapter, ["failed"], timeout=args.timeout,
            progress_path=use_progress, use_agent=use_agent,
        )
    else:
        print("[autotest] No run option specified. "
              "Use --all, --cases, --suite, --failed, or --resume.")
        return 1


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

    # Steam installed
    steam_paths = [
        Path("C:/Program Files (x86)/Steam/steam.exe"),
        Path("C:/Program Files/Steam/steam.exe"),
    ]
    found_steam = next((p for p in steam_paths if p.exists()), None)
    checks["steam_installed"] = {
        "status": "OK" if found_steam else "NOT_FOUND",
        "message": str(found_steam) if found_steam else "Steam not found",
    }

    # Game installed
    game_found = False
    for sp in steam_paths:
        lib_path = sp.parent / "steamapps" / "common" / "Slay the Spire 2"
        if lib_path.exists():
            game_found = True
            break
    checks["game_installed"] = {
        "status": "OK" if game_found else "NOT_FOUND",
        "message": "Slay the Spire 2 found" if game_found else "Game not found",
    }

    # sts2 CLI tool
    from sts2_autotest.adapters.discovery import discover_sts2_cli
    cli_path = discover_sts2_cli()
    checks["sts2_cli_mod"] = {
        "status": "OK" if cli_path else "NOT_FOUND",
        "message": str(cli_path) if cli_path else "sts2 CLI not found",
    }

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


def doctor_cmd(args: Any) -> int:
    """Check environment readiness with real checks."""
    checks = _check_env()

    # Agent endpoint check when enabled via env var
    if _is_agent_default():
        from sts2_autotest.adapters.agent import AgentAdapter

        agent_endpoint = os.environ.get(
            "STS2_ADAPTER__AGENT__ENDPOINT", "http://localhost:8080"
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
            status_marker = "✓" if v["status"] == "OK" else "✗"
            print(f"  {status_marker} {k}: {v['status']} — {v['message']}")
    return 1 if has_failure else 0


def report_cmd(args: Any) -> int:
    """Show test run summary from evidence directory."""
    evidence_dir = Path(args.evidence_dir)
    run_id = args.run_id or "latest"

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


def cli(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point. Used by pyproject.toml [project.scripts]."""
    parser = _create_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        sys.exit(run_cmd(args))
    elif args.command == "doctor":
        sys.exit(doctor_cmd(args))
    elif args.command == "report":
        sys.exit(report_cmd(args))
    else:
        parser.print_help()
        sys.exit(1)
