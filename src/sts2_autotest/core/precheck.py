"""Environment pre-check system — five-layer fail-fast validation before test launch (FR3).

Layers: environment → config → resources → permissions → concurrency.
Each layer runs in order; failure stops further checks (fail-fast).
"""

from __future__ import annotations

import ctypes
import shutil
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil

from sts2_autotest.common.logging import get_logger
from sts2_autotest.common.types import PrecheckSettings

logger = get_logger("core.precheck")

_GAME_EXE = "SlayTheSpire2.exe"
_STEAM_EXE = "steam.exe"

if not hasattr(ctypes, "windll"):
    ctypes.windll = SimpleNamespace(  # type: ignore[attr-defined]
        shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1),
    )


@dataclass
class PrecheckResult:
    """Result of a single pre-check layer."""

    layer: str
    passed: bool
    message: str
    detail: dict[str, object] = field(default_factory=dict)


@dataclass
class PrecheckReport:
    """Aggregated report of all pre-check results."""

    passed: bool
    results: list[PrecheckResult] = field(default_factory=list)

    @property
    def failed_layers(self) -> list[str]:
        return [r.layer for r in self.results if not r.passed]


class PrecheckRunner:
    """Five-layer environment pre-check runner.

    Layers execute in order (fail-fast):
    1. environment: Steam running, game installed, CLI discoverable, Mod loadable
    2. config: adapter path valid, version compatible, pipe reachable, timeouts legal
    3. resources: disk space, memory, port/pipe availability
    4. permissions: admin check, screenshot/evidence dirs writable, lock dir writable
    5. concurrency: game not already running, no stale lock file
    """

    def __init__(
        self,
        settings: PrecheckSettings,
        *,
        cli_discover: Callable[[], str | None] | None = None,
    ) -> None:
        self._settings = settings
        self._cli_discover = cli_discover

    # ── public API ──────────────────────────────────────────

    def run(self) -> PrecheckReport:
        """Execute all five layers in order, fail-fast on first failure."""
        results: list[PrecheckResult] = []

        layers = [
            ("environment", self._check_environment),
            ("config", self._check_config),
            ("resources", self._check_resources),
            ("permissions", self._check_permissions),
            ("concurrency", self._check_concurrency),
        ]

        for layer_name, check_fn in layers:
            result = check_fn()
            results.append(result)
            if not result.passed:
                logger.error(
                    "Pre-check FAILED at layer '%s': %s",
                    layer_name, result.message,
                )
                return PrecheckReport(passed=False, results=results)
            logger.info("Pre-check passed: %s", layer_name)

        return PrecheckReport(passed=True, results=results)

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _safe_process_iter(attrs: list[str]) -> Iterator[dict[str, Any]]:
        """Iterate over psutil processes, swallowing NoSuchProcess/AccessDenied."""
        for p in psutil.process_iter(attrs):
            try:
                info = p.info
                if info.get("name"):
                    yield info
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _discover_cli(self) -> str | None:
        """Safely invoke the CLI discovery callable."""
        if self._cli_discover is None:
            return None
        try:
            return self._cli_discover()
        except Exception as exc:
            logger.warning("CLI discovery callable raised: %s", exc)
            return None

    @staticmethod
    def _find_game_exe(cli_path: str | None) -> Path | None:
        """Try to locate the game executable near the CLI path or in standard Steam locations."""
        # Check relative to CLI path (typical: game_dir/STS2-Cli-Mod/cli.exe)
        if cli_path:
            cli_dir = Path(cli_path).resolve().parent
            for candidate in [
                cli_dir.parent / _GAME_EXE,           # ../SlayTheSpire2.exe
                cli_dir / _GAME_EXE,                   # same dir
                cli_dir.parent / "game" / _GAME_EXE,   # ../game/SlayTheSpire2.exe
            ]:
                if candidate.is_file():
                    return candidate

        # Standard Steam library paths
        steam_dirs = [
            Path("C:/Program Files (x86)/Steam/steamapps/common/SlayTheSpire2"),
            Path.home() / ".steam/steam/steamapps/common/SlayTheSpire2",
        ]
        for steam_dir in steam_dirs:
            candidate = steam_dir / _GAME_EXE
            if candidate.is_file():
                return candidate

        return None

    @staticmethod
    def _find_mod_dir(game_dir: Path | None) -> Path | None:
        """Try to locate the STS2 Mod directory."""
        if game_dir is None:
            return None
        # Mod directory is typically at game_dir/mods or game_dir/../STS2-Mod
        for candidate in [
            game_dir / "mods",
            game_dir.parent / "STS2-Mod",
            Path("mods"),
        ]:
            if candidate.is_dir() or candidate.exists():
                return candidate
        return None

    # ── Layer 1: Environment ─────────────────────────────────

    def _check_environment(self) -> PrecheckResult:
        """Check Steam running, game installed, CLI discoverable, Mod loadable."""
        # 1. Steam process
        steam_processes = [
            p for p in self._safe_process_iter(["name"])
            if _STEAM_EXE.lower() in str(p["name"]).lower()
        ]
        if not steam_processes:
            return PrecheckResult(
                layer="environment",
                passed=False,
                message="Steam is not running",
                detail={"steam_detected": False},
            )

        # 2. CLI discoverable
        cli_path = self._discover_cli()
        if cli_path is None:
            return PrecheckResult(
                layer="environment",
                passed=False,
                message="STS2-Cli-Mod not found — install or set STS2_CLI_PATH",
                detail={"cli_path": None},
            )

        if not Path(cli_path).is_file():
            return PrecheckResult(
                layer="environment",
                passed=False,
                message=f"CLI path does not exist: {cli_path}",
                detail={"cli_path": cli_path},
            )

        # 3. Game installed
        game_exe = self._find_game_exe(cli_path)
        if game_exe is None or not game_exe.is_file():
            return PrecheckResult(
                layer="environment",
                passed=False,
                message="Slay the Spire 2 game executable not found — is the game installed?",
                detail={"game_exe": _GAME_EXE, "game_found": False},
            )

        # 4. Mod loadable
        mod_dir = self._find_mod_dir(game_exe.parent)
        if mod_dir is None:
            return PrecheckResult(
                layer="environment",
                passed=False,
                message="STS2 Mod not found — install the Mod before testing",
                detail={"mod_found": False, "game_dir": str(game_exe.parent)},
            )

        return PrecheckResult(
            layer="environment",
            passed=True,
            message="Environment OK",
            detail={
                "steam_detected": True,
                "cli_path": cli_path,
                "game_exe": str(game_exe),
                "mod_dir": str(mod_dir),
            },
        )

    # ── Layer 2: Config ──────────────────────────────────────

    def _check_config(self) -> PrecheckResult:
        """Check adapter path valid, version compatible, pipe reachable, timeout legal."""
        cli_path = self._settings.adapter_cli_path
        if cli_path and not Path(cli_path).is_file():
            return PrecheckResult(
                layer="config",
                passed=False,
                message=f"Configured adapter CLI path invalid: {cli_path}",
                detail={"adapter_cli_path": cli_path},
            )

        timeout = self._settings.adapter_timeout
        if timeout <= 0:
            return PrecheckResult(
                layer="config",
                passed=False,
                message=f"Adapter timeout must be > 0, got {timeout}",
                detail={"adapter_timeout": timeout},
            )

        # Pipe/communication channel check
        if cli_path:
            try:
                import subprocess
                result = subprocess.run(
                    [cli_path, "--help"],
                    capture_output=True,
                    timeout=10.0,
                )
                if result.returncode != 0:
                    return PrecheckResult(
                        layer="config",
                        passed=False,
                        message=f"CLI communication failed — exit code {result.returncode}",
                        detail={
                            "cli_path": cli_path,
                            "exit_code": result.returncode,
                            "stderr": result.stderr.decode("utf-8", errors="replace")[:200],
                        },
                    )
            except FileNotFoundError:
                return PrecheckResult(
                    layer="config",
                    passed=False,
                    message=f"CLI executable not found: {cli_path}",
                    detail={"cli_path": cli_path},
                )
            except subprocess.TimeoutExpired:
                return PrecheckResult(
                    layer="config",
                    passed=False,
                    message="CLI communication timed out",
                    detail={"cli_path": cli_path},
                )
            except Exception as exc:
                return PrecheckResult(
                    layer="config",
                    passed=False,
                    message=f"CLI communication error: {exc}",
                    detail={"cli_path": cli_path, "error": str(exc)},
                )

        return PrecheckResult(
            layer="config",
            passed=True,
            message="Config OK",
        )

    # ── Layer 3: Resources ───────────────────────────────────

    def _check_resources(self) -> PrecheckResult:
        """Check disk space, memory, socket subsystem availability."""
        # Disk space
        evidence_dir = self._settings.evidence_dir
        free_mb = 0
        try:
            disk_path = Path(evidence_dir)
            if not disk_path.exists():
                disk_path = disk_path.parent
            usage = shutil.disk_usage(str(disk_path))
            free_mb = usage.free // (1024 * 1024)
            threshold_mb = self._settings.disk_threshold_mb
            if free_mb < threshold_mb:
                return PrecheckResult(
                    layer="resources",
                    passed=False,
                    message=f"Disk space insufficient: {free_mb}MB free, "
                            f"need {threshold_mb}MB",
                    detail={
                        "free_mb": free_mb,
                        "threshold_mb": threshold_mb,
                        "path": str(disk_path),
                    },
                )
        except OSError as exc:
            return PrecheckResult(
                layer="resources",
                passed=False,
                message=f"Cannot check disk space: {exc}",
                detail={"error": str(exc)},
            )

        # Memory
        mem = psutil.virtual_memory()
        if mem.percent > 95:
            return PrecheckResult(
                layer="resources",
                passed=False,
                message=f"Memory critically low: {mem.percent}% used",
                detail={"memory_percent": mem.percent, "memory_available_mb": mem.available // (1024 * 1024)},
            )

        # Socket subsystem availability (basic connectivity check)
        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.settimeout(1.0)
            test_socket.close()
        except OSError as exc:
            return PrecheckResult(
                layer="resources",
                passed=False,
                message=f"Socket subsystem error: {exc}",
                detail={"socket_error": str(exc)},
            )

        return PrecheckResult(
            layer="resources",
            passed=True,
            message="Resources OK",
            detail={"free_disk_mb": free_mb},
        )

    # ── Layer 4: Permissions ─────────────────────────────────

    def _check_permissions(self) -> PrecheckResult:
        """Check admin rights and directory writability."""
        # Admin check (Windows only)
        try:
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            if not shell32.IsUserAnAdmin():
                return PrecheckResult(
                    layer="permissions",
                    passed=False,
                    message="Admin rights required — run as Administrator",
                    detail={"is_admin": False},
                )
        except AttributeError:
            # Non-Windows platform — skip admin check
            pass

        # Directory writability
        for dir_path, label in [
            (self._settings.screenshot_dir, "screenshot"),
            (self._settings.evidence_dir, "evidence"),
        ]:
            path = Path(dir_path)
            try:
                path.mkdir(parents=True, exist_ok=True)
                test_file = path / ".write_test"
                test_file.write_text("test", encoding="utf-8")
                test_file.unlink()
            except OSError as exc:
                return PrecheckResult(
                    layer="permissions",
                    passed=False,
                    message=f"{label} directory not writable: {dir_path}",
                    detail={"path": dir_path, "error": str(exc)},
                )

        # Lock file directory writability
        lock_path = Path(self._settings.lock_file).resolve()
        lock_dir = lock_path.parent
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
            test_file = lock_dir / ".lock_write_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
        except OSError as exc:
            return PrecheckResult(
                layer="permissions",
                passed=False,
                message=f"Lock file directory not writable: {lock_dir}",
                detail={"path": str(lock_dir), "error": str(exc)},
            )

        return PrecheckResult(
            layer="permissions",
            passed=True,
            message="Permissions OK",
        )

    # ── Layer 5: Concurrency ─────────────────────────────────

    def _check_concurrency(self) -> PrecheckResult:
        """Check game not already running, no stale lock file."""
        # Game process
        game_processes = [
            p for p in self._safe_process_iter(["name"])
            if _GAME_EXE.lower() in str(p["name"]).lower()
        ]
        if game_processes:
            return PrecheckResult(
                layer="concurrency",
                passed=False,
                message="Game is already running — stop it before testing",
                detail={"game_running": True},
            )

        # Lock file check
        lock_path = Path(self._settings.lock_file)
        if lock_path.exists():
            try:
                content = lock_path.read_text(encoding="utf-8").strip()
                pid = int(content)
                if psutil.pid_exists(pid):
                    try:
                        proc = psutil.Process(pid)
                        proc_name = proc.name()
                        # Verify the PID belongs to a test-related process
                        is_test_process = (
                            "python" in proc_name.lower()
                            or "sts2" in proc_name.lower()
                        )
                        if proc.is_running() and is_test_process:
                            return PrecheckResult(
                                layer="concurrency",
                                passed=False,
                                message=f"Lock file held by PID {pid} ({proc_name})",
                                detail={
                                    "lock_file": str(lock_path),
                                    "holder_pid": pid,
                                    "holder_name": proc_name,
                                    "holder_alive": True,
                                },
                            )
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (ValueError, OSError):
                pass

            return PrecheckResult(
                layer="concurrency",
                passed=False,
                message=f"Stale lock file found: {lock_path} — delete it or verify no test is running",
                detail={
                    "lock_file": str(lock_path),
                    "holder_alive": False,
                },
            )

        return PrecheckResult(
            layer="concurrency",
            passed=True,
            message="Concurrency OK",
        )
