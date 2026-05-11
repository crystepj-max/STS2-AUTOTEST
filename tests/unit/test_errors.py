"""Tests for common/errors.py — ErrorCategory, ErrorSubType, STS2Error."""

from datetime import datetime, timezone

import pytest

from sts2_autotest.common.errors import (
    AdapterErrorSubType,
    ErrorCategory,
    STS2Error,
)


class TestErrorCategory:
    """ErrorCategory StrEnum tests."""

    def test_has_all_five_categories(self) -> None:
        expected = {
            "ADAPTER_ERROR", "GAME_ERROR", "ASSERTION_ERROR",
            "CRASH_ERROR", "TIMEOUT_ERROR",
        }
        actual = {c.name for c in ErrorCategory}
        assert expected == actual

    def test_values_are_snake_case(self) -> None:
        for cat in ErrorCategory:
            assert cat.value == cat.name.lower()

    def test_is_string_enum(self) -> None:
        assert isinstance(ErrorCategory.ADAPTER_ERROR, str)


class TestAdapterErrorSubType:
    """AdapterErrorSubType StrEnum tests."""

    def test_has_all_subtypes(self) -> None:
        expected = {"TIMEOUT", "JSON_PARSE_FAILURE", "PROCESS_EXIT", "NONZERO_EXIT_CODE", "VERSION_MISMATCH"}
        actual = {s.name for s in AdapterErrorSubType}
        assert expected == actual

    def test_is_string_enum(self) -> None:
        assert isinstance(AdapterErrorSubType.TIMEOUT, str)


class TestSTS2Error:
    """STS2Error base exception tests."""

    def test_create_with_required_fields(self) -> None:
        err = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Connection timeout",
        )
        assert err.category == ErrorCategory.ADAPTER_ERROR
        assert err.message == "Connection timeout"
        assert isinstance(err.detail, dict)
        assert isinstance(err.timestamp, datetime)

    def test_create_with_detail(self) -> None:
        detail = {"command": "get_state", "timeout_secs": 30, "adapter": "CliMod"}
        err = STS2Error(
            category=ErrorCategory.ADAPTER_ERROR,
            message="Timeout",
            detail=detail,
        )
        assert err.detail == detail

    def test_timestamp_defaults_to_utc_now(self) -> None:
        before = datetime.now(timezone.utc)
        err = STS2Error(category=ErrorCategory.GAME_ERROR, message="test")
        after = datetime.now(timezone.utc)
        assert before <= err.timestamp <= after

    def test_custom_timestamp(self) -> None:
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        err = STS2Error(
            category=ErrorCategory.CRASH_ERROR,
            message="test",
            timestamp=ts,
        )
        assert err.timestamp == ts

    def test_is_exception(self) -> None:
        err = STS2Error(category=ErrorCategory.TIMEOUT_ERROR, message="test")
        assert isinstance(err, Exception)
        with pytest.raises(STS2Error):
            raise err

    def test_to_dict_structure(self) -> None:
        ts = datetime(2026, 5, 10, 8, 0, 0, tzinfo=timezone.utc)
        err = STS2Error(
            category=ErrorCategory.ASSERTION_ERROR,
            message="HP mismatch",
            detail={"expected": 50, "actual": 30},
            timestamp=ts,
        )
        result = err.to_dict()
        assert result["type"] == ErrorCategory.ASSERTION_ERROR
        assert result["message"] == "HP mismatch"
        assert result["detail"] == {"expected": 50, "actual": 30}
        assert "2026-05-10" in result["timestamp"]
