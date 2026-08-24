"""单元测试：CI Deploy Gawain 的瞬时错误判定与路径/校验逻辑。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".github" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_SPEC = importlib.util.spec_from_file_location(
    "deploy_gawain_script",
    _SCRIPTS_DIR / "deploy_gawain.py",
)
assert _SPEC and _SPEC.loader
deploy_gawain = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = deploy_gawain
_SPEC.loader.exec_module(deploy_gawain)

is_transient_godot_export_error = deploy_gawain.is_transient_godot_export_error
resolve_godot_bin = deploy_gawain.resolve_godot_bin
resolve_game_paths = deploy_gawain.resolve_game_paths
verify_deploy_artifacts = deploy_gawain.verify_deploy_artifacts
deploy_with_retry = deploy_gawain.deploy_with_retry


def test_is_transient_godot_export_error_matches_real_failure_log() -> None:
    log = """
  [ DONE ] savepack
EXEC : error : Condition "!EditorSettings::get_singleton() || !EditorSettings::get_singleton()->has_setting(p_setting)" is true.
error MSB3073: 命令“Godot --headless --export-pack "BasicExport" .../gawain.pck”已退出，代码为 -1。
"""
    assert is_transient_godot_export_error(log) is True


def test_is_transient_godot_export_error_rejects_unrelated_failure() -> None:
    log = "error CS1002: ; expected\nBuild FAILED."
    assert is_transient_godot_export_error(log) is False
    assert is_transient_godot_export_error("") is False


def test_resolve_godot_bin_prefers_explicit_executable(tmp_path: Path) -> None:
    godot = tmp_path / "Godot"
    godot.write_text("#!/bin/sh\n", encoding="utf-8")
    godot.chmod(0o755)
    assert resolve_godot_bin(str(godot)) == godot


def test_resolve_godot_bin_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GODOT_PATH", raising=False)
    with patch.object(deploy_gawain.Path, "is_file", return_value=False):
        assert resolve_godot_bin("/no/such/godot") is None


def test_resolve_game_paths_default_and_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STS2_GAME_DIR", raising=False)
    install, mods = resolve_game_paths(str(tmp_path / "Slay the Spire 2"))
    assert install.name == "Slay the Spire 2"
    assert mods.name == "mods"
    assert "SlayTheSpire2.app" in str(mods)


def test_verify_deploy_artifacts_lists_missing(tmp_path: Path) -> None:
    mod = tmp_path / "Gawain"
    mod.mkdir()
    (mod / "Gawain.dll").write_bytes(b"x")
    missing = verify_deploy_artifacts(tmp_path)
    assert "Gawain.dll" not in missing
    assert "gawain.pck" in missing


def test_deploy_with_retry_retries_transient_once(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    log_path = tmp_path / "deploy.log"
    godot = tmp_path / "Godot"
    godot.write_text("#!/bin/sh\n", encoding="utf-8")
    godot.chmod(0o755)
    game = tmp_path / "game"
    mods = game / "mods"
    mods.mkdir(parents=True)

    publish_rcs = iter([1, 0])
    transient_log = "EditorSettings::get_singleton\nerror MSB3073\n--export-pack\n"

    def fake_publish(*_a: object, **_k: object) -> int:
        rc = next(publish_rcs)
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        log_path.write_text(existing + transient_log + f"exit={rc}\n", encoding="utf-8")
        return rc

    with (
        patch.object(deploy_gawain, "warmup_godot", return_value=0) as warmup,
        patch.object(deploy_gawain, "publish_gawain", side_effect=fake_publish) as publish,
        patch.object(deploy_gawain.time, "sleep"),
    ):
        rc = deploy_with_retry(
            project,
            godot=godot,
            game_dir=game,
            mods_dir=mods,
            log_path=log_path,
            max_attempts=2,
            sleep_seconds=0,
        )

    assert rc == 0
    assert publish.call_count == 2
    assert warmup.call_count >= 2


def test_deploy_with_retry_does_not_retry_non_transient(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    log_path = tmp_path / "deploy.log"
    godot = tmp_path / "Godot"
    godot.write_text("#!/bin/sh\n", encoding="utf-8")
    godot.chmod(0o755)

    def fake_publish(*_a: object, **_k: object) -> int:
        log_path.write_text("error CS0006: Metadata file not found\n", encoding="utf-8")
        return 1

    with (
        patch.object(deploy_gawain, "warmup_godot", return_value=0),
        patch.object(deploy_gawain, "publish_gawain", side_effect=fake_publish) as publish,
        patch.object(deploy_gawain.time, "sleep"),
    ):
        rc = deploy_with_retry(
            project,
            godot=godot,
            game_dir=tmp_path / "game",
            mods_dir=tmp_path / "mods",
            log_path=log_path,
            max_attempts=2,
        )

    assert rc == 1
    assert publish.call_count == 1
