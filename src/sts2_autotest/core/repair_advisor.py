"""Repair advisor — analyze crash evidence and generate structured repair suggestions (B10).

L1: Rule engine — matches FailureInfo against a hardcoded rule table.
L2: Stack trace parser — extracts file:line locations from Python/C# stack traces.
L3: analyze_from_exception() — static method for first-hand exception analysis (replay path).
"""

from __future__ import annotations

import re
import time
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sts2_autotest.common.errors import ErrorCategory
from sts2_autotest.common.evidence import (
    FailureInfo,
    RepairReport,
    RepairSuggestion,
    SummaryJson,
)
from sts2_autotest.common.errors import FailureClassification
from sts2_autotest.common.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("core.repair_advisor")

# ── L2 stack trace patterns ──────────────────────────────────

_PYTHON_TRACEBACK_RE = re.compile(r'File "(.+?)", line (\d+)')
_CSHARP_STACK_RE = re.compile(r'at .+? in (.+?):line (\d+)')

# ── L1 rule table ─────────────────────────────────────────────


def _match_rules(failure: FailureInfo) -> list[RepairSuggestion]:
    """Match FailureInfo against the hardcoded rule table (L1).

    Returns a list of RepairSuggestion — empty list when no rules match.
    """
    suggestions: list[RepairSuggestion] = []

    # Rule 1: crash_error
    if failure.type == ErrorCategory.CRASH_ERROR.value:
        suggestions.append(RepairSuggestion(
            confidence=0.50,
            category="code_fix",
            title="游戏进程异常退出",
            description="游戏进程异常退出，检查最近修改的 C# 代码是否有未处理异常",
        ))

    # Rule 2: adapter_error + version_mismatch
    if failure.type == ErrorCategory.ADAPTER_ERROR.value:
        msg_lower = failure.message.lower()
        if "version_mismatch" in msg_lower or "版本" in failure.message:
            suggestions.append(RepairSuggestion(
                confidence=0.60,
                category="config_change",
                title="适配器与游戏/BaseLib 版本不兼容",
                description="适配器版本与游戏版本不兼容，更新 BaseLib 或 CLI Mod 到匹配版本",
            ))

    # Rule 3: timeout_error
    if failure.type == ErrorCategory.TIMEOUT_ERROR.value:
        suggestions.append(RepairSuggestion(
            confidence=0.45,
            category="code_fix",
            title="操作超时",
            description="操作超时，检查 Mod 初始化代码是否有死循环或资源阻塞",
        ))

    # Rule 4: assertion_error
    if failure.type == ErrorCategory.ASSERTION_ERROR.value:
        desc = "状态转换断言失败"
        if failure.expected is not None and failure.actual is not None:
            desc = f"状态转换断言失败：预期 {failure.expected}，实际 {failure.actual}"
        elif failure.expected is not None:
            desc = f"状态转换断言失败：预期 {failure.expected}"
        elif failure.actual is not None:
            desc = f"状态转换断言失败：实际 {failure.actual}"
        suggestions.append(RepairSuggestion(
            confidence=0.55,
            category="code_fix",
            title="状态转换断言失败",
            description=desc,
        ))

    # Rule 5: session_error
    if failure.type == ErrorCategory.SESSION_ERROR.value:
        suggestions.append(RepairSuggestion(
            confidence=0.40,
            category="env_fix",
            title="会话级别错误",
            description="会话级别错误，检查运行环境和关键文件路径配置",
        ))

    # Rule 6: game_error
    if failure.type == ErrorCategory.GAME_ERROR.value:
        suggestions.append(RepairSuggestion(
            confidence=0.35,
            category="investigation_needed",
            title="游戏内部错误",
            description="游戏内部错误，需进一步调查游戏日志和 Mod 交互",
        ))

    return suggestions


def _classify_failure(failure: FailureInfo) -> FailureClassification:
    """Classify a failure by root cause (协议层 B20).

    Rules (ordered by specificity):
    1. Crash / game exit → mod (MOD code defect) or environment (process not found)
    2. Adapter error + version mismatch → autotest
    3. Adapter error + connection/port/endpoint → environment
    4. Assertion error → test_case (wrong expectation) or mod (actual mismatch)
    5. Timeout → mod (infinite loop/blocked) or environment (game hung)
    6. Session error → environment
    7. Game error → mod

    Returns FailureClassification enum.
    """
    msg_lower = failure.message.lower()

    # Crash with game process not found → environment
    if failure.type == ErrorCategory.CRASH_ERROR.value:
        if "process" in msg_lower and "not found" in msg_lower:
            return FailureClassification.ENVIRONMENT
        if any(w in msg_lower for w in ["exit", "terminat", "crash", "segfault"]):
            return FailureClassification.MOD
        return FailureClassification.MOD

    # Adapter errors
    if failure.type == ErrorCategory.ADAPTER_ERROR.value:
        if "version_mismatch" in msg_lower or "版本" in failure.message:
            return FailureClassification.AUTOTEST
        if any(w in msg_lower for w in ["connect", "refused", "timeout", "endpoint"]):
            return FailureClassification.ENVIRONMENT
        if any(w in msg_lower for w in ["parse", "serialize", "schema", "field"]):
            return FailureClassification.AUTOTEST
        return FailureClassification.AUTOTEST

    # Assertion errors
    if failure.type == ErrorCategory.ASSERTION_ERROR.value:
        if failure.expected is not None and failure.actual is None:
            # State transition expected but no state returned
            return FailureClassification.TEST_CASE
        if failure.expected is not None and failure.actual is not None:
            return FailureClassification.MOD
        return FailureClassification.TEST_CASE

    # Timeout
    if failure.type == ErrorCategory.TIMEOUT_ERROR.value:
        if any(w in msg_lower for w in ["init", "startup", "load"]):
            return FailureClassification.ENVIRONMENT
        return FailureClassification.MOD

    # Session / game
    if failure.type == ErrorCategory.SESSION_ERROR.value:
        return FailureClassification.ENVIRONMENT
    if failure.type == ErrorCategory.GAME_ERROR.value:
        return FailureClassification.MOD

    return FailureClassification.UNKNOWN


# ── L2 stack trace parser ─────────────────────────────────────


def _parse_stack_trace(stack_trace: str | None) -> list[str]:
    """Parse stack trace text and extract 'file:line' location strings.

    Handles Python traceback (File "...", line N) and C# stack traces
    (at ... in ....cs:line N). Returns empty list for unrecognized formats
    or None input.
    """
    if not stack_trace:
        return []

    locations: list[str] = []

    for match in _PYTHON_TRACEBACK_RE.finditer(stack_trace):
        locations.append(f"{match.group(1)}:{match.group(2)}")

    for match in _CSHARP_STACK_RE.finditer(stack_trace):
        locations.append(f"{match.group(1)}:{match.group(2)}")

    return locations


def _enrich_with_stack_locations(
    suggestions: list[RepairSuggestion],
    locations: list[str],
) -> list[RepairSuggestion]:
    """Enrich suggestions with source_location from parsed stack frames (L2).

    Iterates through suggestions and locations in parallel. Each suggestion
    without a pre-existing source_location gets the next available location.
    Confidence is boosted by +0.2, capped at 0.80.
    """
    enriched: list[RepairSuggestion] = []
    loc_iter = iter(locations)

    for s in suggestions:
        if s.source_location is not None:
            enriched.append(s)
            continue

        try:
            loc = next(loc_iter)
        except StopIteration:
            enriched.append(s)
            continue

        new_confidence = min(s.confidence + 0.2, 0.80)
        enriched.append(s.model_copy(update={
            "source_location": loc,
            "confidence": new_confidence,
        }))

    return enriched


# ── RepairAdvisor ─────────────────────────────────────────────


class RepairAdvisor:
    """Generate structured repair suggestions from crash evidence (B10).

    L1 (rule engine) + L2 (stack trace parsing) run automatically.
    L3 (replay capture) is triggered when enable_replay=True and all
    suggestions have confidence below threshold.
    """

    def __init__(self, *, enable_replay: bool = False) -> None:
        self._enable_replay = enable_replay

    def analyze(self, summary: SummaryJson) -> RepairReport | None:
        """Analyze a SummaryJson and produce a RepairReport (协议层 B20).

        Extended with failure classification for autofix routing.

        Returns None when summary.failure is None (no failure to analyze).
        """
        if summary.failure is None:
            return None

        start = time.monotonic()
        failure = summary.failure

        # B20: classify failure before rule matching
        classification = _classify_failure(failure)

        # L1: rule matching
        suggestions = _match_rules(failure)
        source = "rule_engine"

        # Fallback: when no L1 rules match (e.g. raw Python exception types),
        # produce a generic investigation suggestion so L2 enrichment has
        # something to work with. Matches analyze_from_exception() behavior.
        if not suggestions:
            suggestions.append(RepairSuggestion(
                confidence=0.25,
                category="investigation_needed",
                title=f"未捕获的 {failure.type} 异常",
                description=f"从 FailureInfo 记录的 {failure.type}: {failure.message}",
            ))

        # Add classification info to first suggestion description
        if suggestions:
            first = suggestions[0]
            class_msg = f"[分类: {classification.value}] "
            suggestions[0] = first.model_copy(update={
                "description": class_msg + first.description,
            })

        # L2: stack trace parsing + enrichment
        if failure.stack_trace:
            locations = _parse_stack_trace(failure.stack_trace)
            if locations:
                suggestions = _enrich_with_stack_locations(suggestions, locations)
                source = "rule_engine+stack_trace"

        duration_ms = (time.monotonic() - start) * 1000.0

        # Generate deterministic crash signature matching recovery.crash_signature() semantics
        code = str(failure.exit_code) if failure.exit_code is not None else "none"
        signature = f"{classification.value}:{failure.type}:{code}"

        return RepairReport(
            crash_signature=signature,
            suggestions=suggestions,
            generated_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            analysis_duration_ms=round(duration_ms, 2),
        )

    @staticmethod
    def analyze_from_exception(
        exc: Exception,
        exit_code: int | None,
        game_state: dict[str, object] | None,
    ) -> RepairReport:
        """Generate a RepairReport from a raw exception object.

        Used by L3 replay path — captures first-hand exception data
        at the crash point instead of reading from persisted FailureInfo.

        Args:
            exc: The caught exception.
            exit_code: Process exit code if available (e.g. from subprocess).
            game_state: GameState snapshot at crash point (reserved for future use).
        """
        start = time.monotonic()

        error_type = type(exc).__name__
        message = str(exc)
        stack_trace = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )

        failure = FailureInfo(
            type=error_type,
            message=message,
            stack_trace=stack_trace,
        )

        suggestions = _match_rules(failure)
        source = "rule_engine"

        # Fallback: when no L1 rules match (e.g. raw Python exception types),
        # produce a generic investigation suggestion so L2 enrichment has
        # something to work with.
        if not suggestions:
            suggestions.append(RepairSuggestion(
                confidence=0.25,
                category="investigation_needed",
                title=f"未捕获的 {error_type} 异常",
                description=f"从异常对象直接捕获的 {error_type}: {message}",
            ))

        locations = _parse_stack_trace(stack_trace)
        if locations:
            suggestions = _enrich_with_stack_locations(suggestions, locations)
            source = "rule_engine+stack_trace"

        duration_ms = (time.monotonic() - start) * 1000.0
        code = (
            str(exit_code) if exit_code is not None else "none"
        )

        return RepairReport(
            crash_signature=f"{error_type}:{code}",
            suggestions=suggestions,
            generated_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            analysis_duration_ms=round(duration_ms, 2),
        )
