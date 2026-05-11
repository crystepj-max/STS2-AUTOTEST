"""CLI entry points for STS2-AUTOTEST (FR1, FR2, FR24, FR60-62).

Commands: autotest run, autotest doctor, autotest report.
MVP uses minimal argparse (no click) to avoid extra dependencies.
Full click integration deferred to Beta if needed.
"""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence


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
    run.add_argument("--timeout", type=int, default=30, help="Case timeout (seconds)")

    # autotest doctor
    doc = sub.add_parser("doctor", help="Check environment readiness")
    doc.add_argument("--json", action="store_true", help="Output as JSON")

    # autotest report
    rep = sub.add_parser("report", help="Show test run summary")
    rep.add_argument("run_id", nargs="?", help="Run ID to report on")
    rep.add_argument(
        "--evidence-dir",
        default=DEFAULT_EVIDENCE_DIR,
        help="Evidence directory path",
    )

    return p


def _run_orchestrator(case_ids: list[str], timeout: int) -> int:
    """Create an orchestrator and run the given case IDs."""
    from sts2_autotest.adapters.cli_mod import CliModAdapter
    from sts2_autotest.core.orchestrator import TestOrchestrator

    adapter = CliModAdapter()
    orch = TestOrchestrator(adapter=adapter)

    loop = asyncio.new_event_loop()
    try:
        ok = loop.run_until_complete(orch.start_session())
        if not ok:
            print("[autotest] ERROR: Failed to start session — adapter not ready.")
            return 1
        summary = loop.run_until_complete(orch.run_all(case_ids))
        print(f"[autotest] {summary.passed} passed, {summary.failed} failed, "
              f"{summary.crashed} crashed, {summary.skipped} skipped")
        return 1 if summary.is_failed else 0
    finally:
        loop.run_until_complete(orch.stop_session())
        loop.close()


def run_cmd(args: Any) -> int:
    """Dispatch run command — connects to the real orchestrator."""
    if args.all:
        # MVP: run a placeholder case list; real case discovery is in config/schema
        print("[autotest] Running all cases...")
        return _run_orchestrator(["all"], timeout=args.timeout)
    elif args.cases:
        print(f"[autotest] Running cases: {', '.join(args.cases)}")
        return _run_orchestrator(args.cases, timeout=args.timeout)
    elif args.suite:
        # Suite resolution deferred to config system
        print(f"[autotest] Running suite: {args.suite}")
        return _run_orchestrator([args.suite], timeout=args.timeout)
    elif args.failed:
        print("[autotest] Re-running failed cases...")
        return _run_orchestrator(["failed"], timeout=args.timeout)
    elif args.resume:
        print("[autotest] Resuming from last run...")
        return _run_orchestrator(["resume"], timeout=args.timeout)
    else:
        print("[autotest] No run option specified. Use --all, --cases, --suite, --failed, or --resume.")
        return 1


def _check_env() -> dict[str, str]:
    """Run real environment checks and return status dict."""
    checks: dict[str, str] = {}

    # Python version
    pv = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks["python"] = "OK" if sys.version_info >= (3, 11) else f"FAIL (need >=3.11, got {pv})"

    # Steam installed
    steam_paths = [
        Path("C:/Program Files (x86)/Steam/steam.exe"),
        Path("C:/Program Files/Steam/steam.exe"),
    ]
    checks["steam_installed"] = "OK" if any(p.exists() for p in steam_paths) else "NOT FOUND"

    # Game installed — check common Slay the Spire 2 paths
    game_found = False
    for sp in steam_paths:
        lib_path = sp.parent / "steamapps" / "common" / "Slay the Spire 2"
        if lib_path.exists():
            game_found = True
            break
    checks["game_installed"] = "OK" if game_found else "NOT FOUND"

    # sts2 CLI tool
    checks["sts2_cli_mod"] = "OK" if shutil.which("sts2") else "NOT FOUND"

    # Disk space (C: drive)
    try:
        usage = shutil.disk_usage("C:/")
        free_gb = usage.free // (1024**3)
        checks["disk_space"] = f"OK ({free_gb} GB free)"
    except OSError:
        checks["disk_space"] = "UNKNOWN"

    return checks


def doctor_cmd(args: Any) -> int:
    """Check environment readiness with real checks."""
    checks = _check_env()
    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        for k, v in checks.items():
            status_marker = "✓" if v == "OK" or v.startswith("OK") else "✗"
            print(f"  {status_marker} {k}: {v}")
    has_failure = any(v.startswith("FAIL") for v in checks.values())
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
