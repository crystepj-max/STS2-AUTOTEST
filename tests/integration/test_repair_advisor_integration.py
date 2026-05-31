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
