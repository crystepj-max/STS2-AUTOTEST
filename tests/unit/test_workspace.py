"""Tests for workspace.py — MOD project discovery."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path
from sts2_autotest.common.spec_models import ProjectConfig, WorkspaceConfig
from sts2_autotest.core.workspace import Workspace, WorkspaceError


class TestWorkspace:
    def test_from_yaml(self, tmp_path: Path) -> None:
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "mod-a", "spec_dir": "../mod-a/tests/cases/", "output_dir": "../mod-a/tests/"},
                    {"name": "mod-b", "spec_dir": "../mod-b/tests/cases/"},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config))
        assert len(ws.projects) == 2
        assert ws.projects[0].name == "mod-a"
        assert ws.projects[0].spec_dir == "../mod-a/tests/cases/"
        assert ws.projects[0].output_dir == "../mod-a/tests/"
        # mod-b should default output_dir to spec_dir
        assert ws.projects[1].output_dir == "../mod-b/tests/cases/"

    def test_from_yaml_file_not_found(self) -> None:
        with pytest.raises(WorkspaceError, match="not found"):
            Workspace.from_yaml("/nonexistent/path.yaml")

    def test_from_yaml_invalid_yaml(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("{{{invalid yaml")
        with pytest.raises(WorkspaceError, match="Failed to parse"):
            Workspace.from_yaml(str(config))

    def test_from_yaml_no_workspace_section(self, tmp_path: Path) -> None:
        config = tmp_path / "empty.yaml"
        config.write_text(yaml.dump({"other": "data"}))
        ws = Workspace.from_yaml(str(config))
        assert len(ws.projects) == 0

    def test_resolve_project_found(self, tmp_path: Path) -> None:
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "my-mod", "spec_dir": "../my-mod/tests/cases/"},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config))
        proj = ws.resolve_project("my-mod")
        assert proj is not None
        assert proj.name == "my-mod"

    def test_resolve_project_not_found(self, tmp_path: Path) -> None:
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({"workspace": {"projects": []}}))
        ws = Workspace.from_yaml(str(config))
        assert ws.resolve_project("nonexistent") is None

    def test_discover_projects_with_specs(self, tmp_path: Path) -> None:
        """Integration: discover projects and verify spec_dir exists."""
        mod_dir = tmp_path / "my-mod" / "tests" / "cases"
        mod_dir.mkdir(parents=True)
        (mod_dir / "TC-001.md").write_text("# TC-001 Test\n\n## Metadata\n- id: TC-001\n- level: case")
        (mod_dir / "SUITE-001.md").write_text("# SUITE-001 Suite\n\n## Metadata\n- id: SUITE-001\n- level: suite")

        config = tmp_path / "sts2-autotest.yaml"
        # Use a relative path that resolves correctly from base_dir
        rel_spec_dir = str(Path("my-mod") / "tests" / "cases")
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "my-mod", "spec_dir": rel_spec_dir},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config), base_dir=str(tmp_path))
        cases, suites = ws.discover_project_specs("my-mod")
        assert len(cases) == 1
        assert cases[0].id == "TC-001"
        assert len(suites) == 1
        assert suites[0].id == "SUITE-001"

    def test_from_spec_dir(self) -> None:
        ws = Workspace.from_spec_dir("/some/spec/dir")
        assert len(ws.projects) == 1
        assert ws.projects[0].name == "_direct"
        assert ws.projects[0].spec_dir == "/some/spec/dir"
        assert ws.projects[0].output_dir == "/some/spec/dir"
        assert ws.project_names == ["_direct"]

    def test_discover_project_specs_nonexistent_project(self, tmp_path: Path) -> None:
        ws = Workspace.from_spec_dir(str(tmp_path))
        cases, suites = ws.discover_project_specs("nonexistent-project")
        assert cases == []
        assert suites == []

    def test_discover_project_specs_nonexistent_dir(self, tmp_path: Path) -> None:
        config = tmp_path / "sts2-autotest.yaml"
        config.write_text(yaml.dump({
            "workspace": {
                "projects": [
                    {"name": "ghost-mod", "spec_dir": "/nonexistent/path"},
                ]
            }
        }))
        ws = Workspace.from_yaml(str(config))
        cases, suites = ws.discover_project_specs("ghost-mod")
        assert cases == []
        assert suites == []
