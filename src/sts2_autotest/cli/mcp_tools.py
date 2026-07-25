"""MCP tool implementations — maps MCP tool calls to core modules."""

from __future__ import annotations

import asyncio
import os
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
import uuid
import zipfile
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


# 能力探测超时（秒）——游戏未起时快速返回未就绪，绝不阻塞能力协商。
_CAPABILITIES_PROBE_TIMEOUT = float(
    os.environ.get("STS2_CAPABILITIES_PROBE_TIMEOUT", "8")
)


def _probe_runtime_capabilities() -> dict[str, Any]:
    """非破坏性地探测真实运行能力（游戏控制 + 调试控制台）。

    在独立线程内运行异步探测，避免与 MCP 事件循环冲突；游戏未启动或控制入口
    不可达时快速返回未就绪，绝不抛错、绝不阻塞主流程。探测只用无副作用命令
    （health + 调试控制台 help），严禁执行任何改变游戏进度的命令。
    """
    configured = os.environ.get(
        "STS2_ADAPTER__AGENT__DEBUG_ACTIONS", "false"
    ).lower() in {"true", "1", "yes", "on"}
    result: dict[str, Any] = {
        "game_control_ready": False,
        "debug_actions_configured": configured,
        "debug_actions_verified": False,
        "debug_actions_reason": "GAME_CONTROL_UNAVAILABLE",
        "runtime_capabilities_checked_at": datetime.now(timezone.utc).isoformat(),
    }

    def _worker() -> None:
        try:
            # 游戏控制入口（8080）与调试控制台都由 AgentAdapter 承载。
            from sts2_autotest.cli.main import _create_adapter

            adapter = _create_adapter("agent")
        except Exception:
            return

        async def _probe() -> None:
            try:
                health = await adapter.health_check()
                result["game_control_ready"] = bool(getattr(health, "healthy", False))
                verification = await adapter.verify_debug_actions()
                result["debug_actions_configured"] = bool(verification.configured)
                result["debug_actions_verified"] = bool(verification.verified)
                result["debug_actions_reason"] = verification.reason
                if verification.checked_at:
                    result["runtime_capabilities_checked_at"] = verification.checked_at
            finally:
                try:
                    await adapter.cleanup()
                except Exception:
                    pass

        try:
            asyncio.run(_probe())
        except Exception:
            pass

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout=_CAPABILITIES_PROBE_TIMEOUT)
    return result


def handle_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    """返回跨 Agent 能力协商结果——能力字段反映真实运行状态（修复二）。"""
    runtime = _probe_runtime_capabilities()
    # 快速结束战斗需"配置要求启用" AND "实际探测确认可用"双真。
    fast_end_ready = bool(
        runtime["debug_actions_configured"] and runtime["debug_actions_verified"]
    )
    return {
        "service": "sts2-autotest",
        "contract_version": "1",
        "run_statuses": [
            "QUEUED", "PRECHECK", "PREPARING", "STARTING", "RUNNING",
            "RECOVERING", "COLLECTING", "PASSED", "FAILED_PRODUCT",
            "FAILED_PLATFORM", "BLOCKED_ENVIRONMENT", "CANCELLED",
        ],
        "operations": [
            "capabilities", "submit_run", "get_run", "cancel_run", "resume_run", "get_report",
        ],
        "transports": ["mcp_http", "cli_json"],
        "single_game_instance": True,
        "goal_scene_execution": True,
        # 真实运行能力（非仅配置声明）——供 Agent 判断是否可用。
        "game_control_ready": runtime["game_control_ready"],
        "debug_actions_configured": runtime["debug_actions_configured"],
        "debug_actions_verified": runtime["debug_actions_verified"],
        "debug_actions_reason": runtime["debug_actions_reason"],
        "runtime_capabilities_checked_at": runtime["runtime_capabilities_checked_at"],
        "supported_target_scene": [
            "MAIN_MENU", "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT",
            "REST", "SHOP", "CHEST", "CARD_REWARD", "NEXT_ACT",
        ],
        "supports_act_traversal": True,
        "route_policies": ["leftmost", "target"],
        "combat_modes": ["traversal", "basic", "death"],
        "combat_capabilities": {
            "traversal_fast_end_action": "win_combat",
            "traversal_fast_end_enabled": fast_end_ready,
            "traversal_fallback": "basic",
            "fast_end_is_development_only": True,
            "death_mode": {
                "target_scene": "COMBAT",
                "end_turn_only": True,
                "success_screen": "GAME_OVER",
                "description": "combat_mode=death 时战斗中每回合只执行 end_turn，"
                "直到真实 GAME_OVER；用于角色死亡测试，禁止与 win_combat 混用。",
            },
        },
        "supported_journeys": [
            "new_run", "resume_run", "first_battle", "finish_interstitials",
            "goal_scene", "act_traversal", "card_test",
        ],
        "card_test": {
            "journey": "card_test",
            "parameter": "card_id",
            "requires_debug_actions": True,
            "debug_actions_enabled": fast_end_ready,
            "description": "通过调试控制台把 card_id 加入手牌，验证入手并真实打出；"
            "平台只断言通用可观察事实，具体卡牌效果由项目用例断言。",
        },
        "evidence_levels": ["none", "minimal", "full"],
        "submit_parameters": [
            "journey", "character_id", "target_scene", "route_policy",
            "combat_mode", "timeout", "evidence", "idempotency_key", "card_id",
        ],
        "compatibility": "旧 project/suite/cases 请求继续可用；目标场景请求使用同一任务入口。",
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
    if args.get("target_scene"):
        argv.extend(["--target-scene", str(args["target_scene"])])
    if args.get("route_policy"):
        argv.extend(["--route-policy", str(args["route_policy"])])
    if args.get("combat_mode"):
        argv.extend(["--combat-mode", str(args["combat_mode"])])
    if args.get("card_id"):
        argv.extend(["--card-id", str(args["card_id"])])
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
    journey = args.get("journey")
    target_scene = args.get("target_scene")
    if target_scene and not journey:
        journey = "goal_scene"
    adapter = args.get("adapter")
    if adapter is None and (target_scene or journey in {"goal_scene", "act_traversal", "card_test"}):
        adapter = "agent"
    request_args = {**args, "adapter": adapter} if adapter else args
    request = RunRequest(
        project=args.get("project"),
        suite=args.get("suite"),
        cases=[str(item) for item in args.get("cases", [])],
        mode=mode,
        timeout=timeout,
        adapter=adapter,
        spec_dir=args.get("spec_dir"),
        evidence=evidence,
        idempotency_key=args.get("idempotency_key"),
        metadata={
            **(metadata or {}),
            **({"journey": journey} if journey else {}),
            **({"character_id": args["character_id"]} if args.get("character_id") else {}),
            **({"target_scene": target_scene} if target_scene else {}),
            **({"route_policy": args["route_policy"]} if args.get("route_policy") else {}),
            **({"combat_mode": args["combat_mode"]} if args.get("combat_mode") else {}),
            **({"card_id": args["card_id"]} if args.get("card_id") else {}),
        },
    )
    record = store.create(request)
    if record.request is not request:
        return serialize_record(record)
    request.argv = _request_argv(request_args, record.run_id, mode=mode)
    store.update(record.run_id, request=request)
    # 修复四：恢复任务在新 run_id 上显式记录它继承自哪个原任务。
    resumed_from = (metadata or {}).get("resumed_from")
    if resumed_from:
        store.update(record.run_id, resumed_from=str(resumed_from))
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
    target_scenes = {
        "MAIN_MENU", "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT",
        "REST", "SHOP", "CHEST", "CARD_REWARD", "NEXT_ACT",
    }
    if args.get("target_scene") and str(args["target_scene"]).upper() not in target_scenes:
        raise McpError(INVALID_PARAMS, f"Unsupported target_scene: {args['target_scene']}")
    if args.get("route_policy", "leftmost") not in {"leftmost", "target"}:
        raise McpError(INVALID_PARAMS, "route_policy must be leftmost or target")
    if args.get("combat_mode", "traversal") not in {"traversal", "basic", "death"}:
        raise McpError(INVALID_PARAMS, "combat_mode must be traversal, basic, or death")
    journey = args.get("journey")
    if journey and journey not in {
        "new_run", "resume_run", "first_battle", "finish_interstitials",
        "goal_scene", "act_traversal", "card_test",
    }:
        raise McpError(INVALID_PARAMS, f"Unsupported journey: {journey}")
    if journey == "card_test":
        if args.get("target_scene"):
            raise McpError(INVALID_PARAMS, "card_test does not take target_scene")
        if not str(args.get("card_id") or "").strip():
            raise McpError(INVALID_PARAMS, "card_test requires a non-empty card_id")
    if journey == "act_traversal" and args.get("target_scene") not in (None, "NEXT_ACT", "next_act"):
        raise McpError(INVALID_PARAMS, "act_traversal target_scene must be NEXT_ACT")
    if spec_dir:
        resolved = _validate_path(str(spec_dir))
        if not resolved.is_dir():
            raise McpError(INVALID_PARAMS, f"spec_dir is not a directory: {spec_dir}")
    project = args.get("project")
    if isinstance(project, str) and ("/" in project or "\\" in project or project.startswith(".")):
        # 目录型 project 与 spec_dir 适用同一允许范围：
        # 项目目录、其声明指向的配置文件、以及配置内部声明的
        # 规格目录与输出目录都必须在白名单内。
        resolved_project = _validate_path(project)
        if not resolved_project.is_dir():
            raise McpError(INVALID_PARAMS, f"project directory does not exist: {project}")
        from sts2_autotest.adapters.project_extension import (
            find_project_config_file,
            load_project_spec_output,
        )

        config_file = find_project_config_file(resolved_project)
        if config_file is not None:
            _validate_path(str(config_file))
        declared_spec, declared_output = load_project_spec_output(resolved_project)
        if declared_spec:
            _validate_path(declared_spec)
        if declared_output:
            _validate_path(declared_output)
    if journey == "act_traversal" and not args.get("target_scene"):
        args = {**args, "target_scene": "NEXT_ACT"}
    return _submit_persistent_run(args)


def handle_get_run(args: dict[str, Any]) -> dict[str, Any]:
    run_id = _required_run_id(args)
    store = _run_store()
    store.reap_if_worker_gone(run_id)
    return serialize_record(store.load(run_id))


def handle_cancel_run(args: dict[str, Any]) -> dict[str, Any]:
    run_id = _required_run_id(args)
    return serialize_record(_run_store().request_cancel(run_id))


def handle_resume_run(args: dict[str, Any]) -> dict[str, Any]:
    from sts2_autotest.core.run_service import resume_precheck

    run_id = _required_run_id(args)
    record = _run_store().load(run_id)
    if record is None:
        raise McpError(INVALID_PARAMS, f"Unknown run_id: {run_id}")
    # 修复四：恢复必须等待原任务的取消/失败完全结束（终态 + 证据已封存）。
    ok, reason = resume_precheck(record)
    if not ok:
        raise McpError(
            INVALID_PARAMS,
            f"Run {run_id} cannot be resumed (status={record.status}, "
            f"evidence_sealed={record.evidence_sealed}): {reason}",
        )
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
            "target_scene": record.request.metadata.get("target_scene"),
            "route_policy": record.request.metadata.get("route_policy", "leftmost"),
            "combat_mode": record.request.metadata.get("combat_mode", "traversal"),
            "card_id": record.request.metadata.get("card_id"),
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


def compile_spec_file(spec_path: Path, output_dir: Path | None = None, character_aliases: dict[str, str] | None = None) -> Path:
    """Compile a spec file into pytest code.

    Reads the Markdown file, parses it, and generates a pytest test file
    via the B25 code generator.  Returns the path of the generated file.
    Wrapped at module level for testability.
    character_aliases：任务项目提供的角色别名映射（空=平台默认原游戏角色）。
    """
    from sts2_autotest.core.code_generator import CodeGenerator
    from sts2_autotest.core.markdown_parser import MarkdownParser, detect_level

    markdown = spec_path.read_text(encoding="utf-8")
    parser = MarkdownParser()
    generator = CodeGenerator(character_aliases=character_aliases)

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
    project_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run pytest in a spec directory via subprocess.

    Wrapped at module level for testability.
    project_dir：任务项目根目录；提供时经 STS2_PROJECT_DIR 传入测试
    子进程，使运行时装配按该项目读取项目扩展规则（卡牌前缀、
    种子命令模板），否则测试进程退回当前目录的中性配置。
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

    env: dict[str, str] | None = None
    if project_dir is not None:
        env = {**os.environ, "STS2_PROJECT_DIR": str(Path(project_dir).resolve())}

    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 30, env=env
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


def _report_roots(run_id: str, record: Any) -> list[Path]:
    candidates: list[Path] = []
    if record is not None:
        for raw in (
            record.evidence_dir,
            record.result.get("evidence_dir") if isinstance(record.result, dict) else None,
        ):
            if raw:
                candidates.append(Path(str(raw)).expanduser())
    evidence_root = Path(
        os.environ.get("STS2_AUTOTEST_EVIDENCE_DIR", "tests/output")
    ).expanduser()
    candidates.extend([evidence_root / run_id, Path("tests/output") / run_id])
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate if candidate.is_absolute() else Path.cwd() / candidate
        key = str(resolved.resolve())
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _artifact_candidates(run_id: str, record: Any, roots: list[Path]) -> list[Path]:
    raw_paths: list[str] = []
    if record is not None:
        if isinstance(record.result, dict) and record.result.get("artifact_path"):
            raw_paths.append(str(record.result["artifact_path"]))
    for root in roots:
        summary_path = root / "summary.json"
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(summary, dict) and summary.get("artifact_path"):
                    raw_paths.append(str(summary["artifact_path"]))
            except (OSError, ValueError):
                pass
        raw_paths.extend(str(path) for path in (root.parent / "artifacts").glob(f"{run_id}_*.zip"))
    candidates: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.is_file():
            resolved = path.resolve()
            if str(resolved) not in seen:
                seen.add(str(resolved))
                candidates.append(resolved)
    return sorted(candidates, key=lambda path: path.stat().st_mtime)


def _read_report_json(
    roots: list[Path], artifact: Path | None, name: str,
) -> tuple[dict[str, Any] | None, Path | None]:
    for root in roots:
        path = root / "reports" / name
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            return loaded, path.resolve()
    if artifact is not None:
        try:
            with zipfile.ZipFile(artifact) as archive:
                raw = archive.read(f"reports/{name}")
            loaded = json.loads(raw.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded, None
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            pass
    return None, None


def _compact_failure(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {
        key: value[key]
        for key in (
            "type", "message", "classification", "exit_code", "stuck_screen",
            "last_action", "reason_code", "reason", "status_trajectory",
        )
        if key in value
    }
    if isinstance(value.get("last_state"), dict):
        compact["last_state_summary"] = {
            "screen": value["last_state"].get("screen"),
            "available_actions": value["last_state"].get("available_actions", []),
        }
    return compact


def _compact_result_payload(value: dict[str, Any]) -> dict[str, Any]:
    """剔除可能巨大的嵌入状态（journey_evidence、dict 形态的 last/final_state），
    保留标量 final_state 等关键结果字段。"""
    compact: dict[str, Any] = {}
    for key, item in value.items():
        if key == "journey_evidence":
            continue
        if key in {"last_state", "final_state"} and isinstance(item, dict):
            continue
        compact[key] = item
    if isinstance(compact.get("failure"), dict):
        compact["failure"] = _compact_failure(compact["failure"])
    return compact


def _compact_trace(value: Any, path: Path | None, artifact: Path | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    operations = value.get("operations")
    map_route = value.get("map_route")
    return {
        "journey": value.get("journey"),
        "target_scene": value.get("target_scene"),
        "scene_trajectory": value.get("scene_trajectory", []),
        "rooms": value.get("rooms", []),
        "duration_ms": value.get("duration_ms"),
        "operation_count": len(operations) if isinstance(operations, list) else 0,
        "map_route_count": len(map_route) if isinstance(map_route, list) else 0,
        "path": str(path) if path is not None else None,
        "archive_member": "reports/journey-trace.json" if artifact is not None and path is None else None,
    }


def read_run_report(run_id: str) -> dict[str, Any]:
    """从持久目录或真实证据压缩包读取精简报告。"""
    record = _run_store().load(run_id)
    roots = _report_roots(run_id, record)
    report_dir = next((root for root in roots if root.is_dir()), None)
    artifact_candidates = _artifact_candidates(run_id, record, roots)
    artifact = artifact_candidates[-1] if artifact_candidates else None

    summary: dict[str, Any] = {}
    summary_path: Path | None = None
    for root in roots:
        path = root / "summary.json"
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            summary = loaded
            summary_path = path.resolve()
            break
    if not summary and artifact is not None:
        try:
            with zipfile.ZipFile(artifact) as archive:
                loaded = json.loads(archive.read("summary.json").decode("utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            pass

    if record is not None:
        run_summary = serialize_record(record)
        raw_result = run_summary.get("result")
        if isinstance(raw_result, dict):
            compact_result = _compact_result_payload(raw_result)
            report_trace_path = (
                str(report_dir / "reports" / "journey-trace.json")
                if report_dir is not None and (report_dir / "reports" / "journey-trace.json").is_file()
                else None
            )
            compact_result["journey_trace_path"] = (
                report_trace_path or compact_result.get("journey_trace_path")
            )
            run_summary["result"] = compact_result
    else:
        run_summary = {"run_id": run_id, "status": "NOT_FOUND"}

    manifest, manifest_path = _read_report_json(roots, artifact, "evidence-manifest.json")
    trace, trace_path = _read_report_json(roots, artifact, "journey-trace.json")
    journey_failure, failure_path = _read_report_json(roots, artifact, "journey-failure.json")
    run_result, run_result_path = _read_report_json(roots, artifact, "run-result.json")
    failure = _compact_failure(summary.get("failure"))
    if failure is None and record is not None:
        failure = _compact_failure(record.result.get("failure"))

    compact_summary = dict(summary) if summary else dict(run_summary)
    if isinstance(compact_summary.get("failure"), dict):
        compact_summary["failure"] = _compact_failure(compact_summary["failure"])
    compact_run_result = None
    if isinstance(run_result, dict):
        compact_run_result = _compact_result_payload(run_result)

    report_paths = {
        "summary": str(summary_path) if summary_path is not None else None,
        "evidence_manifest": str(manifest_path) if manifest_path is not None else None,
        "journey_trace": str(trace_path) if trace_path is not None else None,
        "journey_failure": str(failure_path) if failure_path is not None else None,
        "run_result": str(run_result_path) if run_result_path is not None else None,
        "human_report": (
            str((report_dir / "summary.md").resolve())
            if report_dir is not None and (report_dir / "summary.md").is_file()
            else None
        ),
    }
    artifact_status = {
        "exists": artifact is not None,
        "path": str(artifact) if artifact is not None else None,
        "readable": False,
    }
    if artifact is not None:
        try:
            with zipfile.ZipFile(artifact) as archive:
                archive.testzip()
            artifact_status["readable"] = True
        except (OSError, zipfile.BadZipFile):
            artifact_status["readable"] = False

    return {
        "summary": compact_summary,
        "run": run_summary,
        "progress": (record.progress if record is not None else {}),
        "failures": [failure] if failure else [],
        "evidence_manifest": manifest,
        "journey_trace": _compact_trace(trace, trace_path, artifact),
        "journey_failure": _compact_failure(journey_failure),
        "run_result": compact_run_result,
        "report_paths": report_paths,
        "evidence_dir": str(report_dir.resolve()) if report_dir is not None else None,
        "evidence_pack_url": str(artifact) if artifact is not None else None,
        "artifact_status": artifact_status,
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
    generated_file = compile_spec_file(
        resolved, output_dir, character_aliases=_aliases_from_args(args)
    )
    return {"generated_file": str(generated_file), "warnings": []}


def _aliases_from_args(args: dict[str, Any]) -> dict[str, str] | None:
    """按任务 project 解析角色别名（无 project 时 None=平台默认）。"""
    project = args.get("project")
    if not isinstance(project, str) or not project:
        return None
    from sts2_autotest.cli.main import _resolve_project_base_dir
    from sts2_autotest.adapters.project_extension import load_character_aliases

    base_dir = _resolve_project_base_dir(project)
    if base_dir is None:
        return None
    return load_character_aliases(base_dir)


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
    project_dir: Path | None = None
    project = args.get("project")
    if isinstance(project, str) and project:
        from sts2_autotest.cli.main import _resolve_project_base_dir

        project_dir = _resolve_project_base_dir(project)
    return run_tests_in_dir(
        resolved, suite=suite_filter, timeout=timeout, project_dir=project_dir
    )


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
    # project 贯穿编译（角色别名）与执行（项目扩展配置）；
    # 目录型 project 与提交入口同一白名单。
    project = args.get("project")
    if isinstance(project, str) and project and ("/" in project or "\\" in project or project.startswith(".")):
        resolved_project = _validate_path(project)
        if not resolved_project.is_dir():
            raise McpError(INVALID_PARAMS, f"project directory does not exist: {project}")
        from sts2_autotest.adapters.project_extension import find_project_config_file

        config_file = find_project_config_file(resolved_project)
        if config_file is not None:
            _validate_path(str(config_file))
    stages: list[str] = args.get("stages", ["review", "compile", "run"])
    result: dict[str, Any] = {
        "review_issues": [],
        "compiled_files": [],
        "test_result": None,
    }
    md_files = list(resolved.rglob("*.md"))
    if not md_files:
        return result
    compile_args: dict[str, Any] = {"project": project} if project else {}
    for md_file in md_files:
        if "review" in stages:
            review_result = handle_review_spec({"spec_path": str(md_file)})
            result["review_issues"].extend(review_result["issues"])
        if "compile" in stages:
            compile_result = handle_compile_spec(
                {"spec_path": str(md_file), **compile_args}
            )
            result["compiled_files"].append(compile_result["generated_file"])
    if "run" in stages:
        result["test_result"] = handle_run_test(
            {"spec_dir": spec_dir, **({"project": project} if project else {})}
        )
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
                                "enum": [
                                    "new_run", "resume_run", "first_battle", "finish_interstitials",
                                    "goal_scene", "act_traversal", "card_test",
                                ],
                            },
                            "character_id": {"type": "string"},
                            "card_id": {"type": "string"},
                            "target_scene": {
                                "type": "string",
                                "enum": [
                                    "MAIN_MENU", "CHARACTER_SELECT", "MAP", "EVENT", "COMBAT",
                                    "REST", "SHOP", "CHEST", "CARD_REWARD", "NEXT_ACT",
                                ],
                            },
                            "route_policy": {"type": "string", "enum": ["leftmost", "target"]},
                            "combat_mode": {"type": "string", "enum": ["traversal", "basic", "death"]},
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
                            "project": {
                                "type": "string",
                                "description": (
                                    "Task project (directory or registered name); "
                                    "its project_extension config applies at runtime"
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
                            "project": {
                                "type": "string",
                                "description": (
                                    "Task project (directory or registered name); "
                                    "its character aliases and project_extension "
                                    "config apply to compile and run phases"
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
