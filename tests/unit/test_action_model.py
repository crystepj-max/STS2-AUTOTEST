"""Tests for core/action_model.py — ActionDescriptor and TestResult."""

import pytest

from sts2_autotest.common.state import GameScreen
from sts2_autotest.core.action_model import ActionDescriptor, TestResult


class TestActionDescriptor:
    """ActionDescriptor frozen dataclass tests."""

    def test_required_only(self) -> None:
        d = ActionDescriptor(action_type="play_card")
        assert d.action_type == "play_card"
        assert d.params == {}
        assert d.expected_state is None
        assert d.timeout == 30.0

    def test_full_fields(self) -> None:
        d = ActionDescriptor(
            action_type="play_card",
            params={"card_id": "Strike"},
            expected_state=GameScreen.COMBAT,
            timeout=5.0,
        )
        assert d.params == {"card_id": "Strike"}
        assert d.expected_state == GameScreen.COMBAT
        assert d.timeout == 5.0

    def test_frozen(self) -> None:
        d = ActionDescriptor(action_type="end_turn")
        with pytest.raises(Exception):
            d.action_type = "other"  # type: ignore[misc]


class TestTestResult:
    """TestResult dataclass tests."""

    def test_pass_result(self) -> None:
        r = TestResult(case_id="TC-001", status="pass")
        assert r.case_id == "TC-001"
        assert r.status == "pass"
        assert r.detail is None

    def test_fail_with_detail(self) -> None:
        r = TestResult(case_id="TC-002", status="fail", detail="HP mismatch")
        assert r.status == "fail"
        assert r.detail == "HP mismatch"

    def test_all_status_values(self) -> None:
        for status in ("pass", "fail", "crash", "skip"):
            r = TestResult(case_id="x", status=status)
            assert r.status == status
