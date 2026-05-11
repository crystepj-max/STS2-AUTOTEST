"""Evidence packager — creates evidence pack directories with summary.json (FR23, FR64)."""

from __future__ import annotations

__test__ = False

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sts2_autotest.common.evidence import (
    ArtifactsInfo,
    EnvironmentInfo,
    EvidencePack,
    FailureInfo,
    RunInfo,
    SummaryJson,
)
from sts2_autotest.common.logging import get_logger

logger = get_logger("evidence.packager")


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

    def _write_json(self, path: Path, data: dict[str, object]) -> None:
        """Write JSON with atomic write."""
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
