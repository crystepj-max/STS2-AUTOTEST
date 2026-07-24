"""MOD project workspace discovery.

Resolves MOD project paths from sts2-autotest.yaml workspace config,
from --spec-dir CLI parameter, or from sts2-mod.yaml (协议层 B20).

Extended (B20): added from_mod_yaml(), resolve_suite_paths(),
mod_manifest_path() for full MOD project discovery.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from sts2_autotest.common.spec_models import ProjectConfig, SuiteSpec, TestSpec
from sts2_autotest.core.markdown_parser import MarkdownParser


class WorkspaceError(Exception):
    """Raised when workspace configuration cannot be loaded or parsed."""
    pass


class Workspace:
    """Manages MOD project discovery and spec resolution.

    Supports three modes:
    1. Direct --spec-dir: single project, no config file needed
    2. Workspace config: multiple projects declared in sts2-autotest.yaml
    3. MOD project config: rich project declaration from sts2-mod.yaml
    """

    def __init__(self, projects: list[ProjectConfig], base_dir: str = "") -> None:
        self._projects = {p.name: p for p in projects}
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._parser = MarkdownParser()

    @classmethod
    def from_mod_yaml(cls, mod_yaml_path: str, base_dir: str = "") -> Workspace:
        """Load a single-project Workspace from a sts2-mod.yaml file."""
        path = Path(mod_yaml_path)
        if not path.is_file():
            raise WorkspaceError(f"MOD config not found: {mod_yaml_path}")

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise WorkspaceError(f"Failed to parse MOD YAML: {e}")

        if not isinstance(data, dict) or "mod" not in data:
            return cls([], base_dir=base_dir)

        mod_data = data["mod"]
        autotest = data.get("autotest", {})
        design = data.get("design", {})

        spec_dirs = autotest.get("spec_dirs", [])
        spec_dir = spec_dirs[0] if spec_dirs else ""
        suite_dir_list = autotest.get("suite_dirs", [])
        output_dir = autotest.get("evidence_dir", "tests/output")

        # Resolve project root directory for relative path resolution
        mod_root = str(Path(mod_yaml_path).parent.resolve()) if mod_yaml_path else ""

        proj = ProjectConfig(
            name=mod_data.get("name", mod_data["id"]),
            mod_id=mod_data["id"],
            manifest=mod_data.get("manifest", ""),
            spec_dir=os.path.join(mod_root, spec_dir) if spec_dir and mod_root else spec_dir,
            output_dir=os.path.join(mod_root, output_dir) if output_dir and mod_root else output_dir,
            source_dirs=[os.path.join(mod_root, d) for d in autotest.get("source_dirs", []) if mod_root],
            design_docs=[os.path.join(mod_root, d) for d in design.get("docs", []) if mod_root],
            suite_dirs=[os.path.join(mod_root, d) for d in suite_dir_list if mod_root],
            default_suite=autotest.get("default_suite", ""),
            autotest_config=autotest.get("config", ""),
        )
        return cls([proj], base_dir=base_dir)

    @classmethod
    def from_yaml(cls, yaml_path: str, base_dir: str = "") -> Workspace:
        """Load workspace from a sts2-autotest.yaml file."""
        path = Path(yaml_path)
        if not path.is_file():
            raise WorkspaceError(f"Config file not found: {yaml_path}")

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise WorkspaceError(f"Failed to parse YAML: {e}")

        if not isinstance(data, dict):
            return cls([], base_dir=base_dir)

        workspace_data = data.get("workspace", {})
        if not isinstance(workspace_data, dict):
            return cls([], base_dir=base_dir)

        projects_data = workspace_data.get("projects", [])
        if not isinstance(projects_data, list):
            return cls([], base_dir=base_dir)

        projects = []
        for p in projects_data:
            if not isinstance(p, dict) or "name" not in p:
                continue
            projects.append(ProjectConfig(
                name=p["name"],
                spec_dir=p.get("spec_dir", ""),
                output_dir=p.get("output_dir", ""),
                manifest=p.get("manifest", ""),
            ))

        return cls(projects, base_dir=base_dir)

    @classmethod
    def from_spec_dir(cls, spec_dir: str) -> Workspace:
        """Create a single-project workspace from --spec-dir."""
        return cls([
            ProjectConfig(name="_direct", spec_dir=spec_dir, output_dir=spec_dir),
        ])

    def resolve_suite_paths(self, project_name: str) -> list[str]:
        """Resolve suite directory paths for a project."""
        project = self.resolve_project(project_name)
        if project is None:
            return []
        result = []
        for d in getattr(project, "suite_dirs", []):
            resolved = self._resolve_path(d)
            if resolved:
                result.append(resolved)
        return result

    def mod_manifest_path(self, project_name: str) -> str | None:
        """Resolve full path to the project's MOD manifest file."""
        project = self.resolve_project(project_name)
        if project is None or not project.manifest:
            return None
        return self._resolve_path(project.manifest)

    @property
    def project_names(self) -> list[str]:
        return list(self._projects.keys())

    @property
    def projects(self) -> list[ProjectConfig]:
        return list(self._projects.values())

    def resolve_project(self, name: str) -> Optional[ProjectConfig]:
        """Find a project by name. Returns None if not found."""
        return self._projects.get(name)

    def discover_project_specs(self, project_name: str) -> tuple[list[TestSpec], list[SuiteSpec]]:
        """Discover and parse all spec files in a project's spec_dir.

        Returns (cases, suites) as parsed TestSpec/SuiteSpec lists.
        """
        project = self.resolve_project(project_name)
        if project is None:
            return [], []

        spec_dir = self._resolve_path(project.spec_dir)
        if not spec_dir or not Path(spec_dir).is_dir():
            return [], []

        result_cases, result_suites = self._parser.discover_specs(spec_dir)
        return result_cases, result_suites

    def _resolve_path(self, path: str) -> str:
        """Resolve a potentially relative path against base_dir."""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str((self._base_dir / p).resolve())
