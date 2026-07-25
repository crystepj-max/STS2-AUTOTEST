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
    """按公共任务的 project 解析项目配置（Review 复核 #2 / 三轮复核 #1-#2）。

    project 同时接受：已登记名称（本地 workspace 配置）与直接项目目录。
    未携带 project 时保持空中性，不得串用其他项目规则。
    """

    def _make_mod_project(self, base: Path) -> Path:
        """构造一个最小 MOD 项目目录（manifest + 项目配置）。"""
        config_dir = base / "automation" / "autotest" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "sts2-autotest.yaml").write_text(
            "project_extension:\n"
            "  card_id_prefixes:\n"
            "    mymod: MYMOD-\n"
            "  seed_command_template: 'mymod_seed {seed}'\n"
            "  character_aliases:\n"
            "    MyChar: MYMOD-MYCHAR\n",
            encoding="utf-8",
        )
        (base / "sts2-mod.yaml").write_text(
            "mod:\n  id: mymod\n"
            "autotest:\n  config: automation/autotest/config/sts2-autotest.yaml\n",
            encoding="utf-8",
        )
        return base

    def test_create_adapter_with_project_directory(self, tmp_path, monkeypatch) -> None:
        """直接传项目目录：无需任何平台登记即可读到项目规则。"""
        from sts2_autotest.cli.main import _create_adapter

        monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)
        mod_dir = self._make_mod_project(tmp_path / "my-mod")

        adapter = _create_adapter("agent", project=str(mod_dir))

        assert adapter._card_id_prefixes == {"mymod": "MYMOD-"}
        assert adapter._seed_command_template == "mymod_seed {seed}"

    def test_create_adapter_with_registered_name(self, tmp_path, monkeypatch) -> None:
        """已登记名称：经本地 workspace 配置的 manifest 指针解析。"""
        from sts2_autotest.cli.main import _create_adapter

        monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)
        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sts2-autotest.yaml").write_text(
            "workspace:\n"
            "  projects:\n"
            "    - name: mymod\n"
            f"      manifest: {mod_dir}/sts2-mod.yaml\n"
            "      spec_dir: " + str(mod_dir) + "\n",
            encoding="utf-8",
        )

        adapter = _create_adapter("agent", project="mymod")

        assert adapter._card_id_prefixes == {"mymod": "MYMOD-"}
        assert adapter._seed_command_template == "mymod_seed {seed}"

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

    def test_env_var_overrides_project_yaml(self, tmp_path, monkeypatch) -> None:
        from sts2_autotest.cli.main import _create_adapter

        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        monkeypatch.setenv("STS2_PROJECT__CARD_ID_PREFIXES", "other:OTHER-")
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)

        adapter = _create_adapter("agent", project=str(mod_dir))

        assert adapter._card_id_prefixes == {"mymod": "MYMOD-", "other": "OTHER-"}

    def test_character_aliases_follow_project(self, tmp_path, monkeypatch) -> None:
        """角色别名按任务 project 读取（公共服务目录启动编译场景）。"""
        from sts2_autotest.cli.main import _load_character_aliases

        monkeypatch.delenv("STS2_PROJECT__CHARACTER_ALIASES", raising=False)
        mod_dir = self._make_mod_project(tmp_path / "my-mod")

        assert _load_character_aliases(str(mod_dir)) == {"MyChar": "MYMOD-MYCHAR"}

    def test_recovery_factory_keeps_project_config(self, tmp_path, monkeypatch) -> None:
        """恢复重建入口继续携带 project：强制重建后项目规则仍保留。"""
        from sts2_autotest.cli import main as cli_main

        monkeypatch.delenv("STS2_PROJECT__CARD_ID_PREFIXES", raising=False)
        monkeypatch.delenv("STS2_PROJECT__SEED_COMMAND_TEMPLATE", raising=False)
        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        captured: dict = {}

        def fake_run_with_adapter(adapter, case_ids, **kwargs):
            captured["factory"] = kwargs.get("adapter_factory")
            return 0

        monkeypatch.setattr(
            cli_main, "_run_orchestrator_with_adapter", fake_run_with_adapter
        )
        monkeypatch.setattr(cli_main, "_create_adapter", cli_main._create_adapter)

        cli_main._dispatch_orchestrator(
            object(), ["all"], 30, use_agent=True, project=str(mod_dir)
        )

        factory = captured["factory"]
        assert factory is not None
        recreated = factory()
        assert recreated._card_id_prefixes == {"mymod": "MYMOD-"}
        assert recreated._seed_command_template == "mymod_seed {seed}"

    def test_compile_cmd_passes_project_aliases(self, tmp_path, monkeypatch) -> None:
        """从公共服务目录编译项目规格时，角色别名随 project 生效。"""
        from argparse import Namespace
        from sts2_autotest.cli import main as cli_main

        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        (spec_dir / "TC-X.md").write_text(
            "# TC-X 选择角色\n\n"
            "## Metadata\n- id: TC-X\n- level: case\n- priority: P0\n\n"
            "## Start State\n- MAIN_MENU\n\n"
            "## End State\n- EVENT\n\n"
            "## When\n1. 选择 MyChar\n2. 开始冒险\n\n"
            "## Then\n- 不 crash\n",
            encoding="utf-8",
        )
        captured: dict = {}

        class FakeGenerator:
            def __init__(self, character_aliases=None):
                captured["aliases"] = character_aliases

            def generate_to_file(self, spec, output_dir):
                return "fake.py"

        monkeypatch.setattr(
            "sts2_autotest.core.code_generator.CodeGenerator", FakeGenerator
        )
        monkeypatch.chdir(tmp_path)  # 公共服务目录：不是项目目录，无项目配置文件
        args = Namespace(
            command="compile",
            spec_dir=str(spec_dir),
            output_dir=str(tmp_path / "out"),
            project=str(mod_dir),
            use_revised=False,
        )

        rc = cli_main.compile_cmd(args)

        assert rc == 0
        assert captured["aliases"] == {"MyChar": "MYMOD-MYCHAR"}

    def test_project_directory_determines_spec_and_output_dirs(self, tmp_path, monkeypatch) -> None:
        """目录型项目同时决定规格来源与默认输出，不回退平台默认目录。"""
        from argparse import Namespace
        from sts2_autotest.cli.main import _resolve_output_dir, _resolve_spec_dir

        monkeypatch.chdir(tmp_path)  # 公共服务目录：无 docs/process/specs
        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        (mod_dir / "automation" / "autotest" / "config" / "sts2-autotest.yaml").write_text(
            "workspace:\n"
            "  projects:\n"
            "    - name: mymod\n"
            "      spec_dir: automation/autotest/specs\n"
            "      output_dir: automation/autotest/generated\n"
            "project_extension:\n"
            "  card_id_prefixes:\n"
            "    mymod: MYMOD-\n",
            encoding="utf-8",
        )
        args = Namespace(spec_dir=None, output_dir=None, project=str(mod_dir))

        (mod_dir / "automation/autotest/specs").mkdir(parents=True)
        (mod_dir / "automation/autotest/generated").mkdir(parents=True)
        spec_dir = _resolve_spec_dir(args)
        output_dir = _resolve_output_dir(args, spec_dir or "")

        assert spec_dir == str((mod_dir / "automation/autotest/specs").resolve())
        assert output_dir == str((mod_dir / "automation/autotest/generated").resolve())
        assert "docs/process/specs" not in (spec_dir or "")
        assert output_dir != "tests/generated"

    def test_explicit_project_without_declarations_fails_structurally(self, tmp_path, monkeypatch) -> None:
        """显式项目缺少规格/输出声明时结构化失败，绝不回退平台目录。"""
        import pytest
        from argparse import Namespace
        from sts2_autotest.cli.main import (
            ProjectConfigError,
            _resolve_output_dir,
            _resolve_spec_dir,
        )

        monkeypatch.chdir(tmp_path)  # 公共服务目录（有 docs/process/specs 也不许回退）
        (tmp_path / "docs/process/specs").mkdir(parents=True)
        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        # 清空项目配置中的 workspace 声明，使其没有任何 spec/output 声明
        (mod_dir / "automation/autotest/config/sts2-autotest.yaml").write_text(
            "project_extension:\n  card_id_prefixes:\n    mymod: MYMOD-\n",
            encoding="utf-8",
        )
        args = Namespace(spec_dir=None, output_dir=None, project=str(mod_dir))

        with pytest.raises(ProjectConfigError, match="spec_dir"):
            _resolve_spec_dir(args)
        with pytest.raises(ProjectConfigError, match="output_dir"):
            _resolve_output_dir(args, "whatever")

    def test_explicit_project_with_nonexistent_spec_dir_fails(self, tmp_path, monkeypatch) -> None:
        """项目声明的规格目录不存在时结构化失败（无效目录检查）。"""
        import pytest
        from argparse import Namespace
        from sts2_autotest.cli.main import ProjectConfigError, _resolve_spec_dir

        monkeypatch.chdir(tmp_path)
        mod_dir = self._make_mod_project(tmp_path / "my-mod")
        (mod_dir / "automation/autotest/config/sts2-autotest.yaml").write_text(
            "workspace:\n"
            "  projects:\n"
            "    - name: mymod\n"
            "      spec_dir: no/such/spec-dir\n"
            "      output_dir: automation/autotest/generated\n",
            encoding="utf-8",
        )
        args = Namespace(spec_dir=None, project=str(mod_dir))

        with pytest.raises(ProjectConfigError, match="does not exist"):
            _resolve_spec_dir(args)
