"""Tests for adapters.project_extension — 项目扩展配置统一读取。"""

from __future__ import annotations

from pathlib import Path

from sts2_autotest.adapters.project_extension import (
    load_card_id_prefixes,
    load_character_aliases,
    load_seed_command_template,
    parse_card_id_prefixes,
)


def _write_yaml_config(base: Path, project_extension: str) -> None:
    (base / "sts2-autotest.yaml").write_text(
        "workspace:\n  projects: []\n" + project_extension,
        encoding="utf-8",
    )


def test_defaults_are_empty_without_config(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
    monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)
    monkeypatch.delenv("STS2_PROJECT__CHARACTER_ALIASES", raising=False)

    assert load_card_id_prefixes(tmp_path) == {}
    assert load_seed_command_template(tmp_path) == ""
    assert load_character_aliases(tmp_path) == {}


def test_reads_project_extension_from_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
    monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)
    monkeypatch.delenv("STS2_PROJECT__CHARACTER_ALIASES", raising=False)
    _write_yaml_config(
        tmp_path,
        "project_extension:\n"
        "  card_id_prefixes:\n"
        "    mymod: MYMOD-\n"
        "  seed_command_template: 'mymod_seed {seed}'\n"
        "  character_aliases:\n"
        "    MyChar: MYMOD-MYCHAR\n",
    )

    assert load_card_id_prefixes(tmp_path) == {"mymod": "MYMOD-"}
    assert load_seed_command_template(tmp_path) == "mymod_seed {seed}"
    assert load_character_aliases(tmp_path) == {"MyChar": "MYMOD-MYCHAR"}


def test_resolves_config_via_mod_manifest_pointer(tmp_path, monkeypatch) -> None:
    """没有常规配置文件时，经 sts2-mod.yaml 的 autotest.config 指针定位。"""
    monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
    monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)
    config_dir = tmp_path / "automation" / "autotest" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sts2-autotest.yaml").write_text(
        "project_extension:\n"
        "  card_id_prefixes:\n"
        "    mymod: MYMOD-\n",
        encoding="utf-8",
    )
    (tmp_path / "sts2-mod.yaml").write_text(
        "mod:\n  id: mymod\n"
        "autotest:\n  config: automation/autotest/config/sts2-autotest.yaml\n",
        encoding="utf-8",
    )

    assert load_card_id_prefixes(tmp_path) == {"mymod": "MYMOD-"}


def test_env_vars_override_yaml(tmp_path, monkeypatch) -> None:
    _write_yaml_config(
        tmp_path,
        "project_extension:\n"
        "  card_id_prefixes:\n"
        "    mymod: MYMOD-\n"
        "  seed_command_template: 'yaml_seed {seed}'\n"
        "  character_aliases:\n"
        "    YamlChar: YAML-CHAR\n",
    )
    monkeypatch.setenv("STS2_PROJECT__CARD_ID_PREFIXES", "other:OTHER-")
    monkeypatch.setenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", "env_seed {seed}")
    monkeypatch.setenv("STS2_PROJECT__CHARACTER_ALIASES", "EnvChar:ENV-CHAR")

    assert load_card_id_prefixes(tmp_path) == {"mymod": "MYMOD-", "other": "OTHER-"}
    assert load_seed_command_template(tmp_path) == "env_seed {seed}"
    assert load_character_aliases(tmp_path) == {
        "YamlChar": "YAML-CHAR",
        "EnvChar": "ENV-CHAR",
    }


def test_parse_card_id_prefixes_format() -> None:
    assert parse_card_id_prefixes("gawain:GAWAINMOD-, other:OTHER-") == {
        "gawain": "GAWAINMOD-",
        "other": "OTHER-",
    }
    assert parse_card_id_prefixes("") == {}
    assert parse_card_id_prefixes("broken,,no-colon-here-ok:x") == {"no-colon-here-ok": "x"}


class TestPerTaskProjectResolution:
    """按公共任务的 project 解析项目配置（Review 复核 #2）。

    场景：公共服务从平台仓库目录启动、环境变量为空、任务携带
    project=gawain —— 适配器必须读到 Gawain 的项目扩展规则；
    未携带 project 时必须保持空中性，不得串用其他项目规则。
    """

    def test_create_adapter_with_project_reads_project_config(self, monkeypatch) -> None:
        from sts2_autotest.cli.main import _create_adapter

        monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)

        adapter = _create_adapter("agent", project="gawain")

        assert adapter._card_id_prefixes == {"gawain": "GAWAINMOD-"}
        assert adapter._seed_command_template == "gawain_emergency_recruit_seed {seed}"

    def test_create_adapter_without_project_stays_neutral(self, monkeypatch) -> None:
        from sts2_autotest.cli.main import _create_adapter

        monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)

        adapter = _create_adapter("agent")

        assert adapter._card_id_prefixes == {}
        assert adapter._seed_command_template == ""

    def test_create_adapter_with_unknown_project_falls_back_to_neutral(self, monkeypatch) -> None:
        from sts2_autotest.cli.main import _create_adapter

        monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)

        adapter = _create_adapter("agent", project="no-such-project")

        assert adapter._card_id_prefixes == {}
        assert adapter._seed_command_template == ""

    def test_env_var_overrides_project_yaml(self, monkeypatch) -> None:
        from sts2_autotest.cli.main import _create_adapter

        monkeypatch.setenv("STS2_PROJECT__CARD_ID_PREFIXES", "other:OTHER-")
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)

        adapter = _create_adapter("agent", project="gawain")

        assert adapter._card_id_prefixes == {"gawain": "GAWAINMOD-", "other": "OTHER-"}
