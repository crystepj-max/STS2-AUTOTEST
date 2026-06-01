"""Unit tests for core/evidence_hooks.py — B10 failure tracking behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from sts2_autotest.common.errors import ErrorCategory, STS2Error
from sts2_autotest.common.evidence import FailureInfo
from sts2_autotest.common.types import CaptureResult
from sts2_autotest.core.action_model import TestResult
from sts2_autotest.core.evidence_hooks import RealEvidenceHooks


# ── Helpers ────────────────────────────────────────────────────


def _make_capture_result(status: str = "ok") -> CaptureResult:
    return CaptureResult(status=status, path=Path("/tmp/shot.png"))


@dataclass
class _FakeCapture:
    """Minimal ScreenCaptureProtocol stub for testing."""

    def capture_with_validation(
        self, window_title: str, case_id: str,
    ) -> CaptureResult:
        return _make_capture_result()

    def capture(
        self, window_title: str, case_id: str = "unknown",
    ) -> CaptureResult:
        return _make_capture_result()


class _FakePackager:
    """Minimal EvidencePackagerProtocol stub that records create_pack args."""

    def __init__(self) -> None:
        self.last_failure: FailureInfo | None = None
        self.last_run_result: str = "unknown"

    def create_pack(
        self,
        pack_id: str | None = None,
        *,
        run_result: str = "unknown",
        duration_ms: int = 0,
        failure: FailureInfo | None = None,
    ) -> object:
        self.last_failure = failure
        self.last_run_result = run_result
        return Path("/tmp/pack")

    def export_artifact(self, pack_id: str, result: str = "unknown") -> object:
        return None

    def list_packs(self) -> list[object]:
        return []


@pytest.fixture
def fake_capture() -> _FakeCapture:
    return _FakeCapture()


@pytest.fixture
def fake_packager() -> _FakePackager:
    return _FakePackager()


@pytest.fixture
def hooks(
    fake_capture: _FakeCapture,
    fake_packager: _FakePackager,
) -> RealEvidenceHooks:
    return RealEvidenceHooks(
        capture=fake_capture,  # type: ignore[arg-type]
        packager=fake_packager,  # type: ignore[arg-type]
    )


# ── _last_failure lifecycle ─────────────────────────────────────


class TestRealEvidenceHooksB10FailureTracking:
    """Tests for B10 failure tracking in RealEvidenceHooks."""

    def test_last_failure_is_none_initially(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        assert hooks._last_failure is None

    def test_last_failure_reset_after_session_end(
        self, hooks: RealEvidenceHooks, fake_packager: _FakePackager,
    ) -> None:
        """on_session_end 后 _last_failure 必须 reset，避免下个 passed session
        继承了上轮 crash 的旧 failure。"""
        # Simulate a crash first
        hooks.on_crash("case_1", RuntimeError("boom"))
        assert hooks._last_failure is not None

        # End the session
        hooks.on_session_end({"passed": 0, "failed": 0, "crashed": 1})

        # Must be reset
        assert hooks._last_failure is None
        # And packager received the failure
        assert fake_packager.last_failure is not None
        assert fake_packager.last_failure.type == "RuntimeError"

    def test_passed_session_does_not_leak_failure(
        self, hooks: RealEvidenceHooks, fake_packager: _FakePackager,
    ) -> None:
        """First session crashes, second passes — second pack must have no failure."""
        # Session 1: crash
        hooks.on_crash("c1", RuntimeError("boom1"))
        hooks.on_session_end({"passed": 0, "failed": 0, "crashed": 1})
        assert fake_packager.last_failure is not None

        # Session 2: all pass, no crash
        hooks.on_case_end(TestResult("c2", "pass"))
        hooks.on_session_end({"passed": 1, "failed": 0, "crashed": 0})
        assert fake_packager.last_failure is None


# ── on_case_end 兜底 ────────────────────────────────────────────


class TestOnCaseEndFailureCapture:
    """Tests for failure capture via on_case_end when on_crash didn't fire."""

    def test_captures_on_fail_status(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        hooks.on_case_end(TestResult("c1", "fail", "HP mismatch"))
        assert hooks._last_failure is not None
        assert hooks._last_failure.type == "assertion_error"
        assert hooks._last_failure.message == "HP mismatch"

    def test_captures_on_crash_status(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        hooks.on_case_end(TestResult("c1", "crash", "game died"))
        assert hooks._last_failure is not None
        assert hooks._last_failure.type == "crash_error"
        assert hooks._last_failure.message == "game died"

    def test_captures_on_deterministic_fail_status(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        hooks.on_case_end(TestResult("c1", "deterministic_fail", "consecutive"))
        assert hooks._last_failure is not None
        assert hooks._last_failure.type == "session_error"

    def test_does_not_overwrite_crash_data(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        """on_crash 先触发，on_case_end 后触发 — 不覆盖更详细的 crash 数据。"""
        hooks.on_crash("c1", RuntimeError("real crash"))
        crash_failure = hooks._last_failure
        assert crash_failure is not None
        assert crash_failure.type == "RuntimeError"

        # on_case_end with cruder info should NOT overwrite
        hooks.on_case_end(TestResult("c1", "crash", "game died"))
        assert hooks._last_failure is crash_failure
        assert hooks._last_failure.type == "RuntimeError"  # preserved from on_crash

    def test_passed_case_does_not_set_failure(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        hooks.on_case_end(TestResult("c1", "pass"))
        assert hooks._last_failure is None

    def test_skipped_case_does_not_set_failure(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        hooks.on_case_end(TestResult("c1", "skip", "interrupted"))
        assert hooks._last_failure is None


# ── on_crash 信息提取 ───────────────────────────────────────────


class TestOnCrashInfoExtraction:
    """Tests for FailureInfo extraction from on_crash exceptions."""

    def test_extracts_raw_exception_type(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        hooks.on_crash("c1", ValueError("bad value"))
        assert hooks._last_failure is not None
        assert hooks._last_failure.type == "ValueError"
        assert hooks._last_failure.message == "bad value"
        assert hooks._last_failure.exit_code is None

    def test_extracts_sts2error_category(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        exc = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="游戏崩溃，exit_code=0xC0000005",
        )
        hooks.on_crash("c1", exc)
        assert hooks._last_failure is not None
        assert hooks._last_failure.type == "crash_error"
        assert "0xC0000005" in hooks._last_failure.message

    def test_extracts_exit_code_from_sts2error_detail(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        exc = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="crash",
            detail={"exit_code": 0xC0000005},
        )
        hooks.on_crash("c1", exc)
        assert hooks._last_failure is not None
        assert hooks._last_failure.exit_code == 0xC0000005

    def test_exit_code_none_when_sts2error_has_no_detail(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        exc = STS2Error(
            category=ErrorCategory.GAME_ERROR,
            message="game error",
        )
        hooks.on_crash("c1", exc)
        assert hooks._last_failure is not None
        assert hooks._last_failure.exit_code is None

    def test_stack_trace_is_populated(
        self, hooks: RealEvidenceHooks,
    ) -> None:
        try:
            raise RuntimeError("deep")
        except RuntimeError as exc:
            hooks.on_crash("c1", exc)

        assert hooks._last_failure is not None
        assert hooks._last_failure.stack_trace is not None
        assert "RuntimeError" in hooks._last_failure.stack_trace
        assert "deep" in hooks._last_failure.stack_trace


# ── on_session_end → create_pack 传递 ───────────────────────────


class TestOnSessionEndPackagerIntegration:
    """Tests for failure passing from on_session_end to create_pack."""

    def test_passes_failure_to_create_pack(
        self, hooks: RealEvidenceHooks, fake_packager: _FakePackager,
    ) -> None:
        hooks.on_crash("c1", RuntimeError("boom"))
        hooks.on_session_end({"passed": 0, "failed": 1, "crashed": 0})

        assert fake_packager.last_failure is not None
        assert fake_packager.last_failure.type == "RuntimeError"
        assert fake_packager.last_failure.message == "boom"
        assert fake_packager.last_run_result == "failed"

    def test_passes_none_failure_when_no_crash(
        self, hooks: RealEvidenceHooks, fake_packager: _FakePackager,
    ) -> None:
        hooks.on_session_end({"passed": 3, "failed": 0, "crashed": 0})

        assert fake_packager.last_failure is None
        assert fake_packager.last_run_result == "passed"

    def test_passes_failure_from_on_case_end_fallback(
        self, hooks: RealEvidenceHooks, fake_packager: _FakePackager,
    ) -> None:
        # on_case_end with fail status (on_crash never called)
        hooks.on_case_end(TestResult("c1", "fail", "assertion failed"))
        hooks.on_session_end({"passed": 0, "failed": 1, "crashed": 0})

        assert fake_packager.last_failure is not None
        assert fake_packager.last_failure.type == "assertion_error"
        assert fake_packager.last_failure.message == "assertion failed"
