"""Unit tests for core/repair_advisor.py — RepairAdvisor, rule engine, stack parser (B10)."""

from __future__ import annotations

import pytest

from sts2_autotest.common.evidence import (
    FailureInfo,
    RepairReport,
    RepairSuggestion,
    SummaryJson,
    RunInfo,
    EnvironmentInfo,
)
from sts2_autotest.core.repair_advisor import (
    RepairAdvisor,
    _match_rules,
    _parse_stack_trace,
    _enrich_with_stack_locations,
)


# ── L1 Rule Engine ────────────────────────────────────────────

class TestMatchRules:
    """Tests for _match_rules() — L1 rule engine."""

    def test_crash_error_rule(self) -> None:
        failure = FailureInfo(type="crash_error", message="游戏崩溃")
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.category == "code_fix"
        assert s.confidence == 0.50
        assert "异常退出" in s.title

    def test_adapter_error_version_mismatch(self) -> None:
        failure = FailureInfo(
            type="adapter_error",
            message="version_mismatch: expected 1.0 got 0.9",
        )
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.category == "config_change"
        assert s.confidence == 0.60
        assert "版本" in s.title

    def test_timeout_error_rule(self) -> None:
        failure = FailureInfo(type="timeout_error", message="操作超时")
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.category == "code_fix"
        assert s.confidence == 0.45

    def test_assertion_error_rule_basic(self) -> None:
        failure = FailureInfo(type="assertion_error", message="断言失败")
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.category == "code_fix"
        assert s.confidence == 0.55
        assert "断言" in s.title

    def test_assertion_error_with_expected_actual(self) -> None:
        failure = FailureInfo(
            type="assertion_error",
            message="状态不匹配",
            expected="MAP",
            actual="COMBAT",
        )
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert "MAP" in s.description
        assert "COMBAT" in s.description

    def test_session_error_rule(self) -> None:
        failure = FailureInfo(type="session_error", message="会话失败")
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.category == "env_fix"
        assert s.confidence == 0.40

    def test_game_error_rule(self) -> None:
        failure = FailureInfo(type="game_error", message="游戏内部错误")
        suggestions = _match_rules(failure)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.category == "investigation_needed"
        assert s.confidence == 0.35

    def test_unknown_type_returns_empty(self) -> None:
        failure = FailureInfo(type="unknown_error_type", message="???")
        suggestions = _match_rules(failure)
        assert suggestions == []

    def test_all_default_fields_set(self) -> None:
        failure = FailureInfo(type="crash_error", message="测试")
        suggestions = _match_rules(failure)
        for s in suggestions:
            assert s.source_location is None
            assert s.patch is None
            assert isinstance(s.related_docs, list)


# ── L2 Stack Trace Parser ─────────────────────────────────────

class TestParseStackTrace:
    """Tests for _parse_stack_trace() — L2 stack trace parsing."""

    def test_python_traceback(self) -> None:
        trace = '''
Traceback (most recent call last):
  File "/app/src/mod.py", line 42, in do_thing
    result = bad_call()
  File "/app/src/utils.py", line 15, in bad_call
    raise ValueError("oops")
ValueError: oops
'''
        locations = _parse_stack_trace(trace)
        assert len(locations) == 2
        assert locations[0] == "/app/src/mod.py:42"
        assert locations[1] == "/app/src/utils.py:15"

    def test_csharp_stack_trace(self) -> None:
        trace = '''Unhandled exception. System.NullReferenceException: Object reference not set
   at GawainCode.CombatManager.DoAttack(Int32 targetId) in /src/GawainCode/CombatManager.cs:line 127
   at GawainCode.GameLoop.Update() in /src/GawainCode/GameLoop.cs:line 53'''
        locations = _parse_stack_trace(trace)
        assert len(locations) == 2
        assert locations[0] == "/src/GawainCode/CombatManager.cs:127"
        assert locations[1] == "/src/GawainCode/GameLoop.cs:53"

    def test_mixed_python_csharp(self) -> None:
        trace = '''File "/app/test.py", line 10, in test_fn
    adapter.act(action)
System.NullReferenceException at GawainCode.Mod.Init() in /src/Mod.cs:line 30'''
        locations = _parse_stack_trace(trace)
        assert len(locations) == 2
        assert locations[0] == "/app/test.py:10"
        assert locations[1] == "/src/Mod.cs:30"

    def test_empty_stack_trace(self) -> None:
        assert _parse_stack_trace("") == []

    def test_none_stack_trace(self) -> None:
        assert _parse_stack_trace(None) == []  # type: ignore[arg-type]

    def test_unrecognized_format(self) -> None:
        trace = "Some random error text without proper traceback format"
        assert _parse_stack_trace(trace) == []


# ── L1+L2 Enrichment ──────────────────────────────────────────

class TestEnrichWithStackLocations:
    """Tests for _enrich_with_stack_locations()."""

    def test_enriches_first_n_suggestions(self) -> None:
        s1 = RepairSuggestion(confidence=0.5, category="code_fix", title="A", description="a")
        s2 = RepairSuggestion(confidence=0.5, category="code_fix", title="B", description="b")
        locations = ["file.py:10"]
        enriched = _enrich_with_stack_locations([s1, s2], locations)
        assert enriched[0].source_location == "file.py:10"
        assert enriched[0].confidence == 0.70  # 0.5 + 0.2
        assert enriched[1].source_location is None  # no location left
        assert enriched[1].confidence == 0.50  # unchanged

    def test_confidence_capped_at_0_80(self) -> None:
        s = RepairSuggestion(confidence=0.75, category="code_fix", title="X", description="x")
        locations = ["file.py:1"]
        enriched = _enrich_with_stack_locations([s], locations)
        assert enriched[0].confidence == 0.80  # capped, not 0.95

    def test_skips_already_located(self) -> None:
        s = RepairSuggestion(
            confidence=0.5, category="code_fix", title="X", description="x",
            source_location="already.py:1",
        )
        locations = ["new.py:2"]
        enriched = _enrich_with_stack_locations([s], locations)
        assert enriched[0].source_location == "already.py:1"  # unchanged
        assert enriched[0].confidence == 0.5  # unchanged since already had location

    def test_empty_locations_no_change(self) -> None:
        s = RepairSuggestion(confidence=0.5, category="code_fix", title="X", description="x")
        enriched = _enrich_with_stack_locations([s], [])
        assert enriched[0].source_location is None
        assert enriched[0].confidence == 0.5

    def test_more_locations_than_suggestions(self) -> None:
        s = RepairSuggestion(confidence=0.5, category="code_fix", title="X", description="x")
        locations = ["a.py:1", "b.py:2", "c.py:3"]
        enriched = _enrich_with_stack_locations([s], locations)
        assert enriched[0].source_location == "a.py:1"
        assert len(enriched) == 1


# ── RepairAdvisor.analyze() ────────────────────────────────────

class TestRepairAdvisorAnalyze:
    """Tests for RepairAdvisor.analyze()."""

    def _make_summary(self, failure: FailureInfo | None) -> SummaryJson:
        return SummaryJson(
            pack_id="test",
            test_run=RunInfo(run_id="r", result="failed", duration_ms=100),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
            failure=failure,
        )

    def test_returns_none_when_no_failure(self) -> None:
        advisor = RepairAdvisor()
        summary = self._make_summary(None)
        assert advisor.analyze(summary) is None

    def test_returns_report_with_suggestions(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(type="crash_error", message="崩溃")
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert len(report.suggestions) >= 1
        assert report.source == "rule_engine"
        assert report.analysis_duration_ms >= 0

    def test_source_is_rule_engine_when_no_stack_trace(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(type="timeout_error", message="超时")
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert report.source == "rule_engine"

    def test_source_is_rule_engine_plus_stack_when_trace_parsed(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(
            type="crash_error",
            message="崩溃",
            stack_trace='File "/app/mod.py", line 42, in foo\n    bar()',
        )
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert report.source == "rule_engine+stack_trace"
        assert report.suggestions[0].source_location == "/app/mod.py:42"

    def test_source_stays_rule_engine_when_trace_unparseable(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(
            type="crash_error",
            message="崩溃",
            stack_trace="garbled nonsense",
        )
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert report.source == "rule_engine"

    def test_fallback_suggestion_when_no_rules_match(self) -> None:
        """When no L1 rules match, a generic investigation suggestion is generated
        (fallback behavior shared with analyze_from_exception)."""
        advisor = RepairAdvisor()
        failure = FailureInfo(type="weird_unknown", message="???")
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert len(report.suggestions) == 1
        assert report.suggestions[0].category == "investigation_needed"
        assert report.suggestions[0].confidence == 0.25
        assert "weird_unknown" in report.suggestions[0].title

    def test_crash_signature_in_report(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(type="crash_error", message="崩溃")
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert "crash_error" in report.crash_signature
        assert ":none" in report.crash_signature  # no exit_code → "none"

    def test_crash_signature_with_exit_code(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(type="crash_error", message="崩溃", exit_code=0xC0000005)
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert ":3221225477" in report.crash_signature  # 0xC0000005 as int


# ── RepairAdvisor.analyze_from_exception() ─────────────────────

class TestAnalyzeFromException:
    """Tests for RepairAdvisor.analyze_from_exception()."""

    def test_returns_report_from_exception(self) -> None:
        try:
            raise ValueError("test error message")
        except ValueError as exc:
            report = RepairAdvisor.analyze_from_exception(
                exc, exit_code=1, game_state=None,
            )

        assert report is not None
        # Python traceback always has File/line patterns -> stack_trace source
        assert report.source == "rule_engine+stack_trace"
        assert report.analysis_duration_ms >= 0
        assert "ValueError" in report.crash_signature

    def test_with_stack_trace_enrichment(self) -> None:
        def inner() -> None:
            raise RuntimeError("deep error")

        try:
            inner()
        except RuntimeError as exc:
            report = RepairAdvisor.analyze_from_exception(
                exc, exit_code=None, game_state=None,
            )

        assert report is not None
        assert report.source == "rule_engine+stack_trace"
        # At least one suggestion should have a source_location
        located = [s for s in report.suggestions if s.source_location is not None]
        assert len(located) >= 1

    def test_with_exit_code_in_signature(self) -> None:
        try:
            raise Exception("boom")
        except Exception as exc:
            report = RepairAdvisor.analyze_from_exception(
                exc, exit_code=255, game_state=None,
            )

        assert ":255" in report.crash_signature

    def test_without_exit_code_uses_none(self) -> None:
        try:
            raise Exception("boom")
        except Exception as exc:
            report = RepairAdvisor.analyze_from_exception(
                exc, exit_code=None, game_state=None,
            )

        assert ":none" in report.crash_signature
