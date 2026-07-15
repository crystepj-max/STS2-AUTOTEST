"""MCP tool implementations — maps MCP tool calls to core modules."""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sts2_autotest.cli.mcp_protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    McpError,
    McpTool,
)
from sts2_autotest.core.run_service import (
    RunRequest,
    RunStore,
    serialize_record,
    spawn_worker,
)

# ── Path whitelist ──
# _ALLOWED_ROOTS is populated from STS2_MCP_PATH_WHITELIST at import time.
# When empty, _validate_path falls back to ~/STS2-WORKSPACE as the default.
_whitelist = os.environ.get("STS2_MCP_PATH_WHITELIST", "")
_ALLOWED_ROOTS: list[Path] = (
    [Path(p) for p in _whitelist.split(os.pathsep) if p.strip()] if _whitelist else []
)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_path(spec_path: str) -> Path:
    """Resolve and validate a path against the configured whitelist.

    When ``STS2_MCP_PATH_WHITELIST`` is not set, only paths under
    ``~/STS2-WORKSPACE`` are allowed (production-safe default).  When the env
    var is explicitly set, its value is used as the whitelist instead.
    """
    resolved = Path(spec_path).resolve()
    allowed_roots = _ALLOWED_ROOTS or [Path.home() / "STS2-WORKSPACE"]
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise McpError(
        INVALID_PARAMS,
        f"Path '{spec_path}' is not within allowed roots: {allowed_roots}",
    )


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _junit_counts(path: Path) -> tuple[int, int, int]:
    """从测试报告读取准确的通过、失败和跳过数量。"""
    if not path.is_file():
        return 0, 0, 0
    try:
        root = ET.parse(path).getroot()
        nodes = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
        passed = failed = skipped = 0
        for node in nodes:
            total = int(node.attrib.get("tests", "0"))
            node_failed = int(node.attrib.get("failures", "0"))
            node_failed += int(node.attrib.get("errors", "0"))
            node_skipped = int(node.attrib.get("skipped", "0"))
            failed += node_failed
            skipped += node_skipped
            passed += max(0, total - node_failed - node_skipped)
        return passed, failed, skipped
    except (OSError, ET.ParseError, ValueError):
        return 0, 0, 0


# ── Health check ──


def handle_health_check(args: dict[str, Any]) -> dict[str, Any]:
    """Return MCP service health status."""
    return {
        "status": "ok",
        "service": "sts2-autotest-mcp",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _run_store() -> RunStore:
    return RunStore(os.environ.get("STS2_AUTOTEST_RUN_ROOT", "tests/output/.runs"))


def _required_run_id(args: dict[str, Any]) -> str:
    run_id = str(args.get("run_id", ""))
    if not run_id:
        raise McpError(INVALID_PARAMS, "run_id is required")
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise McpError(INVALID_PARAMS, "run_id contains invalid characters")
    return run_id


def handle_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    """返回跨 Agent 能力协商结果。"""
    return {
        "service": "sts2-autotest",
        "contract_version": "1",
        "run_statuses": [
            "QUEUED", "PRECHECK", "PREPARING", "STARTING", "RUNNING",
            "RECOVERING", "COLLECTING", "PASSED", "FAILED_PRODUCT",
            "FAILED_PLATFORM", "BLOCKED_ENVIRONMENT", "CANCELLED",
        ],
        "operations": [
            "submit_run", "get_run", "cancel_run", "resume_run", "get_report",
        ],
        "transports": ["mcp_http", "cli_json"],
        "single_game_instance": True,
    }


def _request_argv(args: dict[str, Any], run_id: str, *, mode: str = "new") -> list[str]:
    argv = ["run"]
    if mode == "resume" or args.get("resume"):
        argv.append("--resume")
    has_explicit_target = bool(args.get("cases") or args.get("suite") or args.get("journey"))
    if args.get("all") or not has_explicit_target:
        argv.append("--all")
    if args.get("cases"):
        argv.extend(["--cases", *[str(item) for item in args["cases"]]])
    if args.get("suite"):
        argv.extend(["--suite", str(args["suite"])])
    if args.get("spec_dir"):
        argv.extend(["--spec-dir", str(args["spec_dir"])])
    if args.get("project"):
        argv.extend(["--project", str(args["project"])])
    if args.get("adapter"):
        argv.extend(["--adapter", str(args["adapter"])])
    if args.get("journey"):
        argv.extend(["--journey", str(args["journey"])])
    if args.get("character_id"):
        argv.extend(["--character-id", str(args["character_id"])])
    if args.get("timeout") is not None:
        argv.extend(["--timeout", str(int(args["timeout"]))])
    argv.extend(["--internal-run-id", run_id])
    return argv


def _submit_persistent_run(args: dict[str, Any], *, mode: str = "new", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence = str(args.get("evidence", "full")).lower()
    if evidence not in {"none", "minimal", "full"}:
        raise McpError(INVALID_PARAMS, "evidence must be none, minimal, or full")
    timeout = int(args.get("timeout", 60))
    if timeout < 1:
        raise McpError(INVALID_PARAMS, "timeout must be at least 1 second")
    store = _run_store()
    request = RunRequest(
        project=args.get("project"),
        suite=args.get("suite"),
        cases=[str(item) for item in args.get("cases", [])],
        mode=mode,
        timeout=timeout,
        adapter=args.get("adapter"),
        spec_dir=args.get("spec_dir"),
        evidence=evidence,
        idempotency_key=args.get("idempotency_key"),
        metadata={
            **(metadata or {}),
            **({"journey": args["journey"]} if args.get("journey") else {}),
            **({"character_id": args["character_id"]} if args.get("character_id") else {}),
        },
    )
    record = store.create(request)
    if record.request is not request:
        return serialize_record(record)
    request.argv = _request_argv(args, record.run_id, mode=mode)
    store.update(record.run_id, request=request)
    try:
        spawn_worker(store, record, request.argv)
    except OSError as exc:
        store.update(
            record.run_id,
            status="FAILED_PLATFORM",
            phase="COMPLETED",
            finished_at=datetime.now(timezone.utc).isoformat(),
            message=f"Cannot start worker: {exc}",
        )
    return serialize_record(store.load(record.run_id))


def handle_submit_run(args: dict[str, Any]) -> dict[str, Any]:
    """异步提交统一测试任务，立即返回 run_id。"""
    spec_dir = args.get("spec_dir")
    if spec_dir:
        resolved = _validate_path(str(spec_dir))
        if not resolved.is_dir():
            raise McpError(INVALID_PARAMS, f"spec_dir is not a directory: {spec_dir}")
    return _submit_persistent_run(args)


def handle_get_run(args: dict[str, Any]) -> dict[str, Any]:
    run_id = _required_run_id(args)
    return serialize_record(_run_store().load(run_id))


def handle_cancel_run(args: dict[str, Any]) -> dict[str, Any]:
    run_id = _required_run_id(args)
    return serialize_record(_run_store().request_cancel(run_id))


def handle_resume_run(args: dict[str, Any]) -> dict[str, Any]:
    run_id = _required_run_id(args)
    record = _run_store().load(run_id)
    if record is None:
        raise McpError(INVALID_PARAMS, f"Unknown run_id: {run_id}")
    return _submit_persistent_run(
        {
            "project": record.request.project,
            "suite": record.request.suite,
            "cases": record.request.cases,
            "spec_dir": record.request.spec_dir,
            "adapter": record.request.adapter,
            "timeout": record.request.timeout,
            "journey": record.request.metadata.get("journey"),
            "character_id": record.request.metadata.get("character_id"),
            "evidence": record.request.evidence,
        },
        mode="resume",
        metadata={"resumed_from": run_id},
    )


# ── Wrapper functions (mocked in tests) ──


def review_spec_file(spec_path: Path) -> Any:
    """Review a spec file.

    Reads the Markdown file, parses it, and runs the spec reviewer.
    Delegates to ``SpecReviewer`` via the B25 review pipeline.
    Wrapped at module level for testability.
    """
    from sts2_autotest.core.markdown_parser import MarkdownParser, detect_level
    from sts2_autotest.core.spec_reviewer import SpecReviewer

    markdown = spec_path.read_text(encoding="utf-8")
    parser = MarkdownParser()
    reviewer = SpecReviewer()

    level = detect_level(markdown)
    if level == "suite":
        suite_spec = parser.parse_suite(markdown, str(spec_path))
        return reviewer.review_suite(suite_spec)
    else:
        case_spec = parser.parse_case(markdown, str(spec_path))
        return reviewer.review(case_spec)


def compile_spec_file(spec_path: Path, output_dir: Path | None = None) -> Path:
    """Compile a spec file into pytest code.

    Reads the Markdown file, parses it, and generates a pytest test file
    via the B25 code generator.  Returns the path of the generated file.
    Wrapped at module level for testability.
    """
    from sts2_autotest.core.code_generator import CodeGenerator
    from sts2_autotest.core.markdown_parser import MarkdownParser, detect_level

    markdown = spec_path.read_text(encoding="utf-8")
    parser = MarkdownParser()
    generator = CodeGenerator()

    resolved_output = output_dir or (spec_path.parent.parent / "generated")

    level = detect_level(markdown)
    if level == "suite":
        suite_spec = parser.parse_suite(markdown, str(spec_path))
        resolved_output.mkdir(parents=True, exist_ok=True)
        func_name = suite_spec.id.lower().replace("-", "_")
        out_file = resolved_output / f"test_{func_name}.py"
        code = generator.generate_suite_test(suite_spec, {})
        out_file.write_text(code, encoding="utf-8")
        return out_file
    else:
        case_spec = parser.parse_case(markdown, str(spec_path))
        output_str = generator.generate_to_file(case_spec, str(resolved_output))
        return Path(output_str)


def run_tests_in_dir(
    spec_dir: Path | str,
    suite: str = "",
    timeout: int = 60,
    *,
    targets: list[Path | str] | None = None,
    output_dir: Path | str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run pytest in a spec directory via subprocess.

    Wrapped at module level for testability.
    """
    spec_dir = Path(spec_dir)
    if output_dir is not None:
        output_dir = Path(output_dir)
    if targets is not None:
        targets = [Path(target) for target in targets]
    run_id = run_id or (
        f"mcp-run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        f"-{uuid.uuid4().hex[:6]}"
    )
    resolved_output = output_dir or (Path("tests/output") / run_id)
    resolved_output.mkdir(parents=True, exist_ok=True)
    junit_xml = resolved_output / "junit.xml"

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *[str(target) for target in (targets or [spec_dir])],
        "-v",
        "--junitxml",
        str(junit_xml),
    ]
    if suite:
        cmd.extend(["-k", suite])

    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 30
        )
    except subprocess.TimeoutExpired:
        return {
            "run_id": run_id,
            "passed": 0,
            "failed": 0,
            "status": "TIMEOUT",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "junit_xml_url": str(junit_xml),
            "stderr": f"Test execution timed out after {timeout + 30}s",
        }

    passed, failed, skipped = _junit_counts(junit_xml)
    if passed == 0 and failed == 0 and skipped == 0:
        # Compatibility fallback for mocked or non-pytest runners that do not
        # emit JUnit XML. Real pytest runs are always counted from XML.
        passed = result.stdout.count("PASSED")
        failed = result.stdout.count("FAILED")

    if result.returncode != 0:
        status = "FAILED"
        stderr_snippet = result.stderr[:500] if result.stderr else f"Exit code: {result.returncode}"
    else:
        status = "OK"
        stderr_snippet = None

    evidence_dir: str | None = None
    if junit_xml.is_file() and os.environ.get("STS2_AUTOTEST_EVIDENCE", "full").lower() != "none":
        from importlib import import_module

        EvidencePackager = import_module(
            "sts2_autotest.evidence.packager"
        ).EvidencePackager

        evidence_root = Path(
            os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", "tests/output")
        )
        packager = EvidencePackager(evidence_root)
        pack_result = "passed" if status == "OK" else "failed"
        pack_dir = packager.create_pack(
            pack_id=run_id,
            run_result=pack_result,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        shutil.copy2(junit_xml, pack_dir / "reports" / "junit.xml")
        (pack_dir / "reports" / "run-result.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "status": status,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        packager.export_artifact(run_id, result=pack_result)
        evidence_dir = str(pack_dir)

    return {
        "run_id": run_id,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "status": status,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "junit_xml_url": str(junit_xml),
        "evidence_dir": evidence_dir,
        "stderr": stderr_snippet,
    }


def read_run_report(run_id: str) -> dict[str, Any]:
    """Read a past run report from the output directory.

    Wrapped at module level for testability.
    """
    record = _run_store().load(run_id)
    report_dir = Path(record.evidence_dir) if record and record.evidence_dir else Path("tests/output") / run_id
    summary_path = report_dir / "summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, ValueError):
            summary = {}
    if record is not None:
        run_summary = serialize_record(record)
    else:
        run_summary = {"run_id": run_id, "status": "NOT_FOUND"}
    artifact_path = (
        summary.get("artifact_path")
        or (record.result.get("artifact_path") if record else None)
        or str(report_dir / "artifacts" / f"{run_id}.zip")
    )
    failure = summary.get("failure")
    if not isinstance(failure, dict) and record is not None:
        failure = record.result.get("failure")
    return {
        "summary": summary or run_summary,
        "failures": [failure] if isinstance(failure, dict) else [],
        "evidence_pack_url": artifact_path,
    }


# ── Tool handlers ──


def handle_review_spec(args: dict[str, Any]) -> dict[str, Any]:
    """Review a Markdown test spec for issues (B25 review phase)."""
    spec_path = args.get("spec_path")
    if not spec_path:
        raise McpError(INVALID_PARAMS, "spec_path is required")
    resolved = _validate_path(spec_path)
    if not resolved.exists():
        raise McpError(INVALID_PARAMS, f"Spec file not found: {spec_path}")
    report = review_spec_file(resolved)
    return {
        "spec_id": report.spec_id,
        "issues": [
            {
                "category": i.category.value,
                "location": i.location,
                "description": i.description,
                "suggestion": i.suggestion,
            }
            for i in report.issues
        ],
        "revised_draft": None,
    }


def handle_compile_spec(args: dict[str, Any]) -> dict[str, Any]:
    """Compile a reviewed spec into pytest code (B25 compile phase)."""
    spec_path = args.get("spec_path")
    if not spec_path:
        raise McpError(INVALID_PARAMS, "spec_path is required")
    resolved = _validate_path(spec_path)
    if not resolved.exists():
        raise McpError(INVALID_PARAMS, f"Spec file not found: {spec_path}")
    output = args.get("output_dir")
    if output:
        output_dir = _validate_path(output)
    else:
        output_dir = resolved.parent.parent / "generated"
    generated_file = compile_spec_file(resolved, output_dir)
    return {"generated_file": str(generated_file), "warnings": []}


def handle_run_test(args: dict[str, Any]) -> dict[str, Any]:
    """Execute tests in a spec directory."""
    spec_dir = args.get("spec_dir")
    if not spec_dir:
        raise McpError(INVALID_PARAMS, "spec_dir is required")
    resolved = _validate_path(spec_dir)
    if not resolved.is_dir():
        raise McpError(INVALID_PARAMS, f"spec_dir is not a directory: {spec_dir}")
    timeout = int(args.get("timeout", 60))
    suite_filter = args.get("suite", "")
    return run_tests_in_dir(resolved, suite=suite_filter, timeout=timeout)


def handle_get_report(args: dict[str, Any]) -> dict[str, Any]:
    """Retrieve a past test run report by run_id."""
    run_id = _required_run_id(args)
    return read_run_report(run_id)


def handle_list_specs(args: dict[str, Any]) -> dict[str, Any]:
    """List available test specs in a directory."""
    spec_dir = args.get("spec_dir", "")
    if spec_dir:
        search_dir = _validate_path(spec_dir)
    else:
        search_dir = _ALLOWED_ROOTS[0] if _ALLOWED_ROOTS else Path(".")
    specs: list[dict[str, str]] = []
    for md in sorted(search_dir.rglob("*.md")):
        spec_type = "suite" if md.name.upper().startswith("SUITE") else "case"
        specs.append({"name": md.stem, "path": str(md), "type": spec_type})
    return {"specs": specs}


def handle_run_pipeline(args: dict[str, Any]) -> dict[str, Any]:
    """Execute full NL pipeline: review -> compile -> run."""
    spec_dir = args.get("spec_dir")
    if not spec_dir:
        raise McpError(INVALID_PARAMS, "spec_dir is required")
    resolved = _validate_path(spec_dir)
    stages: list[str] = args.get("stages", ["review", "compile", "run"])
    result: dict[str, Any] = {
        "review_issues": [],
        "compiled_files": [],
        "test_result": None,
    }
    md_files = list(resolved.rglob("*.md"))
    if not md_files:
        return result
    for md_file in md_files:
        if "review" in stages:
            review_result = handle_review_spec({"spec_path": str(md_file)})
            result["review_issues"].extend(review_result["issues"])
        if "compile" in stages:
            compile_result = handle_compile_spec({"spec_path": str(md_file)})
            result["compiled_files"].append(compile_result["generated_file"])
    if "run" in stages:
        result["test_result"] = handle_run_test({"spec_dir": spec_dir})
    return result


# ── Tool Registry ──


class ToolRegistry:
    """Registry of MCP tools and their handler functions."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[McpTool, ToolHandler]] = {
            "health_check": (
                McpTool(
                    name="health_check",
                    description="Check MCP service health",
                    input_schema={"type": "object", "properties": {}},
                ),
                handle_health_check,
            ),
            "capabilities": (
                McpTool(
                    name="capabilities",
                    description="Discover the stable cross-agent run contract",
                    input_schema={"type": "object", "properties": {}},
                ),
                handle_capabilities,
            ),
            "submit_run": (
                McpTool(
                    name="submit_run",
                    description="Submit a persistent asynchronous regression run",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project": {"type": "string"},
                            "spec_dir": {"type": "string"},
                            "suite": {"type": "string"},
                            "cases": {"type": "array", "items": {"type": "string"}},
                            "timeout": {"type": "integer", "minimum": 1},
                            "adapter": {"type": "string", "enum": ["cli", "agent"]},
                            "journey": {
                                "type": "string",
                                "enum": ["new_run", "resume_run", "first_battle", "finish_interstitials"],
                            },
                            "character_id": {"type": "string"},
                            "evidence": {"type": "string", "enum": ["none", "minimal", "full"]},
                            "idempotency_key": {"type": "string"},
                        },
                    },
                ),
                handle_submit_run,
            ),
            "get_run": (
                McpTool(
                    name="get_run",
                    description="Get a persistent run status and result",
                    input_schema={
                        "type": "object",
                        "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"],
                    },
                ),
                handle_get_run,
            ),
            "cancel_run": (
                McpTool(
                    name="cancel_run",
                    description="Cancel a queued or running regression",
                    input_schema={
                        "type": "object",
                        "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"],
                    },
                ),
                handle_cancel_run,
            ),
            "resume_run": (
                McpTool(
                    name="resume_run",
                    description="Resume a prior run from its saved progress",
                    input_schema={
                        "type": "object",
                        "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"],
                    },
                ),
                handle_resume_run,
            ),
            "review_spec": (
                McpTool(
                    name="review_spec",
                    description=(
                        "Review a Markdown test spec for issues "
                        "(B25 review phase)"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "spec_path": {
                                "type": "string",
                                "description": "Path to the Markdown spec file",
                            }
                        },
                        "required": ["spec_path"],
                    },
                ),
                handle_review_spec,
            ),
            "compile_spec": (
                McpTool(
                    name="compile_spec",
                    description=(
                        "Compile a reviewed spec into pytest code "
                        "(B25 compile phase)"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "spec_path": {
                                "type": "string",
                                "description": "Path to the Markdown spec file",
                            },
                            "output_dir": {
                                "type": "string",
                                "description": "Optional output directory",
                            },
                        },
                        "required": ["spec_path"],
                    },
                ),
                handle_compile_spec,
            ),
            "run_test": (
                McpTool(
                    name="run_test",
                    description=(
                        "Execute tests in a spec directory "
                        "(B25 run phase)"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "spec_dir": {
                                "type": "string",
                                "description": "Directory containing test specs",
                            },
                            "suite": {
                                "type": "string",
                                "description": "Optional suite name filter",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": (
                                    "Timeout per test in seconds (default 60)"
                                ),
                            },
                        },
                        "required": ["spec_dir"],
                    },
                ),
                handle_run_test,
            ),
            "run_pipeline": (
                McpTool(
                    name="run_pipeline",
                    description=(
                        "Execute full NL pipeline: review -> compile -> run "
                        "(equivalent to 'autotest run --all')"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "spec_dir": {
                                "type": "string",
                                "description": "Directory containing test specs",
                            },
                            "stages": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["review", "compile", "run"],
                                },
                                "description": (
                                    "Pipeline stages to execute (default: all)"
                                ),
                            },
                        },
                        "required": ["spec_dir"],
                    },
                ),
                handle_run_pipeline,
            ),
            "get_report": (
                McpTool(
                    name="get_report",
                    description=(
                        "Retrieve a past test run report by run_id"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "run_id": {
                                "type": "string",
                                "description": "Run ID to fetch",
                            }
                        },
                        "required": ["run_id"],
                    },
                ),
                handle_get_report,
            ),
            "list_specs": (
                McpTool(
                    name="list_specs",
                    description="List available test specs in a directory",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "spec_dir": {
                                "type": "string",
                                "description": (
                                    "Optional directory to search"
                                ),
                            }
                        },
                    },
                ),
                handle_list_specs,
            ),
        }

    def list_tools(self) -> list[McpTool]:
        """Return the list of all registered tool definitions."""
        return [tool for tool, _ in self._tools.values()]

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Look up and execute a tool handler by name."""
        entry = self._tools.get(tool_name)
        if entry is None:
            raise McpError(METHOD_NOT_FOUND, f"Unknown tool: {tool_name}")
        _, handler = entry
        return handler(args)
