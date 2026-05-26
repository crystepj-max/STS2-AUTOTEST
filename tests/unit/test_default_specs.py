"""Tests for the default natural-language spec assets."""

from __future__ import annotations

from pathlib import Path

def test_default_spec_directories_are_present() -> None:
    assert Path("specs/cases").is_dir()
    assert Path("specs/suites").is_dir()
