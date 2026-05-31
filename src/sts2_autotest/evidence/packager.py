"""Evidence packager — creates evidence pack directories with summary.json (FR23, FR64)."""

from __future__ import annotations

__test__ = False

import json
import os
import shutil
import xml.etree.ElementTree as ET
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sts2_autotest.common.evidence import (
    SCHEMA_VERSION,
    ArtifactsInfo,
    EnvironmentInfo,
    EvidencePack,
    FailureInfo,
    RunInfo,
    SummaryJson,
)
from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import EvidencePackagerSettings
from sts2_autotest.core.disk_guard import check_disk_space

logger = get_logger("evidence.packager")


@dataclass
class ArtifactExportJob:
    """Background artifact export job."""

    pack_id: str
    original_pack_dir: Path
    _future: Future[Path | None]
    status: str = "PENDING"
    error: str | None = None
    _error_ref: list[str | None] | None = None

    def wait(self, timeout: float | None = None) -> Path | None:
        """Wait for the export to finish and return the ZIP path when available."""
        try:
            result = self._future.result(timeout=timeout)
        except TimeoutError:
            self.status = "PENDING"
            return None
        except Exception as exc:
            self.status = "FAILED"
            self.error = str(exc) or exc.__class__.__name__
            return None

        if result is None:
            self.status = "FAILED"
            if self._error_ref is not None:
                self.error = self._error_ref[0]
            if self.error is None:
                self.error = "Artifact export failed"
            return None

        self.status = "DONE"
        self.error = None
        return result


class EvidencePackager:
    """Creates evidence pack directories with summary.json.

    Pack structure:
        {evidence_dir}/{pack_id}/
            summary.json
            screenshots/
            logs/
            reports/
    """

    def __init__(
        self,
        evidence_dir: Path,
        *,
        retention: int = 20,
        framework: str = "sts2-autotest",
        adapter: str = "unknown",
        game: str = "Slay the Spire 2",
    ) -> None:
        self._evidence_dir = evidence_dir
        self._retention = retention
        self._framework = framework
        self._adapter = adapter
        self._game = game
        self._artifact_executor: ThreadPoolExecutor | None = None
        self._last_artifact_error: str | None = None

    # ── public API ──────────────────────────────────────────

    def create_pack(
        self,
        pack_id: str | None = None,
        *,
        run_result: str = "unknown",
        duration_ms: int = 0,
        failure: FailureInfo | None = None,
    ) -> Path:
        """Create an evidence pack directory with summary.json.

        Returns the pack directory path.
        """
        if pack_id is None:
            now = datetime.now(timezone.utc)
            pack_id = now.strftime("run_%Y%m%dT%H%M%S")

        pack_dir = self._evidence_dir / pack_id
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "screenshots").mkdir(exist_ok=True)
        (pack_dir / "logs").mkdir(exist_ok=True)
        (pack_dir / "reports").mkdir(exist_ok=True)

        import platform

        summary = SummaryJson(
            pack_id=pack_id,
            test_run=RunInfo(
                run_id=pack_id,
                result=run_result,
                duration_ms=duration_ms,
            ),
            environment=EnvironmentInfo(
                framework=self._framework,
                adapter=self._adapter,
                game=self._game,
                os=platform.platform(),
                python=platform.python_version(),
            ),
            failure=failure,
        )

        summary_path = pack_dir / "summary.json"
        self._write_json(summary_path, summary.model_dump(mode="json"))

        # AC6: automatically generate summary.md on pack creation
        self._generate_report_for(pack_id, summary)

        # B10: generate repair suggestions from failure evidence
        try:
            from sts2_autotest.core.repair_advisor import RepairAdvisor

            advisor = RepairAdvisor()
            report = advisor.analyze(summary)
            if report is not None:
                # Update summary.json with embedded repair report
                updated = summary.model_copy(update={"repair_report": report})
                self._write_json(summary_path, updated.model_dump(mode="json"))

                # Write standalone repair_suggestions.json for CI / AI Agent consumption
                repair_path = pack_dir / "reports" / "repair_suggestions.json"
                self._write_json(repair_path, report.model_dump(mode="json"))
        except Exception:
            logger.warning(
                "Failed to generate repair suggestions for %s", pack_id, exc_info=True,
            )

        self._enforce_retention()

        logger.info("Evidence pack created: %s", pack_dir)
        return pack_dir

    def copy_artifacts(
        self, pack_id: str, *, screenshots: list[Path] | None = None,
        logs: list[Path] | None = None,
    ) -> None:
        """Copy artifact files into an existing evidence pack."""
        pack_dir = self._evidence_dir / pack_id
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"Evidence pack not found: {pack_dir}")

        if screenshots:
            dest = pack_dir / "screenshots"
            for src in screenshots:
                if src.is_file():
                    shutil.copy2(str(src), str(dest / src.name))

        if logs:
            dest = pack_dir / "logs"
            for src in logs:
                if src.is_file():
                    shutil.copy2(str(src), str(dest / src.name))

        # Update summary.json artifacts lists
        summary = self.read_summary(pack_id)
        if summary is not None:
            updated = summary.model_copy(update={
                "artifacts": ArtifactsInfo(
                    screenshots=[p.name for p in (screenshots or []) if p.is_file()],
                    logs=[p.name for p in (logs or []) if p.is_file()],
                ),
            })
            self._write_json(pack_dir / "summary.json", updated.model_dump(mode="json"))
            # Refresh summary.md after artifacts update
            self._generate_report_for(pack_id, updated)

    def read_summary(self, pack_id: str) -> SummaryJson | None:
        """Read and parse summary.json from an evidence pack."""
        summary_path = self._evidence_dir / pack_id / "summary.json"
        if not summary_path.is_file():
            return None
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            return SummaryJson.model_validate(data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Cannot read summary.json for %s: %s", pack_id, exc)
            return None

    def load_pack(self, pack_id: str) -> SummaryJson:
        """Load and validate an evidence pack with schema version negotiation (FR64/AC3).

        Reads summary.json, checks schema_version major version against
        the framework's SCHEMA_VERSION. Rejects packs with a higher major
        version to prevent silent data loss from unrecognized fields.

        Raises:
            FileNotFoundError: Pack or summary.json does not exist.
            ValueError: Pack has a higher major schema version.
        """
        summary_path = self._evidence_dir / pack_id / "summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(f"Evidence pack not found: {pack_id}")

        data = json.loads(summary_path.read_text(encoding="utf-8"))

        pack_version = data.get("schema_version", "0.0.0")
        try:
            pack_major = int(str(pack_version).split(".")[0])
        except (ValueError, IndexError):
            pack_major = 0

        framework_major = int(SCHEMA_VERSION.split(".")[0])

        if pack_major > framework_major:
            raise ValueError(
                f"Evidence pack '{pack_id}' has schema_version={pack_version} "
                f"which is newer than framework's {SCHEMA_VERSION}. "
                f"Please upgrade the framework."
            )

        return SummaryJson.model_validate(data)

    def generate_report(self, pack_id: str) -> Path:
        """Generate human-readable summary.md from summary.json (FR24/AC6).

        Reads summary.json via load_pack() (schema negotiation), then
        generates a markdown report. Also called automatically by
        create_pack() and copy_artifacts() so callers do not need
        to invoke it separately.

        Uses atomic write to ensure complete-or-nothing.
        """
        summary = self.load_pack(pack_id)
        return self._generate_report_for(pack_id, summary)

    def _generate_report_for(self, pack_id: str, summary: SummaryJson) -> Path:
        """Internal: generate summary.md from an already-loaded SummaryJson."""
        pack_dir = self._evidence_dir / pack_id

        lines: list[str] = []
        lines.append(f"# Evidence Report: {summary.pack_id}")
        lines.append("")

        # Test run
        run = summary.test_run
        result_marker = {
            "passed": "PASS",
            "failed": "FAIL",
        }.get(run.result, run.result.upper())
        lines.append(f"## Test Run")
        lines.append("")
        lines.append(f"- **Result:** {result_marker}")
        lines.append(f"- **Duration:** {run.duration_ms} ms")
        lines.append(f"- **Run ID:** {run.run_id}")
        lines.append("")

        # Environment
        env = summary.environment
        lines.append("## Environment")
        lines.append("")
        lines.append(f"- **Framework:** {env.framework}")
        lines.append(f"- **Adapter:** {env.adapter}")
        lines.append(f"- **Game:** {env.game}")
        lines.append(f"- **OS:** {env.os}")
        lines.append(f"- **Python:** {env.python}")
        lines.append("")

        # Artifacts
        arts = summary.artifacts
        if arts.screenshots or arts.logs:
            lines.append("## Artifacts")
            lines.append("")
            if arts.screenshots:
                lines.append("### Screenshots")
                for s in arts.screenshots:
                    lines.append(f"- `{s}`")
                lines.append("")
            if arts.logs:
                lines.append("### Logs")
                for lg in arts.logs:
                    lines.append(f"- `{lg}`")
                lines.append("")

        # Failure details with expected/actual comparison (AC6)
        if summary.failure is not None:
            fail = summary.failure
            lines.append("## Failure Details")
            lines.append("")
            lines.append(f"- **Type:** `{fail.type}`")
            lines.append(f"- **Message:** {fail.message}")
            if fail.expected is not None or fail.actual is not None:
                lines.append("")
                lines.append("| | Value |")
                lines.append("|---|---|")
                lines.append(f"| **Expected** | `{fail.expected or ''}` |")
                lines.append(f"| **Actual** | `{fail.actual or ''}` |")
            if fail.stack_trace:
                lines.append("")
                lines.append("```")
                lines.append(fail.stack_trace)
                lines.append("```")
            lines.append("")

        report_path = pack_dir / "summary.md"
        if not check_disk_space(str(self._evidence_dir)):
            logger.warning(
                "Insufficient disk space — skipping summary.md write (AC3)",
            )
            return report_path

        tmp = report_path.with_suffix(".md.tmp")
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(report_path))
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        logger.info("Generated report: %s", report_path)
        return report_path

    @classmethod
    def from_config(cls, settings: EvidencePackagerSettings) -> EvidencePackager:
        """Construct EvidencePackager from an EvidencePackagerSettings protocol instance."""
        return cls(
            evidence_dir=Path(settings.evidence_dir),
            retention=settings.evidence_retention,
        )

    def list_packs(self) -> list[str]:
        """List all pack IDs in the evidence directory."""
        if not self._evidence_dir.is_dir():
            return []
        return sorted(
            p.name for p in self._evidence_dir.iterdir()
            if p.is_dir() and (p / "summary.json").is_file()
        )

    # ── internal ────────────────────────────────────────────

    def _enforce_retention(self) -> None:
        """Remove oldest packs exceeding retention limit."""
        packs = self.list_packs()
        if len(packs) <= self._retention:
            return

        to_remove = packs[: len(packs) - self._retention]
        for pack_id in to_remove:
            pack_dir = self._evidence_dir / pack_id
            try:
                shutil.rmtree(str(pack_dir))
                logger.info("Removed old evidence pack: %s", pack_id)
            except OSError as exc:
                logger.warning("Cannot remove pack %s: %s", pack_id, exc)

    # ── artifact export (Story 4.7, FR54) ────────────────────

    def export_artifact(self, pack_id: str, result: str = "unknown") -> Path | None:
        """Export an evidence pack as a ZIP artifact.

        Creates a ZIP file containing summary.json, summary.md, screenshots/,
        logs/, and reports/ from the pack directory.

        Returns the ZIP path on success, None on failure.
        """
        pack_dir = self._evidence_dir / pack_id
        self._last_artifact_error = None
        if not pack_dir.is_dir():
            logger.warning("Cannot export artifact: pack %s not found", pack_id)
            self._last_artifact_error = f"Evidence pack not found: {pack_id}"
            return None

        output_dir = self._evidence_dir / "artifacts"
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        base_name = str(output_dir / f"{pack_id}_{result}_{timestamp}")

        # Generate JUnit XML inside the pack before archiving
        junit_path = pack_dir / "reports" / "junit.xml"
        summary = self.read_summary(pack_id)
        if summary is not None:
            junit_xml = _generate_junit_xml(summary)
            try:
                junit_path.write_text(junit_xml, encoding="utf-8")
            except OSError:
                logger.warning("Failed to write JUnit XML for %s", pack_id)

        try:
            zip_path = shutil.make_archive(
                base_name, "zip", root_dir=str(pack_dir),
                base_dir=".",
            )
            logger.info("Artifact exported: %s", zip_path)
            result_path = Path(zip_path)

            # Update summary.json with artifact_path
            if summary is not None:
                updated = summary.model_copy(update={
                    "artifact_path": str(result_path),
                })
                self._write_json(pack_dir / "summary.json", updated.model_dump(mode="json"))

            return result_path
        except OSError as exc:
            logger.warning("Failed to create artifact ZIP for %s: %s", pack_id, exc)
            self._last_artifact_error = str(exc)
            return None

    def export_artifact_async(
        self, pack_id: str, result: str = "unknown",
    ) -> ArtifactExportJob:
        """Export an evidence pack as a ZIP artifact in the background."""
        if self._artifact_executor is None:
            self._artifact_executor = ThreadPoolExecutor(max_workers=1)

        pack_dir = self._evidence_dir / pack_id
        job_error: list[str | None] = [None]

        def export() -> Path | None:
            result_path = self.export_artifact(pack_id, result=result)
            if result_path is None:
                job_error[0] = self._last_artifact_error
            return result_path

        future = self._artifact_executor.submit(export)
        job = ArtifactExportJob(
            pack_id=pack_id,
            original_pack_dir=pack_dir,
            _future=future,
            _error_ref=job_error,
        )
        return job

    def write_scene_coverage_report(
        self,
        pack_id: str,
        coverage: dict[str, dict[str, object]],
    ) -> dict[str, Path]:
        """Write scene coverage reports as JSON and Markdown."""
        pack_dir = self._evidence_dir / pack_id
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"Evidence pack not found: {pack_dir}")

        report_dir = pack_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        json_path = report_dir / "scene-coverage.json"
        markdown_path = report_dir / "scene-coverage.md"
        json_data: dict[str, object] = {scene: entry for scene, entry in coverage.items()}
        self._write_json(json_path, json_data)

        lines = [
            "# Scene Coverage",
            "",
            "| Scene | Visits | Cases |",
            "|---|---:|---|",
        ]
        for scene in sorted(coverage):
            entry = coverage[scene]
            visits = entry.get("visits", 0)
            raw_cases = entry.get("cases", [])
            cases = ", ".join(str(case) for case in raw_cases) if isinstance(raw_cases, list) else ""
            lines.append(f"| {scene} | {visits} | {cases} |")

        tmp = markdown_path.with_suffix(".md.tmp")
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(markdown_path))
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return {"json": json_path, "markdown": markdown_path}

    # ── internal ────────────────────────────────────────────

    def _write_json(self, path: Path, data: dict[str, object]) -> None:
        """Write JSON with atomic write and pre-write disk space check.

        Skips the write (with WARNING) when disk space is below threshold,
        preserving any previously written file at *path*.
        """
        if not check_disk_space(str(self._evidence_dir)):
            logger.warning(
                "Insufficient disk space — skipping JSON write to %s (AC3)",
                path,
            )
            return

        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(str(tmp), str(path))
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _generate_junit_xml(summary: SummaryJson) -> str:
    """Generate JUnit XML from a SummaryJson for CI artifact consumption.

    Format: testsuites → testsuite(name/tests/failures/errors) → testcase
    """
    run = summary.test_run
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite", {
        "name": summary.pack_id,
        "tests": "1",
        "failures": "1" if run.result in ("failed", "crashed") else "0",
        "errors": "1" if run.result == "crashed" else "0",
        "time": str(run.duration_ms / 1000.0),
    })

    # Add a single synthetic test case representing the run
    tc = ET.SubElement(suite, "testcase", {
        "name": f"run_{summary.pack_id}",
        "classname": "sts2_autotest.session",
        "time": str(run.duration_ms / 1000.0),
    })

    if run.result in ("failed", "crashed") and summary.failure is not None:
        failure = ET.SubElement(tc, "failure", {
            "message": summary.failure.message,
            "type": summary.failure.type,
        })
        if summary.failure.stack_trace:
            failure.text = summary.failure.stack_trace

    if summary.artifacts.screenshots:
        for ss in summary.artifacts.screenshots:
            ET.SubElement(tc, "screenshot", {"name": ss})

    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(suites, encoding="unicode")


