"""Markdown parser for natural language test specs (case + suite).

Parses structured Markdown into TestSpec/SuiteSpec models.
Uses regex-based section parsing -- no external Markdown parser needed
for the well-defined template format.
"""

from __future__ import annotations

import re
from pathlib import Path

from sts2_autotest.common.spec_models import SuiteSpec, TestSpec


class ParsingError(ValueError):
    """Raised when a Markdown spec cannot be parsed."""


def detect_level(markdown: str) -> str:
    """Detect whether the Markdown is a 'case' or 'suite' spec.

    Reads the ``level`` field from the Metadata section.
    """
    metadata = _extract_section(markdown, "Metadata")
    if metadata is None:
        raise ParsingError("No level found: no Metadata section")

    m = re.search(r'-\s*level\s*:\s*(\w+)', metadata)
    if not m:
        raise ParsingError("No level found in Metadata")

    level = m.group(1).lower()
    if level not in ("case", "suite"):
        raise ParsingError(f"Invalid level: '{level}'. Must be 'case' or 'suite'.")
    return level


def _extract_section(markdown: str, section_name: str) -> str | None:
    """Extract a section's content by its ``## `` heading name."""
    pattern = rf'##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s|\Z)'
    m = re.search(pattern, markdown, re.DOTALL)
    return m.group(1).strip() if m else None


def _parse_list_items(text: str) -> list[str]:
    """Parse a list of ``- item`` or ``1. item`` lines into a list of strings."""
    items: list[str] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        # Match ``- text`` or ``1. text``
        m = re.match(r'^-\s+(.*)', line)
        if not m:
            m = re.match(r'^\d+\.\s+(.*)', line)
        if m:
            items.append(m.group(1).strip())
    return items


def _parse_kv_list(text: str) -> dict[str, str]:
    """Parse ``- key: value`` lines into a dict."""
    result: dict[str, str] = {}
    for line in text.strip().split("\n"):
        m = re.match(r'-\s*(\w+)\s*:\s*(.+)', line.strip())
        if m:
            result[m.group(1).strip()] = m.group(2).strip()
    return result


def _parse_title(markdown: str) -> str:
    """Extract title from ``# TC-ID Title`` heading."""
    m = re.match(r'^#[ \t]+\S+[ \t]+(.*)', markdown)
    return m.group(1).strip() if m else ""


def _parse_id_from_heading(markdown: str) -> str:
    """Extract ID from ``# TC-ID`` heading."""
    m = re.match(r'^#[ \t]+(\S+)', markdown)
    return m.group(1).strip() if m else ""


class MarkdownParser:
    """Parses structured Markdown test specs into TestSpec/SuiteSpec models."""

    def parse_case(self, markdown: str, source_path: str = "") -> TestSpec:
        """Parse a Markdown case spec into a ``TestSpec``."""
        metadata_text = _extract_section(markdown, "Metadata")
        if metadata_text is None:
            raise ParsingError("No metadata section found")

        metadata = _parse_kv_list(metadata_text)
        spec_id = metadata.get("id") or _parse_id_from_heading(markdown)
        if not spec_id:
            raise ParsingError("No id found in Metadata or heading")

        title = metadata.get("title", _parse_title(markdown))
        tags_str = metadata.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        priority = metadata.get("priority", "P3")

        start_state = self._parse_section_text(markdown, "Start State")
        end_state = self._parse_section_text(markdown, "End State")
        givens = self._parse_section_list(markdown, "Given")
        steps = self._parse_section_list(markdown, "When")
        assertions = self._parse_section_list(markdown, "Then")

        return TestSpec(
            id=spec_id,
            title=title,
            tags=tags,
            priority=priority,
            start_state=start_state,
            end_state=end_state,
            givens=givens,
            steps=steps,
            assertions=assertions,
            source_path=source_path,
        )

    def parse_suite(self, markdown: str, source_path: str = "") -> SuiteSpec:
        """Parse a Markdown suite spec into a ``SuiteSpec``."""
        metadata_text = _extract_section(markdown, "Metadata")
        if metadata_text is None:
            raise ParsingError("No metadata section found")

        metadata = _parse_kv_list(metadata_text)
        suite_id = metadata.get("id") or _parse_id_from_heading(markdown)
        if not suite_id:
            raise ParsingError("No id found in Metadata or heading")

        title = metadata.get("title", _parse_title(markdown))
        tags_str = metadata.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        priority = metadata.get("priority", "P3")

        goal_text = self._parse_section_text(markdown, "Goal")
        mode_text = self._parse_section_text(markdown, "Mode")
        execution_mode = "sequential_shared_session"
        if mode_text:
            m = re.search(r'execution\s*:\s*(\S+)', mode_text)
            if m:
                execution_mode = m.group(1)

        includes = self._parse_section_list(markdown, "Includes")
        suite_assertions = self._parse_section_list(markdown, "Then")

        return SuiteSpec(
            id=suite_id,
            title=title,
            tags=tags,
            priority=priority,
            goal=goal_text,
            execution_mode=execution_mode,
            includes=includes,
            suite_assertions=suite_assertions,
            source_path=source_path,
        )

    def discover_specs(
        self, spec_dir: str
    ) -> tuple[list[TestSpec], list[SuiteSpec]]:
        """Scan a directory recursively for ``.md`` spec files, parse them, and split into cases/suites."""
        cases: list[TestSpec] = []
        suites: list[SuiteSpec] = []
        path = Path(spec_dir)
        if not path.is_dir():
            return cases, suites

        for f in sorted(path.rglob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
                level = detect_level(text)
                if level == "case":
                    cases.append(self.parse_case(text, source_path=str(f)))
                elif level == "suite":
                    suites.append(self.parse_suite(text, source_path=str(f)))
            except (ParsingError, UnicodeDecodeError, PermissionError):
                continue  # skip files that don't match the spec format or can't be read
        return cases, suites

    def _parse_section_text(self, markdown: str, name: str) -> str:
        """Extract a section's content as raw text."""
        text = _extract_section(markdown, name)
        return text if text else ""

    def _parse_section_list(self, markdown: str, name: str) -> list[str]:
        """Extract a section's content as a list of items."""
        text = _extract_section(markdown, name)
        return _parse_list_items(text) if text else []
