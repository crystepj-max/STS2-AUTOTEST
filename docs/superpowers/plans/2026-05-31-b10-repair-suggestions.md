# B10 Level 2 修复建议 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当测试会话崩溃时，自动分析 crash evidence pack 并生成结构化修复建议（`repair_suggestions.json`）。

**Architecture:** 在 `common/evidence.py` 中新增 `RepairSuggestion` 和 `RepairReport` 两个 frozen pydantic 数据模型，在 `core/repair_advisor.py` 中实现 `RepairAdvisor` 类（L1 规则引擎 + L2 堆栈解析），在 `EvidencePackager.create_pack()` 末尾集成调用。L3 按需重现在本计划中仅保留 `analyze_from_exception` 静态方法入口，完整实现留待后续。

**Tech Stack:** Python >=3.11, pydantic (frozen models), pytest, stdlib `re`/`time`/`datetime`/`traceback`

---

## File Map

| 操作 | 文件 | 职责 |
|------|------|------|
| Modify | `src/sts2_autotest/common/evidence.py` | 新增 `RepairSuggestion`、`RepairReport` 模型；`SummaryJson` 增加 `repair_report` 字段 |
| Create | `src/sts2_autotest/core/repair_advisor.py` | `RepairAdvisor` 类（L1 规则引擎 + L2 堆栈解析）+ `analyze_from_exception` 静态方法 |
| Modify | `src/sts2_autotest/evidence/packager.py` | `create_pack()` 末尾集成 `RepairAdvisor.analyze()` |
| Create | `tests/unit/test_repair_advisor.py` | `RepairAdvisor` 纯函数 + 数据模型单元测试 |
| Create | `tests/integration/test_repair_advisor_integration.py` | 从真实 evidence pack 生成 repair_suggestions.json 的集成测试 |
| Defer | `tests/e2e/test_repair_e2e.py` | 需要真实游戏崩溃环境 + `@pytest.mark.requires_game`，留待 L3 replay 实现后 |

**E2E 测试延迟说明：** 设计文档 7.3 节要求的 `test_full_crash_to_repair_flow` 需要故意触发真实游戏崩溃，依赖完整游戏环境（`@pytest.mark.requires_game`）。Task 4 的集成测试已覆盖"创建 pack → 生成 repair_suggestions.json"的完整管道，E2E 将在 L3 replay 实现后补上。

---

### Task 1: 数据模型 — RepairSuggestion + RepairReport + SummaryJson 扩展

**Files:**
- Modify: `src/sts2_autotest/common/evidence.py`
- Modify: `tests/unit/test_common_evidence.py`

- [ ] **Step 1: 在 test_common_evidence.py 中新增测试类**

在文件末尾追加以下三个测试类：

```python
class TestRepairSuggestion:
    """RepairSuggestion model tests (B10)."""

    def test_create_minimal(self) -> None:
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.5,
            category="code_fix",
            title="测试建议",
            description="这是一条测试建议",
        )
        assert s.confidence == 0.5
        assert s.category == "code_fix"
        assert s.title == "测试建议"
        assert s.description == "这是一条测试建议"
        assert s.source_location is None
        assert s.patch is None
        assert s.related_docs == []

    def test_with_optional_fields(self) -> None:
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.8,
            category="config_change",
            title="配置错误",
            description="需要修改配置文件",
            source_location="src/mod.py:42",
            patch='@@ -1,3 +1,3 @@\n-old\n+new',
            related_docs=["https://example.com/doc"],
        )
        assert s.source_location == "src/mod.py:42"
        assert s.patch is not None
        assert len(s.related_docs) == 1

    def test_frozen(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.5, category="code_fix", title="t", description="d",
        )
        with pytest.raises(ValidationError):
            s.confidence = 0.9  # type: ignore[misc]

    def test_confidence_must_be_float(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.common.evidence import RepairSuggestion

        with pytest.raises(ValidationError):
            RepairSuggestion(
                confidence="high",  # type: ignore[arg-type]
                category="code_fix",
                title="t",
                description="d",
            )

    def test_roundtrip(self) -> None:
        from sts2_autotest.common.evidence import RepairSuggestion

        s = RepairSuggestion(
            confidence=0.75,
            category="env_fix",
            title="环境问题",
            description="检查环境变量",
            source_location="config/settings.yaml:10",
        )
        data = s.model_dump(mode="json")
        restored = RepairSuggestion.model_validate(data)
        assert restored.confidence == 0.75
        assert restored.category == "env_fix"
        assert restored.source_location == "config/settings.yaml:10"


class TestRepairReport:
    """RepairReport model tests (B10)."""

    def test_create_empty(self) -> None:
        from sts2_autotest.common.evidence import RepairReport

        r = RepairReport(
            crash_signature="ValueError:1",
            suggestions=[],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=1.5,
        )
        assert r.crash_signature == "ValueError:1"
        assert r.suggestions == []
        assert r.source == "rule_engine"

    def test_with_suggestions(self) -> None:
        from sts2_autotest.common.evidence import RepairReport, RepairSuggestion

        s = RepairSuggestion(
            confidence=0.6, category="code_fix", title="修复", description="修",
        )
        r = RepairReport(
            crash_signature="crash:0xC0000005",
            suggestions=[s],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine+stack_trace",
            analysis_duration_ms=3.2,
        )
        assert len(r.suggestions) == 1
        assert r.suggestions[0].title == "修复"

    def test_frozen(self) -> None:
        from pydantic import ValidationError
        from sts2_autotest.common.evidence import RepairReport

        r = RepairReport(
            crash_signature="x:0",
            suggestions=[],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=0.0,
        )
        with pytest.raises(ValidationError):
            r.source = "replay_capture"  # type: ignore[misc]

    def test_roundtrip(self) -> None:
        from sts2_autotest.common.evidence import RepairReport, RepairSuggestion

        s = RepairSuggestion(
            confidence=0.5, category="investigation_needed", title="调查", description="需进一步分析",
        )
        r = RepairReport(
            crash_signature="Error:none",
            suggestions=[s],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=5.0,
        )
        data = r.model_dump(mode="json")
        restored = RepairReport.model_validate(data)
        assert restored.crash_signature == "Error:none"
        assert len(restored.suggestions) == 1
        assert restored.suggestions[0].category == "investigation_needed"


class TestSummaryJsonWithRepairReport:
    """SummaryJson.repair_report optional field (B10)."""

    def test_default_is_none(self) -> None:
        from sts2_autotest.common.evidence import (
            SummaryJson, RunInfo, EnvironmentInfo,
        )

        s = SummaryJson(
            pack_id="p1",
            test_run=RunInfo(run_id="r1", result="passed", duration_ms=100),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
        )
        assert s.repair_report is None

    def test_with_repair_report(self) -> None:
        from sts2_autotest.common.evidence import (
            SummaryJson, RunInfo, EnvironmentInfo, RepairReport,
        )

        report = RepairReport(
            crash_signature="e:0",
            suggestions=[],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine",
            analysis_duration_ms=0.0,
        )
        s = SummaryJson(
            pack_id="p2",
            test_run=RunInfo(run_id="r2", result="failed", duration_ms=200),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
            repair_report=report,
        )
        assert s.repair_report is not None
        assert s.repair_report.crash_signature == "e:0"

    def test_roundtrip_with_repair_report(self) -> None:
        from sts2_autotest.common.evidence import (
            SummaryJson, RunInfo, EnvironmentInfo, RepairReport, RepairSuggestion,
        )

        suggestion = RepairSuggestion(
            confidence=0.5, category="code_fix", title="t", description="d",
        )
        report = RepairReport(
            crash_signature="X:1",
            suggestions=[suggestion],
            generated_at="2026-05-31T00:00:00Z",
            source="rule_engine+stack_trace",
            analysis_duration_ms=2.0,
        )
        s = SummaryJson(
            pack_id="p3",
            test_run=RunInfo(run_id="r3", result="crashed", duration_ms=0),
            environment=EnvironmentInfo(
                framework="fw", adapter="ad", game="g", os="os", python="3.11",
            ),
            repair_report=report,
        )
        data = s.model_dump(mode="json")
        restored = SummaryJson.model_validate(data)
        assert restored.repair_report is not None
        assert restored.repair_report.suggestions[0].confidence == 0.5
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
python -m pytest tests/unit/test_common_evidence.py::TestRepairSuggestion -v
```

预期：`ImportError: cannot import name 'RepairSuggestion'`

- [ ] **Step 3: 实现 RepairSuggestion 模型**

在 `src/sts2_autotest/common/evidence.py` 中，在 `class FailureInfo` 之后添加：

```python
class RepairSuggestion(BaseModel):
    """Single repair suggestion generated from crash analysis (B10).

    Fields source_location and patch are None when the analysis cannot
    pinpoint a specific code location — this is a valid state, not an error.
    """

    model_config = ConfigDict(frozen=True)

    confidence: float
    category: str  # code_fix | config_change | env_fix | investigation_needed
    title: str
    description: str
    source_location: str | None = None
    patch: str | None = None
    related_docs: list[str] = []
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
python -m pytest tests/unit/test_common_evidence.py::TestRepairSuggestion -v
```

预期：PASS (4 tests)

- [ ] **Step 5: 运行 RepairReport 测试，验证失败**

```bash
python -m pytest tests/unit/test_common_evidence.py::TestRepairReport -v
```

预期：FAIL — `ImportError: cannot import name 'RepairReport'`

- [ ] **Step 6: 实现 RepairReport 模型**

在 `RepairSuggestion` 之后添加：

```python
class RepairReport(BaseModel):
    """Complete repair analysis report for a single crash event (B10).

    Contains the crash signature, a list of RepairSuggestion entries,
    and metadata about how the analysis was generated.
    """

    model_config = ConfigDict(frozen=True)

    crash_signature: str
    suggestions: list[RepairSuggestion]
    generated_at: str  # ISO 8601 UTC
    source: str  # "rule_engine" | "rule_engine+stack_trace" | "replay_capture"
    analysis_duration_ms: float
```

- [ ] **Step 7: 运行 RepairReport 测试，验证通过**

```bash
python -m pytest tests/unit/test_common_evidence.py::TestRepairReport -v
```

预期：PASS (4 tests)

- [ ] **Step 8: 运行 SummaryJson 测试，验证失败**

```bash
python -m pytest tests/unit/test_common_evidence.py::TestSummaryJsonWithRepairReport -v
```

预期：FAIL — `repair_report` 字段不存在

- [ ] **Step 9: 扩展 SummaryJson，新增 repair_report 字段**

在 `SummaryJson` 类中，在 `artifact_path` 字段之后添加：

```python
    repair_report: RepairReport | None = None
```

- [ ] **Step 10: 运行 SummaryJson 测试，验证通过**

```bash
python -m pytest tests/unit/test_common_evidence.py::TestSummaryJsonWithRepairReport -v
```

预期：PASS (3 tests)

- [ ] **Step 11: 运行全部 common/evidence 测试，确保无回归**

```bash
python -m pytest tests/unit/test_common_evidence.py -v
```

预期：全部 PASS

- [ ] **Step 12: Commit**

```bash
git add src/sts2_autotest/common/evidence.py tests/unit/test_common_evidence.py
git commit -m "feat(b10): add RepairSuggestion and RepairReport data models to common/evidence"
```

---

### Task 2: RepairAdvisor — L1 规则引擎 + L2 堆栈解析

**Files:**
- Create: `src/sts2_autotest/core/repair_advisor.py`
- Create: `tests/unit/test_repair_advisor.py`

- [ ] **Step 1: 创建测试文件并写第一条测试（规则引擎 — crash_error）**

创建 `tests/unit/test_repair_advisor.py`：

```python
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

    def test_empty_suggestions_when_no_rules_match(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(type="weird_unknown", message="???")
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert report.suggestions == []

    def test_crash_signature_in_report(self) -> None:
        advisor = RepairAdvisor()
        failure = FailureInfo(type="crash_error", message="崩溃")
        summary = self._make_summary(failure)
        report = advisor.analyze(summary)
        assert report is not None
        assert "crash_error" in report.crash_signature


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
        assert report.source == "rule_engine"
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
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
python -m pytest tests/unit/test_repair_advisor.py -v
```

预期：全部 FAIL — `ModuleNotFoundError: No module named 'sts2_autotest.core.repair_advisor'`

- [ ] **Step 3: 实现 repair_advisor.py — 模块级函数**

创建 `src/sts2_autotest/core/repair_advisor.py`：

```python
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
        """Analyze a SummaryJson and produce a RepairReport.

        Returns None when summary.failure is None (no failure to analyze).
        """
        if summary.failure is None:
            return None

        start = time.monotonic()
        failure = summary.failure

        # L1: rule matching
        suggestions = _match_rules(failure)
        source = "rule_engine"

        # L2: stack trace parsing + enrichment
        if failure.stack_trace:
            locations = _parse_stack_trace(failure.stack_trace)
            if locations:
                suggestions = _enrich_with_stack_locations(suggestions, locations)
                source = "rule_engine+stack_trace"

        duration_ms = (time.monotonic() - start) * 1000.0

        # Generate deterministic crash signature
        signature = f"{failure.type}:none"

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
        game_state: dict | None,
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
```

- [ ] **Step 4: 运行全部测试，验证通过**

```bash
python -m pytest tests/unit/test_repair_advisor.py -v
```

预期：全部 PASS (25 tests)

- [ ] **Step 5: 运行类型检查**

```bash
mypy src/sts2_autotest/core/repair_advisor.py --strict
```

预期：无错误

- [ ] **Step 6: Commit**

```bash
git add src/sts2_autotest/core/repair_advisor.py tests/unit/test_repair_advisor.py
git commit -m "feat(b10): implement RepairAdvisor with L1 rule engine and L2 stack trace parser"
```

---

### Task 3: 集成 — EvidencePackager.create_pack() 调用 RepairAdvisor

**Files:**
- Modify: `src/sts2_autotest/evidence/packager.py`

- [ ] **Step 1: 在 create_pack() 中集成 RepairAdvisor**

在 `src/sts2_autotest/evidence/packager.py` 的 `create_pack()` 方法中，在 `self._generate_report_for(pack_id, summary)` 调用之后、`self._enforce_retention()` 之前，插入以下代码块：

找到以下行（约第 143 行）：
```python
        # AC6: automatically generate summary.md on pack creation
        self._generate_report_for(pack_id, summary)

        self._enforce_retention()
```

替换为：
```python
        # AC6: automatically generate summary.md on pack creation
        self._generate_report_for(pack_id, summary)

        # B10: generate repair suggestions from failure evidence
        try:
            from sts2_autotest.core.repair_advisor import RepairAdvisor

            advisor = RepairAdvisor()
            report = advisor.analyze(summary)
            if report is not None:
                # Update summary.json with embedded repair report
                updated = summary.model_copy(update={"repair_report": report})
                self._write_json(summary_path, updated.model_dump(mode="json"))

                # Write standalone repair_suggestions.json for CI / AI Agent consumption
                repair_path = pack_dir / "reports" / "repair_suggestions.json"
                self._write_json(repair_path, report.model_dump(mode="json"))
        except Exception:
            logger.warning(
                "Failed to generate repair suggestions for %s", pack_id, exc_info=True,
            )

        self._enforce_retention()
```

- [ ] **Step 2: 运行已有 packager 测试，确保无回归**

```bash
python -m pytest tests/unit/test_packager.py -v
```

预期：全部 PASS（现有测试不应被 B10 集成影响，因为 try/except 包裹了所有新增逻辑）

- [ ] **Step 3: 手动验证 create_pack 生成 repair_suggestions.json**

创建一个简单的验证脚本 `tests/unit/test_packager_b10.py`（临时，后续合并到集成测试）：

```python
"""Smoke test: verify EvidencePackager.create_pack() generates repair_suggestions.json (B10)."""

import json
from pathlib import Path

from sts2_autotest.common.evidence import FailureInfo
from sts2_autotest.evidence.packager import EvidencePackager


def test_create_pack_generates_repair_suggestions(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    pkgr = EvidencePackager(evidence_dir)

    failure = FailureInfo(
        type="crash_error",
        message="游戏崩溃",
        stack_trace='File "/app/mod.py", line 42, in foo\n    bar()',
    )
    pack_dir = pkgr.create_pack("b10_test", run_result="crashed", duration_ms=0, failure=failure)

    # Verify standalone repair_suggestions.json
    repair_path = pack_dir / "reports" / "repair_suggestions.json"
    assert repair_path.is_file(), f"Expected {repair_path} to exist"

    data = json.loads(repair_path.read_text(encoding="utf-8"))
    assert "crash_signature" in data
    assert "suggestions" in data
    assert len(data["suggestions"]) >= 1
    assert data["source"] == "rule_engine+stack_trace"

    # Verify summary.json has embedded repair_report
    summary_data = json.loads((pack_dir / "summary.json").read_text(encoding="utf-8"))
    assert "repair_report" in summary_data
    assert summary_data["repair_report"] is not None
    assert summary_data["repair_report"]["crash_signature"] == data["crash_signature"]


def test_create_pack_no_failure_skips_repair(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    pkgr = EvidencePackager(evidence_dir)

    pack_dir = pkgr.create_pack("b10_pass", run_result="passed", duration_ms=100)

    # repair_suggestions.json should NOT exist (no failure to analyze)
    repair_path = pack_dir / "reports" / "repair_suggestions.json"
    assert not repair_path.is_file()

    # summary.json should have repair_report: null
    summary_data = json.loads((pack_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary_data.get("repair_report") is None
```

- [ ] **Step 4: 运行 smoke 测试**

```bash
python -m pytest tests/unit/test_packager_b10.py -v
```

预期：2 PASS

- [ ] **Step 5: 运行全部单元测试，确保无回归**

```bash
python -m pytest tests/unit/ -v
```

预期：全部 PASS

- [ ] **Step 6: 运行导入隔离检查**

```bash
lint-imports
```

预期：通过（`core/repair_advisor.py` → `common/` 是合法方向）

- [ ] **Step 7: 运行 mypy 类型检查**

```bash
mypy src/sts2_autotest --strict
```

预期：无新增错误

- [ ] **Step 8: Commit**

```bash
git add src/sts2_autotest/evidence/packager.py tests/unit/test_packager_b10.py
git commit -m "feat(b10): integrate RepairAdvisor into EvidencePackager.create_pack()"
```

---

### Task 4: 集成测试 — 真实 evidence pack 验证

**Files:**
- Create: `tests/integration/test_repair_advisor_integration.py`

- [ ] **Step 1: 创建集成测试文件**

创建 `tests/integration/test_repair_advisor_integration.py`：

```python
"""Integration tests for B10 repair suggestions with real evidence packs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sts2_autotest.common.evidence import FailureInfo
from sts2_autotest.evidence.packager import EvidencePackager


@pytest.mark.integration
class TestRepairAdvisorIntegration:
    """Integration tests using real EvidencePackager + RepairAdvisor pipeline."""

    def test_generates_repair_suggestions_json(self, tmp_path: Path) -> None:
        """Full pipeline: create pack with failure → repair_suggestions.json exists and is valid."""
        evidence_dir = tmp_path / "evidence"
        pkgr = EvidencePackager(evidence_dir)

        failure = FailureInfo(
            type="crash_error",
            message="游戏进程异常退出，exit_code=0xC0000005",
            stack_trace=(
                'Traceback (most recent call last):\n'
                '  File "/app/src/game.py", line 200, in update\n'
                '    self.mods.tick()\n'
                '  File "/app/src/mod_loader.py", line 55, in tick\n'
                '    mod.on_update()\n'
                'RuntimeError: access violation\n'
            ),
        )

        pack_dir = pkgr.create_pack(
            "integration_b10_test",
            run_result="crashed",
            duration_ms=1500,
            failure=failure,
        )

        # 1. repair_suggestions.json exists
        repair_path = pack_dir / "reports" / "repair_suggestions.json"
        assert repair_path.is_file(), f"Missing {repair_path}"

        # 2. Valid JSON structure
        data = json.loads(repair_path.read_text(encoding="utf-8"))
        assert "crash_signature" in data
        assert "suggestions" in data
        assert "generated_at" in data
        assert "source" in data
        assert "analysis_duration_ms" in data

        # 3. Has at least one suggestion for crash_error
        suggestions = data["suggestions"]
        assert len(suggestions) >= 1

        # 4. First suggestion has source_location from stack trace
        first = suggestions[0]
        assert first["source_location"] is not None
        assert "game.py" in first["source_location"] or "mod_loader.py" in first["source_location"]

        # 5. summary.json also has embedded repair_report
        summary_data = json.loads(
            (pack_dir / "summary.json").read_text(encoding="utf-8"),
        )
        assert "repair_report" in summary_data
        assert summary_data["repair_report"] is not None
        assert summary_data["repair_report"]["crash_signature"] == data["crash_signature"]

    def test_missing_failure_is_noop(self, tmp_path: Path) -> None:
        """When summary has no failure, no repair_suggestions.json is generated."""
        evidence_dir = tmp_path / "evidence"
        pkgr = EvidencePackager(evidence_dir)

        pack_dir = pkgr.create_pack(
            "integration_b10_pass",
            run_result="passed",
            duration_ms=500,
        )

        # repair_suggestions.json should not exist
        repair_path = pack_dir / "reports" / "repair_suggestions.json"
        assert not repair_path.is_file()

        # summary.json repair_report should be None
        summary_data = json.loads(
            (pack_dir / "summary.json").read_text(encoding="utf-8"),
        )
        assert summary_data.get("repair_report") is None

    def test_all_error_categories_generate_report(self, tmp_path: Path) -> None:
        """Each of the 6 error categories should produce at least one suggestion."""
        categories = [
            ("crash_error", "崩溃"),
            ("adapter_error", "version_mismatch detected"),
            ("timeout_error", "操作超时"),
            ("assertion_error", "断言失败"),
            ("session_error", "会话错误"),
            ("game_error", "游戏内部错误"),
        ]

        evidence_dir = tmp_path / "evidence"
        pkgr = EvidencePackager(evidence_dir)

        for i, (error_type, message) in enumerate(categories):
            failure = FailureInfo(type=error_type, message=message)
            pack_dir = pkgr.create_pack(
                f"cat_test_{i}",
                run_result="failed",
                duration_ms=100,
                failure=failure,
            )

            repair_path = pack_dir / "reports" / "repair_suggestions.json"
            assert repair_path.is_file(), f"Missing report for {error_type}"

            data = json.loads(repair_path.read_text(encoding="utf-8"))
            assert len(data["suggestions"]) >= 1, f"No suggestions for {error_type}"
```

- [ ] **Step 2: 运行集成测试**

```bash
python -m pytest tests/integration/test_repair_advisor_integration.py -v
```

预期：3 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_repair_advisor_integration.py
git commit -m "test(b10): add integration tests for repair advisor with real evidence packs"
```

---

### Task 5: 最终验证

- [ ] **Step 1: 运行全部单元测试**

```bash
python -m pytest tests/unit/ -v
```

预期：全部 PASS（包括新增的 test_repair_advisor.py 和 test_common_evidence.py 测试）

- [ ] **Step 2: 运行全部集成测试**

```bash
python -m pytest tests/integration/ -v
```

预期：全部 PASS（包括新增的 test_repair_advisor_integration.py）

- [ ] **Step 3: 运行 mypy 类型检查**

```bash
mypy src/sts2_autotest --strict
```

预期：无新增错误

- [ ] **Step 4: 运行导入隔离检查**

```bash
lint-imports
```

预期：通过

- [ ] **Step 5: Cleanup — 将 smoke 测试合并到 test_packager.py**

将 `tests/unit/test_packager_b10.py` 中的两个测试函数合并到 `tests/unit/test_packager.py` 末尾，作为一个新的测试类 `TestPackagerB10`，然后删除临时文件。遵循 `tests/unit/` 中每个源文件对应一个测试的惯例。

合并内容（追加到 `tests/unit/test_packager.py` 末尾）：

```python
# ── B10 repair suggestions integration ────────────────────────

class TestPackagerB10:
    """Smoke tests: EvidencePackager.create_pack() generates repair_suggestions.json (B10)."""

    def test_create_pack_generates_repair_suggestions(self, tmp_path: Path) -> None:
        evidence_dir = tmp_path / "evidence"
        pkgr = EvidencePackager(evidence_dir)
        failure = FailureInfo(
            type="crash_error",
            message="游戏崩溃",
            stack_trace='File "/app/mod.py", line 42, in foo\n    bar()',
        )
        pack_dir = pkgr.create_pack(
            "b10_test", run_result="crashed", duration_ms=0, failure=failure,
        )
        repair_path = pack_dir / "reports" / "repair_suggestions.json"
        assert repair_path.is_file()
        data = json.loads(repair_path.read_text(encoding="utf-8"))
        assert len(data["suggestions"]) >= 1
        assert data["source"] == "rule_engine+stack_trace"

    def test_create_pack_no_failure_skips_repair(self, tmp_path: Path) -> None:
        evidence_dir = tmp_path / "evidence"
        pkgr = EvidencePackager(evidence_dir)
        pack_dir = pkgr.create_pack("b10_pass", run_result="passed", duration_ms=100)
        repair_path = pack_dir / "reports" / "repair_suggestions.json"
        assert not repair_path.is_file()
        summary_data = json.loads(
            (pack_dir / "summary.json").read_text(encoding="utf-8"),
        )
        assert summary_data.get("repair_report") is None
```

然后删除临时文件：

```bash
rm tests/unit/test_packager_b10.py
```

- [ ] **Step 6: 最终 commit**

```bash
git add -A
git commit -m "chore(b10): finalize B10 repair suggestions — all tests passing, mypy clean"
```
