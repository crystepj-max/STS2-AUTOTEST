"""scripts/p1_v11_acceptance.py 截图机器判定检查（V11 复核：禁止假通过路径）。"""

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from p1_v11_acceptance import (  # noqa: E402
    _final_state_has_no_saved_run,
    _jpeg_dimensions,
    _verify_final_screenshot,
)


def _jpeg(width: int, height: int, total_kb: float = 60.0) -> bytes:
    """构造带合法 SOF0 的最小 JPEG 字节流（可填充到指定体积）。"""
    header = bytes([0xFF, 0xD8])
    sof_payload = (
        bytes([8])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    sof = bytes([0xFF, 0xC0]) + (len(sof_payload) + 2).to_bytes(2, "big") + sof_payload
    body = header + sof
    target = int(total_kb * 1024)
    return body + b"\x00" * max(0, target - len(body))


def _pack_with_screenshot(tmp_path: Path, data: bytes | None) -> dict:
    """构造含 evidence_pack_url 的假报告（zip 内放/不放 recovery_final 截图）。"""
    pack = tmp_path / "pack.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("summary.json", json.dumps({"status": "CANCELLED"}))
        if data is not None:
            archive.writestr("screenshots/journey_new_run_recovery_final.jpg", data)
    return {"evidence_pack_url": str(pack)}


class TestJpegDimensions:
    def test_valid_jpeg_parsed(self) -> None:
        assert _jpeg_dimensions(_jpeg(1920, 1080)) == (1920, 1080)

    def test_non_jpeg_returns_none(self) -> None:
        assert _jpeg_dimensions(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) is None

    def test_garbage_returns_none(self) -> None:
        assert _jpeg_dimensions(b"\x00" * 60 * 1024) is None

    def test_truncated_returns_none(self) -> None:
        assert _jpeg_dimensions(_jpeg(1920, 1080)[:6]) is None


class TestVerifyFinalScreenshot:
    def test_missing_screenshot_fails(self, tmp_path: Path) -> None:
        result = _verify_final_screenshot(_pack_with_screenshot(tmp_path, None))
        assert result["ok"] is False
        assert "missing" in (result["reason"] or "")

    def test_tiny_screenshot_fails(self, tmp_path: Path) -> None:
        result = _verify_final_screenshot(
            _pack_with_screenshot(tmp_path, _jpeg(1920, 1080, total_kb=4))
        )
        assert result["ok"] is False
        assert "too small" in (result["reason"] or "")

    def test_unparseable_dimensions_fails(self, tmp_path: Path) -> None:
        """V11 复核复现：尺寸读取失败不得假通过。"""
        result = _verify_final_screenshot(
            _pack_with_screenshot(tmp_path, b"\x00" * 60 * 1024)
        )
        assert result["ok"] is False
        assert "cannot parse" in (result["reason"] or "")

    def test_small_dimensions_fails(self, tmp_path: Path) -> None:
        result = _verify_final_screenshot(
            _pack_with_screenshot(tmp_path, _jpeg(640, 480))
        )
        assert result["ok"] is False
        assert "dimensions" in (result["reason"] or "")

    def test_valid_screenshot_passes(self, tmp_path: Path) -> None:
        result = _verify_final_screenshot(
            _pack_with_screenshot(tmp_path, _jpeg(1920, 1080))
        )
        assert result["ok"] is True
        assert result["dimensions"] == "1920x1080"
        assert result["size_kb"] >= 50

    def test_no_report_fails(self) -> None:
        assert _verify_final_screenshot(None)["ok"] is False
        assert _verify_final_screenshot({})["ok"] is False


class TestFinalStateHasNoSavedRun:
    def test_explicit_no_save_passes(self) -> None:
        assert _final_state_has_no_saved_run(
            {"has_run_save": False, "has_new_run_action": True}
        )

    def test_missing_field_uses_clean_action_surface(self) -> None:
        assert _final_state_has_no_saved_run(
            {
                "has_run_save": None,
                "has_new_run_action": True,
                "available_actions": ["open_character_select", "open_timeline"],
            }
        )

    def test_missing_field_with_continue_run_fails(self) -> None:
        assert not _final_state_has_no_saved_run(
            {
                "has_run_save": None,
                "has_new_run_action": True,
                "available_actions": ["continue_run", "abandon_run"],
            }
        )
