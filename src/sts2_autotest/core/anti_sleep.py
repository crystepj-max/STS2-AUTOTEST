"""macOS anti-sleep guard for the duration of a real game task.

Problem: during an unattended real-game run the display / system can sleep,
which suspends rendering and the game control API. On macOS the standard way
to prevent this is ``caffeinate -dimsu``. This guard wraps that as a scoped
subprocess that MUST be started at task begin and stopped at task end / fail /
cancel — never left running as an orphan and never a permanent system setting.

Design constraints (P1 fix five):
- macOS only. On Windows / Linux ``start`` / ``stop`` are no-ops.
- Covers the full task duration; caller is responsible for calling ``stop``
  in a ``finally`` so end / failure / cancel all release it.
- No orphan process: ``stop`` terminates the child and confirms it exited,
  escalating to kill; a best-effort ``psutil`` check verifies the pid is gone.
- Idempotent: double ``start`` / ``stop`` are safe.
- Usable as a context manager.
"""

from __future__ import annotations

import subprocess
import sys
from types import TracebackType
from typing import Any

from sts2_autotest.common.logging import get_logger

logger = get_logger("core.anti_sleep")

_IS_MACOS = sys.platform == "darwin"

# caffeinate flags: -d prevent display sleep, -i prevent idle sleep,
# -m prevent disk sleep, -s prevent system sleep (on AC), -u declare user active.
_CAFFEINATE_ARGS = ["caffeinate", "-dimsu"]


class AntiSleepGuard:
    """Prevent system / display sleep for the lifetime of a real game task."""

    def __init__(self, *, enabled: bool = True) -> None:
        # Only actually active on macOS and when enabled by config.
        self.enabled = bool(enabled) and _IS_MACOS
        self._proc: subprocess.Popen[Any] | None = None
        self.start_error: str | None = None

    @property
    def active(self) -> bool:
        """True when a caffeinate child is currently running."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def start(self) -> bool:
        """Start the anti-sleep guard. Returns True if now active.

        No-op (returns False) on non-macOS or when disabled. A launch failure
        is recorded in ``start_error`` and does not raise — a task must still
        run even if we cannot prevent sleep.
        """
        if not self.enabled:
            return False
        if self.active:
            return True
        try:
            self._proc = subprocess.Popen(
                _CAFFEINATE_ARGS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.start_error = None
            logger.info("anti-sleep guard started pid=%s", self._proc.pid)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            self._proc = None
            self.start_error = f"{type(exc).__name__}: {exc}"
            logger.warning("anti-sleep guard failed to start: %s", self.start_error)
            return False

    def stop(self) -> None:
        """Stop the guard and confirm no orphan caffeinate remains."""
        proc = self._proc
        if proc is None:
            return
        pid = proc.pid
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("anti-sleep stop error: %s", exc)
        finally:
            self._proc = None
        self._confirm_gone(pid)

    @staticmethod
    def _confirm_gone(pid: int) -> None:
        """Best-effort verification that the caffeinate pid is gone."""
        try:
            import psutil

            if psutil.pid_exists(pid):
                p = psutil.Process(pid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    logger.warning("anti-sleep pid %s still alive; killing", pid)
                    p.kill()
        except Exception:
            pass

    # ── context manager ─────────────────────────────────────
    def __enter__(self) -> AntiSleepGuard:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
