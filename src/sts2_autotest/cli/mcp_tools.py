"""MCP tool implementations — maps MCP tool calls to core modules."""

from __future__ import annotations

import os
import subprocess
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

# ── Path whitelist ──
# Only validate when STS2_MCP_PATH_WHITELIST is explicitly set.
# When empty, all paths are allowed (typical in dev/test).
_whitelist = os.environ.get("STS2_MCP_PATH_WHITELIST", "")
_ALLOWED_ROOTS: list[Path] = (
    [Path(p) for p in _whitelist.split(os.pathsep) if p.strip()] if _whitelist else []
)


def _validate_path(spec_path: str) -> Path:
    """Resolve and validate a path against the configured whitelist."""
    resolved = Path(spec_path).resolve()
    if _ALLOWED_ROOTS:
        for root in _ALLOWED_ROOTS:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise McpError(
            INVALID_PARAMS,
            f"Path '{spec_path}' is not within allowed roots: {_ALLOWED_ROOTS}",
        )
    return resolved


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


# ── Health check ──


def handle_health_check(args: dict[str, Any]) -> dict[str, Any]:
    """Return MCP service health status."""
    return {
        "status": "ok",
        "service": "sts2-autotest-mcp",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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
    spec_dir: Path, suite: str = "", timeout: int = 60
) -> dict[str, Any]:
    """Run pytest in a spec directory via subprocess.

    Wrapped at module level for testability.
    """
    run_id = (
        f"mcp-run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        f"-{uuid.uuid4().hex[:6]}"
    )
    output_dir = Path("tests/output") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    junit_xml = output_dir / "junit.xml"

    cmd = [
        "python3.11",
        "-m",
        "pytest",
        str(spec_dir),
        "-v",
        "--timeout",
        str(timeout),
        "--junitxml",
        str(junit_xml),
    ]
    if suite:
        cmd.extend(["-k", suite])

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
            "duration_ms": (timeout + 30) * 1000,
            "junit_xml_url": str(junit_xml),
            "stderr": f"Test execution timed out after {timeout + 30}s",
        }

    passed = result.stdout.count("PASSED")
    failed = result.stdout.count("FAILED")

    if result.returncode != 0:
        status = "FAILED"
        stderr_snippet = result.stderr[:500] if result.stderr else f"Exit code: {result.returncode}"
    else:
        status = "OK"
        stderr_snippet = None

    return {
        "run_id": run_id,
        "passed": passed,
        "failed": failed,
        "status": status,
        "duration_ms": 0,
        "junit_xml_url": str(junit_xml),
        "stderr": stderr_snippet,
    }


def read_run_report(run_id: str) -> dict[str, Any]:
    """Read a past run report from the output directory.

    Wrapped at module level for testability.
    """
    report_dir = Path("tests/output") / run_id
    return {
        "summary": {
            "run_id": run_id,
            "status": "completed" if report_dir.exists() else "not_found",
        },
        "failures": [],
        "evidence_pack_url": f"tests/output/{run_id}/evidence.zip",
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
    run_id = args.get("run_id")
    if not run_id:
        raise McpError(INVALID_PARAMS, "run_id is required")
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
