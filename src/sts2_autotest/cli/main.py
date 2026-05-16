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

    # autotest review
    review = sub.add_parser("review", help="Review natural language test specs")
    review.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    review.add_argument("--project", help="Project name from workspace config")
    review.add_argument("--output", help="Output path for review report (default: stdout)")

    # autotest compile
    compile_cmd_parser = sub.add_parser("compile", help="Compile specs to pytest test files")
    compile_cmd_parser.add_argument("--spec-dir", help="Directory containing Markdown spec files")
    compile_cmd_parser.add_argument("--output-dir", help="Directory for generated test files")
    compile_cmd_parser.add_argument("--project", help="Project name from workspace config")

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


def _run_orchestrator(
    case_ids: list[str],
    timeout: int,
    *,
    progress_path: str | None = None,
    resumed_from: str | None = None,
) -> int:
    """Create an orchestrator and run the given case IDs.

    Lifecycle is owned by run_all() (start_session + stop_session inside).
    """
    from sts2_autotest.adapters.cli_mod import CliModAdapter
    from sts2_autotest.core.orchestrator import TestOrchestrator

    adapter = CliModAdapter()
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
    if getattr(args, "spec_dir", None):
        return args.spec_dir
    project_name = getattr(args, "project", None)
    if project_name:
        ws = _load_workspace()
        if ws:
            project = ws.resolve_project(project_name)
            if project:
                return project.spec_dir
    return None


def _resolve_output_dir(args: Any, spec_dir: str) -> str:
    """Resolve output directory for generated test files."""
    if getattr(args, "output_dir", None):
        return args.output_dir
    project_name = getattr(args, "project", None)
    if project_name:
        ws = _load_workspace()
        if ws:
            project = ws.resolve_project(project_name)
            if project and project.output_dir:
                return project.output_dir
    return spec_dir


def review_cmd(args: Any) -> int:
    """Review natural language test specs and print report."""
    from sts2_autotest.core.workspace import Workspace
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

    print(f"[autotest] Reviewing {len(cases)} case(s), {len(suites)} suite(s) in {spec_dir}\n")

    for spec in cases:
        report = reviewer.review(spec)
        status = "PASS" if report.passed else "ISSUES"
        print(f"  [{status}] {spec.id}: {spec.title}")
        if not report.passed:
            all_passed = False
            for issue in report.issues:
                print(f"         - [{issue.category.value}] {issue.location}: {issue.description}")
        draft = reviewer.generate_revised_draft(spec, report)
        if draft.changes_summary and draft.changes_summary != ["No issues found — spec is already clean"]:
            for change in draft.changes_summary:
                print(f"           draft: {change}")

    for suite in suites:
        report = reviewer.review_suite(suite)
        status = "PASS" if report.passed else "ISSUES"
        print(f"  [{status}] {suite.id}: {suite.title}")
        if not report.passed:
            for issue in report.issues:
                print(f"         - [{issue.category.value}] {issue.location}: {issue.description}")

    print(f"\n[autotest] Review complete. {'All passed' if all_passed else 'Some issues found'}.")
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

    output_dir = _resolve_output_dir(args, spec_dir)
    parser = MarkdownParser()
    cases, suites = parser.discover_specs(spec_dir)

    if not cases and not suites:
        print(f"[autotest] No spec files found in {spec_dir}")
        return 0

    generator = CodeGenerator()
    generated: list[str] = []

    for spec in cases:
        out_path = generator.generate_to_file(spec, output_dir)
        generated.append(out_path)
        print(f"  [GENERATED] {out_path}")

    print(f"\n[autotest] Generated {len(generated)} test file(s) in {output_dir}")
    return 0


def run_cmd(args: Any) -> int:
    """Dispatch run command — connects to the real orchestrator with resume support."""
    from sts2_autotest.core.progress import clear_progress, load_progress

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
            return _run_orchestrator(
                pending, timeout=args.timeout,
                progress_path=use_progress, resumed_from=resumed_from,
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
            print("[autotest] Running compiled tests...")
        else:
            print("[autotest] Running all cases (no spec pipeline)...")
        return _run_orchestrator(
            ["all"], timeout=args.timeout,
            progress_path=use_progress,
        )
    elif args.cases:
        print(f"[autotest] Running cases: {', '.join(args.cases)}")
        return _run_orchestrator(
            args.cases, timeout=args.timeout,
            progress_path=use_progress,
        )
    elif args.suite:
        print(f"[autotest] Running suite: {args.suite}")
        return _run_orchestrator(
            [args.suite], timeout=args.timeout,
            progress_path=use_progress,
        )
    elif args.failed:
        print("[autotest] Re-running failed cases...")
        return _run_orchestrator(
            ["failed"], timeout=args.timeout,
            progress_path=use_progress,
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
    else:
        parser.print_help()
        sys.exit(1)
