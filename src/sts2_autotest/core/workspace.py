"""MOD project workspace discovery.

Resolves MOD project paths from sts2-autotest.yaml workspace config
or from --spec-dir CLI parameter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from sts2_autotest.common.spec_models import ProjectConfig, SuiteSpec, TestSpec
from sts2_autotest.core.markdown_parser import MarkdownParser, ParsingError


class WorkspaceError(Exception):
    """Raised when workspace configuration cannot be loaded or parsed."""
    pass


class Workspace:
    """Manages MOD project discovery and spec resolution.

    Supports two modes:
    1. Direct --spec-dir: single project, no config file needed
    2. Workspace config: multiple projects declared in sts2-autotest.yaml
    """

    def __init__(self, projects: list[ProjectConfig], base_dir: str = "") -> None:
        self._projects = {p.name: p for p in projects}
        self._base_dir = Path(base_dir) if base_dir else Path.cwd()
        self._parser = MarkdownParser()

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
            ))

        return cls(projects, base_dir=base_dir)

    @classmethod
    def from_spec_dir(cls, spec_dir: str) -> Workspace:
        """Create a single-project workspace from --spec-dir."""
        return cls([
            ProjectConfig(name="_direct", spec_dir=spec_dir, output_dir=spec_dir),
        ])

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
