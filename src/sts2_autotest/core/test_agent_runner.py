"""Cross-platform Test Agent Runner — replaces run-test-agent.ps1.

Orchestrates the full test-agent workflow:
  validate → build → localization-check → deploy → launch → smoke → report

Exit codes (matching ROLE_TESTER convention):
  0 = PASSED
  1 = FAILED
  2 = BLOCKED
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import asyncio
import inspect
import json
from sts2_autotest.adapters.agent import AgentAdapter
from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis
from sts2_autotest.core.steam import SteamController
from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    ScreenshotHealthDetector,
    TesseractOcrProvider,
    VisualQaEngine,
    build_visual_qa_payload,
)
from sts2_autotest.report_html import write_html_report

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

_IS_MACOS = platform.system() == "Darwin"
_IS_WINDOWS = platform.system() == "Windows"
_IS_LINUX = platform.system() == "Linux"
_GAME_WINDOW_TITLE = "Slay the Spire 2"
_GAWAIN_CHARACTER_IDS = {"gawain", "gawainmod-gawain", "gawain:character"}
_GAWAIN_CHARACTER_NAMES = {"gawain", "高文"}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    status: str  # PASSED | FAILED | BLOCKED | SKIPPED
    evidence: str = ""
    details: str = ""


@dataclass
class TestAgentResult:
    conclusion: str  # PASSED | FAILED | BLOCKED
    results: list[CheckResult] = field(default_factory=list)
    failure_details: str = ""
    blocked_details: str = ""
    artifact_dir: str = ""
    exit_code: int = 0


@dataclass
class TestPlanConfig:
    """Parsed test-plan YAML, merged with CLI overrides."""

    task_id: str = ""
    mod_project: str = ""
    mod_name: str = ""  # derived from csproj if not set
    infra_path: str = ""
    test_plan_path: str = ""
    game_mods_path: str = ""
    steam_app_id: str = "2868840"
    ping_timeout_seconds: int = 90
    skip_deploy: bool = False
    skip_launch_game: bool = False
    skip_game_smoke: bool = False
    require_game_running: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_path(base: Path, value: str) -> Path:
    """Resolve a possibly-relative path against base."""
    p = Path(value)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def _find_project_root() -> Path:
    """Return the STS2-AUTOTEST project root.

    This file: src/sts2_autotest/core/test_agent_runner.py
    parent        → core/
    parent.parent → sts2_autotest/
    parent.parent.parent → src/
    parent.parent.parent.parent → STS2-AUTOTEST/  (repo root)
    """
    return Path(__file__).resolve().parent.parent.parent.parent


def _normalize_window_token(value: Any) -> str:
    """Normalize window/process labels for loose title matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _select_macos_window(
    windows: list[dict[str, Any]],
    window_title: str,
) -> tuple[int, tuple[int, int]] | None:
    target = _normalize_window_token(window_title)
    if not target:
        return None

    candidates: list[tuple[int, int, int, int, int]] = []
    for window in windows:
        layer = int(window.get("layer", 0) or 0)
        if layer != 0:
            continue

        owner = _normalize_window_token(window.get("owner_name", ""))
        name = _normalize_window_token(window.get("window_name", ""))
        if not owner and not name:
            continue
        if not (
            target in owner
            or owner in target
            or target in name
            or name in target
        ):
            continue

        width = int(round(float(window.get("width", 0) or 0)))
        height = int(round(float(window.get("height", 0) or 0)))
        window_id = int(window.get("window_id", 0) or 0)
        if window_id <= 0 or width <= 0 or height <= 0:
            continue

        exact_match = int(target == owner or target == name)
        candidates.append((exact_match, width * height, window_id, width, height))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    _, _, window_id, width, height = candidates[0]
    return window_id, (width, height)


def _find_macos_window(window_title: str) -> tuple[int, tuple[int, int]] | None:
    """Return the best matching macOS on-screen window id and size."""
    try:
        import Quartz
    except Exception:
        return None

    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll,
        Quartz.kCGNullWindowID,
    ) or []
    normalized_windows = []
    for window in windows:
        bounds = window.get(Quartz.kCGWindowBounds, {}) or {}
        normalized_windows.append(
            {
                "layer": window.get(Quartz.kCGWindowLayer, 0),
                "owner_name": window.get(Quartz.kCGWindowOwnerName, ""),
                "window_name": window.get(Quartz.kCGWindowName, ""),
                "width": bounds.get("Width", 0),
                "height": bounds.get("Height", 0),
                "window_id": window.get(Quartz.kCGWindowNumber, 0),
            }
        )
    return _select_macos_window(normalized_windows, window_title)


def _capture_macos_window_png(path: Path, window_title: str) -> bool:
    """Capture a specific macOS window directly into a PNG file."""
    try:
        import Quartz
        from AppKit import NSBitmapImageRep, NSPNGFileType
    except Exception:
        selector_source = inspect.getsource(_normalize_window_token) + "\n\n" + inspect.getsource(_select_macos_window)
        script = f"""
from __future__ import annotations

from pathlib import Path
import re
import sys

import Quartz
from AppKit import NSBitmapImageRep, NSPNGFileType


{selector_source}


path = Path(sys.argv[1])
window_title = sys.argv[2]
quartz_windows = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionAll,
    Quartz.kCGNullWindowID,
) or []
windows = []
for window in quartz_windows:
    bounds = window.get(Quartz.kCGWindowBounds, {{}}) or {{}}
    windows.append({{
        "layer": window.get(Quartz.kCGWindowLayer, 0),
        "owner_name": window.get(Quartz.kCGWindowOwnerName, ""),
        "window_name": window.get(Quartz.kCGWindowName, ""),
        "width": bounds.get("Width", 0),
        "height": bounds.get("Height", 0),
        "window_id": window.get(Quartz.kCGWindowNumber, 0),
    }})

match = _select_macos_window(windows, window_title)
if match is None:
    raise SystemExit(1)

window_id, _ = match
image = Quartz.CGWindowListCreateImage(
    Quartz.CGRectNull,
    Quartz.kCGWindowListOptionIncludingWindow,
    window_id,
    Quartz.kCGWindowImageBoundsIgnoreFraming,
)
if image is None:
    raise SystemExit(1)

path.parent.mkdir(parents=True, exist_ok=True)
bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
if bitmap is None:
    raise SystemExit(1)
png_data = bitmap.representationUsingType_properties_(NSPNGFileType, {{}})
if png_data is None:
    raise SystemExit(1)
if not png_data.writeToFile_atomically_(str(path), True):
    raise SystemExit(1)
"""
        try:
            result = subprocess.run(
                ["python3", "-c", script, str(path), window_title],
                capture_output=True,
                text=True,
                timeout=15.0,
            )
        except Exception:
            return False
        return result.returncode == 0 and path.exists()

    match = _find_macos_window(window_title)
    if match is None:
        return False

    window_id, _ = match
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
    if bitmap is None:
        return False
    png_data = bitmap.representationUsingType_properties_(NSPNGFileType, {})
    if png_data is None:
        return False
    return bool(png_data.writeToFile_atomically_(str(path), True))


def _steam_fallback_delay_seconds() -> float:
    raw = os.environ.get("STS2_STEAM_FALLBACK_DELAY", "5")
    try:
        return float(raw)
    except ValueError:
        return 5.0


def _find_sln_or_csproj(project_path: Path) -> Path | None:
    """Find a .sln or .csproj file under *project_path*.

    Returns the first match.  (C1: scans recursively, prefers .sln over .csproj)

    Excludes common non-source directories (.git, .claude, .agent-runs, bin, obj).
    Prefers root-level files over deeply nested ones.
    """
    _EXCLUDE_PARTS = {".git", ".claude", ".agent-runs", "bin", "obj", "node_modules", ".godot"}

    def _is_excluded(p: Path) -> bool:
        return bool(_EXCLUDE_PARTS & set(p.relative_to(project_path).parts))

    slns = sorted(
        (p for p in project_path.rglob("*.sln") if not _is_excluded(p)),
        key=lambda p: len(p.relative_to(project_path).parts),
    )
    if slns:
        return slns[0]
    csprojs = sorted(
        (p for p in project_path.rglob("*.csproj") if not _is_excluded(p)),
        key=lambda p: len(p.relative_to(project_path).parts),
    )
    return csprojs[0] if csprojs else None


def _find_build_output(project_path: Path) -> Path | None:
    """Look for the most recent bin/Release or bin/Debug output directory.

    C1 enhancement: scans all nested bin dirs, not just the first match.
    Skips directories that cannot be stat'd (permission errors).
    """
    _EXCLUDE_PARTS = {".git", ".claude", ".agent-runs", "obj", "node_modules"}
    _GODOT_CACHE_PARTS = {"imported", "extension_api"}

    def _is_excluded(p: Path) -> bool:
        parts = p.relative_to(project_path).parts
        if _EXCLUDE_PARTS & set(parts):
            return True
        if ".godot" in parts and (_GODOT_CACHE_PARTS & set(parts)):
            return True
        return False

    candidates: list[tuple[Path, float]] = []
    for pattern in ["**/bin/Release", "**/bin/Debug"]:
        for d in project_path.glob(pattern):
            if not d.is_dir():
                continue
            if _is_excluded(d):
                continue
            try:
                mtime = d.stat().st_mtime
            except OSError:
                continue
            candidates.append((d, mtime))
    # Prefer Release over Debug; within same config prefer most recently modified
    candidates.sort(key=lambda item: (0 if "Release" in str(item[0]) else 1, -item[1]))
    return candidates[0][0] if candidates else None


def _find_mods_path() -> Path | None:
    """Auto-detect Steam STS2 mods directory.  (C4 enhancement)

    Search order:
      1. STS2_MODS_PATH env var
      2. <game_dir>/Mods (Windows / Linux native)
      3. <game_dir>/BepInEx/plugins (BepInEx layout)
    """
    env = os.environ.get("STS2_MODS_PATH")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    from sts2_autotest.adapters.discovery import find_game_dir

    game_dir = find_game_dir()
    if not game_dir:
        return None
    for candidate in [
        game_dir / "Mods",
        game_dir / "BepInEx" / "plugins",
    ]:
        if candidate.is_dir():
            return candidate
    # Create Mods directory if game dir exists
    mods = game_dir / "Mods"
    try:
        mods.mkdir(parents=True, exist_ok=True)
        return mods
    except OSError:
        return None


def _load_test_plan(plan_path: Path) -> dict[str, Any] | None:
    """Load a test-plan YAML file."""
    if not plan_path.exists():
        return None
    if yaml is None:
        return None
    try:
        data = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        return dict(data)
    except Exception:
        return None


def _merge_config(cli_cfg: TestPlanConfig, plan: dict[str, Any] | None) -> TestPlanConfig:
    """Merge CLI overrides on top of test-plan defaults.  CLI always wins."""
    if plan is None:
        return cli_cfg

    cfg = TestPlanConfig()
    inputs = plan.get("inputs", {}) or {}
    env = plan.get("environment", {}) or {}

    # Plan defaults
    cfg.task_id = plan.get("task_id", "") or ""
    cfg.mod_project = inputs.get("mod_project", "") or ""
    cfg.steam_app_id = str(env.get("steam_app_id", cfg.steam_app_id))
    cfg.require_game_running = bool(env.get("require_game_running", True))
    cfg.test_plan_path = str(plan.get("_source_path", ""))

    # CLI overrides (non-empty / non-default wins)
    if cli_cfg.task_id:
        cfg.task_id = cli_cfg.task_id
    if cli_cfg.mod_project:
        cfg.mod_project = cli_cfg.mod_project
    if cli_cfg.infra_path:
        cfg.infra_path = cli_cfg.infra_path
    if cli_cfg.game_mods_path:
        cfg.game_mods_path = cli_cfg.game_mods_path
    if cli_cfg.test_plan_path:
        cfg.test_plan_path = cli_cfg.test_plan_path
    if cli_cfg.steam_app_id != "2868840":
        cfg.steam_app_id = cli_cfg.steam_app_id
    cfg.ping_timeout_seconds = cli_cfg.ping_timeout_seconds
    cfg.skip_deploy = cli_cfg.skip_deploy
    cfg.skip_launch_game = cli_cfg.skip_launch_game
    cfg.skip_game_smoke = cli_cfg.skip_game_smoke

    return cfg


def _run_command(
    name: str,
    cmd: list[str],
    log_path: Path,
    *,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run a subprocess, appending stdout/stderr to *log_path*.  Returns (exit_code, output)."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"# {name}\n")
        f.write(f"> {' '.join(cmd)}\n")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=merged_env,
        )
        output = result.stdout.decode("utf-8", errors="replace").strip()
        err = result.stderr.decode("utf-8", errors="replace").strip()
        combined = output + ("\n" + err if err else "")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(combined + "\n")
            f.write(f"ExitCode: {result.returncode}\n")
        return result.returncode, combined
    except subprocess.TimeoutExpired:
        msg = f"Timeout after {timeout}s"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{msg}\nExitCode: -1\n")
        return -1, msg
    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]}"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{msg}\nExitCode: -2\n")
        return -2, msg
    except OSError as exc:
        msg = f"OS error running '{' '.join(cmd)}': {exc}"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{msg}\nExitCode: -3\n")
        return -3, msg


def _run_launch_command(name: str, cmd: list[str], log_path: Path) -> str:
    """Run a desktop launch command and append its output to *log_path*."""
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"# {name}\n")
        f.write(f"> {' '.join(cmd)}\n")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15.0)
    except subprocess.TimeoutExpired as exc:
        msg = f"Timeout after {exc.timeout}s"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
            f.write("ExitCode: -1\n")
        raise RuntimeError(msg) from exc
    output = (
        result.stdout.decode("utf-8", errors="replace")
        + result.stderr.decode("utf-8", errors="replace")
    ).strip()
    with log_path.open("a", encoding="utf-8") as f:
        if output:
            f.write(output + "\n")
        f.write(f"ExitCode: {result.returncode}\n")
    if result.returncode != 0:
        raise RuntimeError(output or f"{cmd[0]} exited {result.returncode}")
    return output


def _steam_executable_candidates() -> list[Path]:
    return [
        Path.home() / "Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS/steam_osx",
        Path("/Applications/Steam.app/Contents/MacOS/steam_osx"),
    ]


def _find_steam_executable() -> Path | None:
    for steam_exe in _steam_executable_candidates():
        if steam_exe.exists():
            return steam_exe
    return None


def _popen_steam_executable(steam_exe: Path, args: list[str], launch_log: Path, name: str) -> None:
    with launch_log.open("a", encoding="utf-8") as f:
        f.write(f"# {name}\n")
        f.write(f"> {steam_exe} {' '.join(args)}\n")
    subprocess.Popen(
        [str(steam_exe), *args],
        cwd=str(steam_exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _start_steam_client_without_polling(launch_log: Path) -> Path | None:
    """Start the Steam client in a desktop session without reading process lists."""
    failures: list[str] = []
    steam_app = Path("/Applications/Steam.app")
    if steam_app.exists():
        try:
            _run_launch_command("Start Steam", ["open", str(steam_app)], launch_log)
            return _find_steam_executable()
        except RuntimeError as exc:
            failures.append(str(exc))
            with launch_log.open("a", encoding="utf-8") as f:
                f.write(f"Steam.app launch failed: {exc}\n")

    for steam_exe in _steam_executable_candidates():
        if not steam_exe.exists():
            continue
        _popen_steam_executable(steam_exe, [], launch_log, "Start Steam executable")
        return steam_exe

    details = "; ".join(failures) if failures else "Steam executable not found"
    raise RuntimeError(details)


def _launch_game_via_desktop_open(app_id: str, launch_log: Path) -> None:
    """Launch through Steam without process-list polling.

    This fallback is for restricted desktop sandboxes where macOS denies
    psutil/sysctl process scans. It still uses Steam, not the game binary.
    """
    if _IS_MACOS:
        steam_exe = _start_steam_client_without_polling(launch_log)
        time.sleep(_steam_fallback_delay_seconds())
        try:
            _run_launch_command(
                "Start game via Steam bundle",
                ["open", "-b", "com.valvesoftware.steam", f"steam://run/{app_id}"],
                launch_log,
            )
        except RuntimeError as exc:
            with launch_log.open("a", encoding="utf-8") as f:
                f.write(f"Steam bundle URL launch failed: {exc}\n")
            try:
                _run_launch_command("Start game via Steam", ["open", f"steam://run/{app_id}"], launch_log)
                return
            except RuntimeError as url_exc:
                with launch_log.open("a", encoding="utf-8") as f:
                    f.write(f"Steam URL handler failed: {url_exc}\n")
            if steam_exe is None:
                raise
            _popen_steam_executable(
                steam_exe,
                ["-applaunch", app_id],
                launch_log,
                "Start game via Steam executable",
            )
        return
    if _IS_WINDOWS:
        os.startfile(f"steam://run/{app_id}")  # type: ignore[attr-defined]
        return
    _run_launch_command("Start game via Steam", ["xdg-open", f"steam://run/{app_id}"], launch_log)


def _as_msbuild_dir(path: Path) -> str:
    """Return an MSBuild directory property value with a trailing separator."""
    value = str(path)
    if value.endswith(("/", "\\")):
        return value
    return value + os.sep


def _find_root_manifest(project_path: Path, mod_name: str) -> Path | None:
    preferred = project_path / f"{mod_name}.json"
    if preferred.exists():
        return preferred
    manifests = sorted(project_path.glob("*.json"))
    return manifests[0] if manifests else None


def _manifest_declares_pck(project_path: Path, mod_name: str) -> bool:
    manifest = _find_root_manifest(project_path, mod_name)
    if manifest is None:
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("has_pck"))


def _read_manifest_id(project_path: Path, mod_name: str) -> str:
    manifest = _find_root_manifest(project_path, mod_name)
    if manifest is None:
        return mod_name
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return mod_name
    manifest_id = str(data.get("id", "")).strip()
    return manifest_id or mod_name


def _find_godot_path() -> str:
    env_path = os.environ.get("GODOT_PATH", "").strip()
    if env_path:
        return env_path
    if _IS_MACOS:
        for candidate in [
            Path.home() / "Applications/Godot.app/Contents/MacOS/Godot",
            Path("/Applications/Godot.app/Contents/MacOS/Godot"),
            Path("/Applications/Godot_mono.app/Contents/MacOS/Godot"),
        ]:
            if candidate.exists():
                return str(candidate)
    return ""


def _derive_mod_name(mod_project: Path) -> str:
    """Derive the mod name from the csproj file or project directory name.

    Priority:
      1. Root-level .csproj AssemblyName (parsed from XML)
      2. Root-level .csproj filename without extension
      3. Project directory name
    """
    csproj = _find_sln_or_csproj(mod_project)
    if csproj and csproj.suffix == ".csproj":
        try:
            text = csproj.read_text(encoding="utf-8")
            import re
            m = re.search(r"<AssemblyName>\s*(\S+?)\s*</AssemblyName>", text)
            if m:
                return m.group(1)
            m = re.search(r"<RootNamespace>\s*(\S+?)\s*</RootNamespace>", text)
            if m:
                return m.group(1)
        except Exception:
            pass
        return csproj.stem
    return mod_project.name


def _detect_os() -> str:
    """Return a human-readable OS identifier."""
    if _IS_MACOS:
        return f"macOS {platform.mac_ver()[0]}"
    if _IS_WINDOWS:
        return f"Windows {platform.win32_ver()[0]}"
    if _IS_LINUX:
        try:
            import distro
            return f"{distro.name()} {distro.version()}"
        except Exception:
            return f"Linux {platform.release()}"
    return f"{platform.system()} {platform.release()}"


# ---------------------------------------------------------------------------
# Test Agent Runner
# ---------------------------------------------------------------------------


class TestAgentRunner:
    """Cross-platform Test Agent runner.

    Usage::

        runner = TestAgentRunner(
            mod_project="../STS2-GAWAIN",
            task_id="gawain-localization-key-fix",
            infra_path="../sts2-dev-infra",
        )
        result = runner.run()
        print(result.conclusion)  # PASSED / FAILED / BLOCKED
    """

    def __init__(
        self,
        mod_project: str,
        task_id: str,
        infra_path: str,
        *,
        mod_name: str = "",
        test_plan_path: str = "",
        game_mods_path: str = "",
        steam_app_id: str = "2868840",
        ping_timeout_seconds: int = 90,
        skip_deploy: bool = False,
        skip_launch_game: bool = False,
        skip_game_smoke: bool = False,
        require_game_running: bool = True,
    ):
        project_root = _find_project_root()
        self._mod_project_path = _resolve_path(project_root, mod_project)
        self._infra_path = _resolve_path(project_root, infra_path)
        self._task_id = task_id
        self._test_plan_path = (
            _resolve_path(project_root, test_plan_path) if test_plan_path else None
        )
        self._game_mods_path = (
            _resolve_path(project_root, game_mods_path) if game_mods_path else None
        )
        self._steam_app_id = steam_app_id
        self._ping_timeout = ping_timeout_seconds
        self._skip_deploy = skip_deploy
        self._skip_launch_game = skip_launch_game
        self._skip_game_smoke = skip_game_smoke
        self._require_game_running = require_game_running

        # Derive mod name for deployment (Issue 1 fix)
        self._mod_name = mod_name or _derive_mod_name(self._mod_project_path)

        self._artifact_dir = self._mod_project_path / "automation/autotest/output" / task_id
        self._state_dir = self._artifact_dir / "state"
        self._screenshot_dir = self._artifact_dir / "screenshots"
        self._report_path = self._artifact_dir / "test-report.md"

        self.results: list[CheckResult] = []
        self._conclusion = "PASSED"
        self._failure_details = ""
        self._blocked_details = ""

    # -- public API ---------------------------------------------------------

    def run(self) -> TestAgentResult:
        """Execute the full test-agent workflow.  Returns a TestAgentResult."""
        self._ensure_dirs()
        try:
            self._step_validate_inputs()
            self._step_build()
            self._step_localization_check()
            self._step_deploy()
            self._step_launch_game()
            self._step_game_smoke()
        except _Blocked as exc:
            self._conclusion = "BLOCKED"
            self._blocked_details = str(exc)
        except _Failed as exc:
            self._conclusion = "FAILED"
            self._failure_details = str(exc)
        finally:
            self._write_report()
            self._generate_html_report()
        return TestAgentResult(
            conclusion=self._conclusion,
            results=list(self.results),
            failure_details=self._failure_details,
            blocked_details=self._blocked_details,
            artifact_dir=str(self._artifact_dir),
            exit_code={"PASSED": 0, "FAILED": 1, "BLOCKED": 2}[self._conclusion],
        )

    # -- internal steps -----------------------------------------------------

    def _ensure_dirs(self) -> None:
        for d in [self._artifact_dir, self._state_dir, self._screenshot_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _add(self, name: str, status: str, evidence: str = "", details: str = "") -> None:
        self.results.append(CheckResult(name=name, status=status, evidence=evidence, details=details))

    # -- evidence helpers -----------------------------------------------------

    def _capture_screenshot(self, name: str) -> str:
        """Capture a screenshot and save to the report screenshot dir.

        On macOS we capture the actual game window directly so report evidence
        cannot silently degrade into a desktop or Steam screenshot.
        Other platforms keep the existing primary-monitor fallback.
        """
        try:
            path = self._screenshot_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if _IS_MACOS:
                if not _capture_macos_window_png(path, _GAME_WINDOW_TITLE):
                    print(
                        f"[agent-test] WARNING: Screenshot skipped ({name}): "
                        f"game window '{_GAME_WINDOW_TITLE}' was not available",
                        file=sys.__stdout__,
                    )
                    return ""
                return str(path.relative_to(self._mod_project_path))

            import mss
            with mss.mss() as sct:
                sct.shot(mon=1, output=str(path))
            return str(path.relative_to(self._mod_project_path))
        except Exception as exc:
            print(f'[agent-test] WARNING: Screenshot failed ({name}): {exc}', file=sys.__stdout__)
            return ''

    def _save_state_snapshot(self, step_name: str, state_dict: dict) -> str:
        """Save a state JSON snapshot to state dir.

        Returns the relative evidence path (for the report).
        """
        path = self._state_dir / f"{step_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state_dict, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
        return str(path.relative_to(self._mod_project_path))

    # ------------------------------------------------------------------
    # Step 1: Validate inputs
    # ------------------------------------------------------------------

    def _step_validate_inputs(self) -> None:
        # Test plan
        if self._test_plan_path is not None and not self._test_plan_path.exists():
            raise _Blocked(f"Test plan not found: {self._test_plan_path}")
        if self._test_plan_path:
            self._add("Test Plan", "PASSED", str(self._test_plan_path))
        else:
            self._add("Test Plan", "SKIPPED", "No test plan provided")

        # Mod project
        if not self._mod_project_path.exists():
            raise _Blocked(f"Mod project path not found: {self._mod_project_path}")
        self._add("Mod Project", "PASSED", str(self._mod_project_path))

        # Infra path
        if not self._infra_path.exists():
            raise _Blocked(f"Infra path not found: {self._infra_path}")
        self._add("Infra Path", "PASSED", str(self._infra_path))

    # ------------------------------------------------------------------
    # Step 2: Build
    # ------------------------------------------------------------------

    def _step_build(self) -> None:
        build_log = self._artifact_dir / "build.log"
        target = _find_sln_or_csproj(self._mod_project_path)
        if target is None:
            raise _Blocked(
                f"No .sln or .csproj build target found under {self._mod_project_path}"
            )

        # dotnet restore
        rc, out = _run_command("dotnet restore", ["dotnet", "restore", str(target)], build_log)
        if rc != 0:
            raise _Failed(f"dotnet restore failed. See build.log.\n{out[:500]}")

        # dotnet build
        build_cmd = ["dotnet", "build", str(target), "--no-restore"]
        if self._skip_deploy:
            build_cmd.append("-p:ModsPath=")
        elif self._game_mods_path is not None:
            build_cmd.append(f"-p:ModsPath={_as_msbuild_dir(self._game_mods_path)}")
        rc, out = _run_command("dotnet build", build_cmd, build_log)
        if rc != 0:
            raise _Failed(f"dotnet build failed. See build.log.\n{out[:500]}")

        if not self._skip_deploy and _manifest_declares_pck(self._mod_project_path, self._mod_name):
            publish_cmd = ["dotnet", "publish", str(target), "--no-restore"]
            if self._game_mods_path is not None:
                publish_cmd.append(f"-p:ModsPath={_as_msbuild_dir(self._game_mods_path)}")
            godot_path = _find_godot_path()
            if godot_path:
                publish_cmd.append(f"-p:GodotPath={godot_path}")
            rc, out = _run_command("dotnet publish", publish_cmd, build_log, timeout=300.0)
            if rc != 0:
                raise _Failed(f"dotnet publish failed. See build.log.\n{out[:500]}")
            if self._game_mods_path is not None:
                pck_name = _read_manifest_id(self._mod_project_path, self._mod_name)
                expected_pck = self._game_mods_path / self._mod_name / f"{pck_name}.pck"
                if not expected_pck.exists():
                    raise _Failed(f"dotnet publish did not create expected pck: {expected_pck}")

        # C1: locate build output
        build_out = _find_build_output(self._mod_project_path)
        evidence = "build.log"
        if build_out:
            evidence += f"; output: {build_out}"
        self._add("Build", "PASSED", evidence)

    # ------------------------------------------------------------------
    # Step 3: Localization check
    # ------------------------------------------------------------------

    def _step_localization_check(self) -> None:
        loc_script = self._infra_path / "scripts" / "check-localization.py"
        if not loc_script.exists():
            raise _Blocked(f"Localization checker not found: {loc_script}")

        loc_log = self._artifact_dir / "localization-check.log"
        python = sys.executable  # use the same Python that runs us
        rc, out = _run_command(
            "localization check",
            [python, str(loc_script), "--project", str(self._mod_project_path)],
            loc_log,
        )
        if rc == 2:
            raise _Blocked(f"Localization checker could not run. See localization-check.log.\n{out[:500]}")
        if rc == 1:
            raise _Failed(f"Localization check failed. See localization-check.log.\n{out[:500]}")
        self._add("Localization Check", "PASSED", "localization-check.log")

    # ------------------------------------------------------------------
    # Step 4: Deploy
    # ------------------------------------------------------------------

    def _step_deploy(self) -> None:
        deploy_log = self._artifact_dir / "deploy.log"
        if self._skip_deploy:
            deploy_log.write_text("Skipped by --skip-deploy\n", encoding="utf-8")
            self._add("Deploy Mod", "SKIPPED", "deploy.log")
            return

        mods_path = self._game_mods_path
        if mods_path is None:
            # C4: auto-detect
            mods_path = _find_mods_path()
        if mods_path is None:
            deploy_log.write_text("GameModsPath not provided; deploy skipped.\n", encoding="utf-8")
            self._add("Deploy Mod", "BLOCKED", "deploy.log", "GameModsPath not provided and auto-detection failed")
            if not self._skip_game_smoke:
                raise _Blocked("GameModsPath not provided. Use --game-mods-path or --skip-deploy --skip-game-smoke.")
            return

        build_out = _find_build_output(self._mod_project_path)
        if build_out is None:
            raise _Blocked("No build output directory found for deployment.")

        target_dir = mods_path / self._mod_name
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Copy all files from build output to mods dir
            for item in build_out.iterdir():
                dst = target_dir / item.name
                if item.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
            deploy_log.write_text(f"Copied {build_out} to {target_dir}\n", encoding="utf-8")
            self._add("Deploy Mod", "PASSED", "deploy.log")
        except OSError as exc:
            raise _Blocked(f"Failed to deploy mod: {exc}")

    # ------------------------------------------------------------------
    # Step 5: Launch game
    # ------------------------------------------------------------------

    def _step_launch_game(self) -> None:
        launch_log = self._artifact_dir / "launch.log"
        if self._skip_launch_game or self._skip_game_smoke:
            launch_log.write_text("Skipped game launch.\n", encoding="utf-8")
            self._add("Launch Game", "SKIPPED", "launch.log")
            return

        steam = SteamController(
            app_id=self._steam_app_id,
            startup_timeout=60.0,
        )
        try:
            steam_pid = steam.start_steam()
            game_pid = steam.start_game(reuse_existing=True)
        except Exception as exc:
            launch_log.write_text(
                f"SteamController launch failed for app {self._steam_app_id}: {exc}\n"
                "Falling back to desktop open commands.\n",
                encoding="utf-8",
            )
            try:
                _launch_game_via_desktop_open(self._steam_app_id, launch_log)
            except Exception as fallback_exc:
                with launch_log.open("a", encoding="utf-8") as f:
                    f.write(f"Desktop open fallback failed: {fallback_exc}\n")
                raise _Blocked(f"Failed to launch game via Steam: {fallback_exc}") from fallback_exc

            with launch_log.open("a", encoding="utf-8") as f:
                f.write(f"Steam launch URI: steam://run/{self._steam_app_id}\n")
            self._add("Launch Game", "PASSED", "launch.log")
            return

        launch_log.write_text(
            "\n".join(
                [
                    f"Started Steam PID {steam_pid}",
                    f"Started game PID {game_pid}",
                    f"Steam launch URI: steam://run/{self._steam_app_id}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self._add("Launch Game", "PASSED", "launch.log")

    # ------------------------------------------------------------------
    # Step 6: Game smoke test
    # ------------------------------------------------------------------

    def _state_to_dict(self, state: Any) -> dict[str, Any]:
        """Convert adapter state objects or dict-like payloads into a plain dict."""
        if isinstance(state, dict):
            return dict(state)
        if hasattr(state, "model_dump"):
            dumped = state.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {}
        if hasattr(state, "__dict__"):
            return {
                key: value
                for key, value in vars(state).items()
                if not key.startswith("_") and value is not None
            }
        return {}

    def _is_gawain_character_value(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lower = text.lower()
        return (
            lower in _GAWAIN_CHARACTER_IDS
            or lower in _GAWAIN_CHARACTER_NAMES
            or "高文" in text
            or "gawain" in lower
        )

    def _resolve_gawain_character_option_index(self, state: dict[str, Any]) -> int:
        """Find Gawain's character-select option index in an STS2-Agent state payload."""
        character_select = state.get("character_select") or state.get("multiplayer_lobby") or {}
        characters = character_select.get("characters", []) if isinstance(character_select, dict) else []
        for fallback_index, character in enumerate(characters):
            if not isinstance(character, dict):
                continue
            identity_fields = (
                character.get("character_id"),
                character.get("id"),
                character.get("character"),
                character.get("name"),
                character.get("line"),
            )
            if not any(self._is_gawain_character_value(value) for value in identity_fields):
                continue
            index = character.get("index", fallback_index)
            if isinstance(index, int):
                return index
        raise _Failed("Gawain character option was not found in character_select state")

    def _find_unresolved_localization_keys(self, payload: Any) -> list[str]:
        """Return unresolved localization-like strings found in runtime state."""
        findings: list[str] = []
        raw_key = re.compile(r"\bGAWAINMOD-[A-Z0-9_]+(?:\.[A-Za-z0-9_.-]+)+")
        generic_failures = ("missing localization", "KeyNotFound")

        def visit(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    visit(child, f"{path}.{key}" if path else str(key))
                return
            if isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")
                return
            if not isinstance(value, str):
                return
            lower = value.lower()
            if raw_key.search(value) or any(pattern.lower() in lower for pattern in generic_failures):
                findings.append(f"{path}: {value}")

        visit(payload, "")
        return findings

    def _assert_no_unresolved_localization_keys(self, label: str, state: dict[str, Any]) -> None:
        findings = self._find_unresolved_localization_keys(state)
        if findings:
            preview = "; ".join(findings[:5])
            raise _Failed(f"Raw localization keys found in {label}: {preview}")

    def _require_localized_text(self, value: Any, label: str) -> None:
        text = str(value or "").strip()
        if not text:
            raise _Failed(f"Missing localized text for {label}")
        if self._find_unresolved_localization_keys(text):
            raise _Failed(f"Raw localization key found for {label}: {text}")

    def _assert_gawain_selected(self, state: dict[str, Any]) -> None:
        combat = state.get("combat", {}) or {}
        players = combat.get("players", []) if isinstance(combat, dict) else []
        for player in players:
            if not isinstance(player, dict):
                continue
            if (
                self._is_gawain_character_value(player.get("character_id"))
                or self._is_gawain_character_value(player.get("character_name"))
            ):
                return
        raise _Failed("Expected active combat player to be Gawain")

    def _assert_gawain_runtime_text_state(self, state: dict[str, Any]) -> None:
        """Validate the runtime payload exposes Gawain text for character, relic, and deck."""
        self._assert_no_unresolved_localization_keys("gawain-runtime-text", state)

        run = state.get("run", {}) or {}
        if not isinstance(run, dict):
            raise _Failed("Runtime state is missing run payload")
        if not self._is_gawain_character_value(run.get("character_id")):
            raise _Failed(f"Expected run.character_id to be Gawain, got {run.get('character_id')}")
        self._require_localized_text(run.get("character_name"), "run.character_name")

        relics = run.get("relics", [])
        relic = next(
            (
                item for item in relics
                if isinstance(item, dict)
                and str(item.get("relic_id", "")).strip() == "GAWAINMOD-MAGIC_TERMINAL"
            ),
            None,
        )
        if relic is None:
            raise _Failed("Starter relic GAWAINMOD-MAGIC_TERMINAL was not found in run.relics")
        self._require_localized_text(relic.get("name"), "starter relic name")
        self._require_localized_text(relic.get("description"), "starter relic description")

        required_cards = {
            "GAWAINMOD-STRIKE_GAWAIN",
            "GAWAINMOD-DEFEND_GAWAIN",
            "GAWAINMOD-EMERGENCY_RECRUIT",
            "GAWAINMOD-MAGIC_DRAW",
            "GAWAINMOD-PORTABLE_MAGIC_TERMINAL",
        }
        deck = run.get("deck", [])
        if not isinstance(deck, list):
            raise _Failed("Runtime state run.deck is missing or malformed")
        cards_by_id = {
            str(card.get("card_id", "")).strip(): card
            for card in deck
            if isinstance(card, dict)
        }
        missing = sorted(card_id for card_id in required_cards if card_id not in cards_by_id)
        if missing:
            raise _Failed(f"Starter deck is missing cards: {', '.join(missing)}")
        for card_id in sorted(required_cards):
            card = cards_by_id[card_id]
            self._require_localized_text(card.get("name"), f"{card_id} name")
            rules_text = card.get("resolved_rules_text") or card.get("rules_text")
            self._require_localized_text(rules_text, f"{card_id} rules text")

    def _act_and_wait(self, agent: AgentAdapter, action_name: str, params: dict[str, Any]) -> None:
        result = asyncio.run(agent.act(action_name, params))
        if result.status != "success":
            raise _Failed(f"Navigation failed at '{action_name}': {result.detail}")
        asyncio.run(agent.wait_until_actionable(timeout=15))

    def _is_existing_gawain_run_state(self, state: dict[str, Any]) -> bool:
        run = state.get("run", {}) or {}
        return isinstance(run, dict) and self._is_gawain_character_value(run.get("character_id"))

    def _is_character_select_ready(self, agent: AgentAdapter) -> bool:
        state = self._state_to_dict(asyncio.run(agent.get_state()))
        screen = str(state.get("screen", "")).upper()
        if "CHARACTER_SELECT" in screen or "START_RUN_LOBBY" in screen:
            self._save_state_snapshot("character-select", state)
            return True
        actions = asyncio.run(agent.get_available_actions())
        if "select_character" in actions and "embark" in actions:
            self._save_state_snapshot("character-select", state)
            return True
        return False

    def _open_character_select(self, agent: AgentAdapter) -> None:
        """Open character select, tolerating main-menu preload races and reused sessions."""
        last_detail = ""
        for _ in range(12):
            if self._is_character_select_ready(agent):
                return
            result = asyncio.run(agent.act("open_character_select", {}))
            if result.status == "success":
                asyncio.run(agent.wait_until_actionable(timeout=15))
                if self._is_character_select_ready(agent):
                    return
            last_detail = result.detail
            time.sleep(2)
        raise _Failed(f"Navigation failed at 'open_character_select': {last_detail}")

    def _select_gawain_character(self, agent: AgentAdapter) -> None:
        """Select Gawain, preferring stable ids over UI option positions."""
        for params in (
            {"character_id": "GAWAINMOD-GAWAIN"},
            {"character_id": "gawain"},
            {"character": "GAWAINMOD-GAWAIN"},
            {"character": "gawain"},
        ):
            result = asyncio.run(agent.act("select_character", params))
            if result.status == "success":
                asyncio.run(agent.wait_until_actionable(timeout=15))
                return

        character_select_state = self._state_to_dict(asyncio.run(agent.get_state()))
        self._save_state_snapshot("character-select", character_select_state)
        option_index = self._resolve_gawain_character_option_index(character_select_state)
        self._act_and_wait(agent, "select_character", {"option_index": option_index})

    def _advance_to_first_combat(self, agent: AgentAdapter, state_dict: dict[str, Any]) -> dict:
        """Advance through deterministic pre-combat screens until combat starts."""
        for _ in range(12):
            screen = str(state_dict.get("screen", "")).upper()
            if screen == "COMBAT":
                self._assert_gawain_selected(state_dict)
                self._assert_gawain_runtime_text_state(state_dict)
                return state_dict
            if screen == "EVENT":
                self._save_state_snapshot("navigation-event", state_dict)
                self._act_and_wait(agent, "choose_event_option", {"option_index": 0})
                state_dict = self._state_to_dict(asyncio.run(agent.get_state()))
                continue
            if screen == "MAP":
                self._save_state_snapshot("navigation-map", state_dict)
                self._act_and_wait(agent, "choose_map_node", {"option_index": 0})
                state_dict = self._state_to_dict(asyncio.run(agent.get_state()))
                continue
            raise _Failed(f"Expected COMBAT, EVENT, or MAP screen, got {state_dict.get('screen')}")
        raise _Failed(f"Expected COMBAT screen, got {state_dict.get('screen')}")

    def _navigate_to_first_combat(self, agent: AgentAdapter) -> dict:
        """Navigate from MAIN_MENU to first combat. Raises _Failed on failure."""
        state_dict = self._state_to_dict(asyncio.run(agent.get_state()))
        if not self._is_existing_gawain_run_state(state_dict):
            self._open_character_select(agent)
            self._select_gawain_character(agent)
            self._act_and_wait(agent, "embark", {})
            state_dict = self._state_to_dict(asyncio.run(agent.get_state()))
        return self._advance_to_first_combat(agent, state_dict)

    def _verify_card_and_screenshot(
        self, agent: AgentAdapter, card: dict, card_index: int, target_index: int,
    ) -> dict:
        """Play one card: screenshot before, play, verify, screenshot after.

        card is a dict from combat.hand[] with card_id, name, index, energy_cost,
        playable, dynamic_values.

        Returns a dict with verification results for the report.
        """
        card_id = card.get("card_id", f"card_{card_index}")
        card_name = card.get("name", card_id)
        result = {
            "card_id": card_id,
            "name": card_name,
            "index": card_index,
            "status": "UNKNOWN",
            "expected_damage": 0,
            "actual_damage": 0,
            "expected_block": 0,
            "actual_block": 0,
            "screenshot_before": "",
            "screenshot_after": "",
            "error": "",
        }

        for dv in card.get("dynamic_values", []):
            dv_name = dv.get("name", "")
            if dv_name == "damage":
                result["expected_damage"] = dv.get("current_value", dv.get("base_value", 0))
            elif dv_name == "block":
                result["expected_block"] = dv.get("current_value", dv.get("base_value", 0))

        result["screenshot_before"] = self._capture_screenshot(f"card-{card_id}-before.png")

        before = asyncio.run(agent.get_state())
        before_dict = self._state_to_dict(before)
        combat_before = before_dict.get("combat", {}) or {}
        enemies_before = combat_before.get("enemies", [])
        enemy_hp_before = enemies_before[0].get("current_hp", 0) if enemies_before else 0
        player_block_before = combat_before.get("player", {}).get("block", 0)
        self._save_state_snapshot(f"card-{card_id}-before", before_dict)

        try:
            play_result = asyncio.run(
                agent.act("play_card", {"card_id": card_id, "target_index": target_index})
            )
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"play_card failed: {exc}"
            return result

        if play_result.status != "success":
            result["status"] = "FAIL"
            result["error"] = f"play_card: {play_result.status}: {play_result.detail}"
            return result

        asyncio.run(agent.wait_until_actionable(timeout=10))

        after = asyncio.run(agent.get_state())
        after_dict = self._state_to_dict(after)
        combat_after = after_dict.get("combat", {}) or {}
        enemies_after = combat_after.get("enemies", [])
        enemy_hp_after = enemies_after[0].get("current_hp", 0) if enemies_after else 0
        player_block_after = combat_after.get("player", {}).get("block", 0)
        self._save_state_snapshot(f"card-{card_id}-after", after_dict)
        result["screenshot_after"] = self._capture_screenshot(f"card-{card_id}-after.png")

        errors = []
        if result["expected_damage"] > 0:
            hp_diff = enemy_hp_before - enemy_hp_after
            result["actual_damage"] = hp_diff
            if hp_diff != result["expected_damage"]:
                errors.append(f"damage: expected {result['expected_damage']}, got {hp_diff}")
        if result["expected_block"] > 0:
            block_gained = player_block_after - player_block_before
            result["actual_block"] = block_gained
            if block_gained != result["expected_block"]:
                errors.append(f"block: expected {result['expected_block']}, got {block_gained}")

        result["status"] = "OK" if not errors else "FAIL"
        if errors:
            result["error"] = "; ".join(errors)
        return result

    def _build_text_only_card_result(self, card: dict, card_index: int, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Capture text evidence for a card that is visible but not currently playable."""
        card_id = card.get("card_id", f"card_{card_index}")
        card_name = card.get("name", card_id)
        result = {
            "card_id": card_id,
            "name": card_name,
            "index": card_index,
            "status": "TEXT_ONLY",
            "expected_damage": 0,
            "actual_damage": 0,
            "expected_block": 0,
            "actual_block": 0,
            "screenshot_before": self._capture_screenshot(f"card-{card_id}-before.png"),
            "screenshot_after": "",
            "error": "",
        }
        self._require_localized_text(card_name, f"{card_id} hand name")
        rules_text = card.get("resolved_rules_text") or card.get("rules_text")
        self._require_localized_text(rules_text, f"{card_id} hand rules text")
        self._save_state_snapshot(f"card-{card_id}-before", state_dict)
        return result

    def _resolve_current_hand_card(self, state_dict: dict[str, Any], target_card: dict) -> dict[str, Any] | None:
        """Resolve the current copy of a hand card by id, preferring exact index when still valid."""
        combat = state_dict.get("combat", {}) or {}
        hand = combat.get("hand", []) if isinstance(combat, dict) else []
        if not isinstance(hand, list):
            return None
        target_id = str(target_card.get("card_id", "")).strip()
        target_index = target_card.get("index")
        for current in hand:
            if not isinstance(current, dict):
                continue
            if target_index is not None and current.get("index") == target_index and str(current.get("card_id", "")).strip() == target_id:
                return current
        for current in hand:
            if isinstance(current, dict) and str(current.get("card_id", "")).strip() == target_id:
                return current
        return None

    def _step_game_smoke(self) -> None:
        """Execute in-game smoke test via STS2-Agent API.

        Flow: wait for agent health -> navigate to first combat ->
        screenshot + verify each hand card -> validate raw keys.
        """
        if self._skip_game_smoke:
            smoke_log = self._artifact_dir / "game-smoke.log"
            smoke_log.write_text("Skipped by --skip-game-smoke\n", encoding="utf-8")
            self._add("Game Smoke", "SKIPPED", "game-smoke.log")
            if self._require_game_running:
                self._add("Game Required", "BLOCKED", "test-plan",
                          "require_game_running is true but --skip-game-smoke was passed")
            return

        # --- 6a: Wait for sts2-agent HTTP API ---
        agent = AgentAdapter(endpoint="http://127.0.0.1:8080", timeout=10)
        health_ok = False
        deadline = time.time() + self._ping_timeout
        while time.time() < deadline:
            try:
                health = asyncio.run(agent.health_check())
                if health.healthy:
                    health_ok = True
                    break
            except Exception:
                pass
            time.sleep(3)

        if not health_ok:
            raise _Blocked(
                f"STS2-Agent HTTP API did not respond within {self._ping_timeout}s. "
                "Ensure the game is running with STS2AIAgent mod loaded."
            )
        self._add("STS2-Agent Health", "PASSED", "http://127.0.0.1:8080/health")
        if not asyncio.run(agent.wait_until_actionable(timeout=self._ping_timeout)):
            raise _Blocked(
                f"STS2-Agent did not become actionable within {self._ping_timeout}s after health passed."
            )

        # --- 6b: Navigate to first combat ---
        state = self._navigate_to_first_combat(agent)
        self._add("First Combat Reached", "PASSED",
                  self._save_state_snapshot("combat-start", state))

        # --- 6c: Read hand ---
        combat = state.get("combat", {}) or {}
        hand = combat.get("hand", [])
        if not hand:
            raise _Failed("No cards in hand at combat start")

        # --- 6d: Verify each card ---
        self._card_results = []
        for card in hand:
            current_state = self._state_to_dict(asyncio.run(agent.get_state()))
            current_card = self._resolve_current_hand_card(current_state, card)
            if current_card is None:
                continue
            card_index = current_card.get("index", card.get("index", 0))
            available_actions = current_state.get("available_actions", [])
            can_play_cards = isinstance(available_actions, list) and "play_card" in available_actions
            if not can_play_cards or not bool(current_card.get("playable", False)):
                card_result = self._build_text_only_card_result(current_card, card_index, current_state)
            else:
                valid_targets = current_card.get("valid_target_indices", [])
                target_index = 0
                if isinstance(valid_targets, list) and valid_targets:
                    target_index = int(valid_targets[0])
                card_result = self._verify_card_and_screenshot(agent, current_card, card_index, target_index)
            self._card_results.append(card_result)

        passed_count = sum(1 for r in self._card_results if r["status"] == "OK")
        text_only_count = sum(1 for r in self._card_results if r["status"] == "TEXT_ONLY")
        failed = [r for r in self._card_results if r["status"] == "FAIL"]

        if failed:
            detail = "; ".join(
                f"{r['name']}({r['card_id']}): {r['error']}" for r in failed
            )
            raise _Failed(f"Card verification: {len(failed)} failed ({detail})")

        self._add("Card Smoke Test", "PASSED",
                  f"Verified {passed_count} playable cards, captured text for {text_only_count} additional cards; "
                  f"screenshots in automation/autotest/output/{self._task_id}/screenshots/")

        # --- 6e: Clean up ---
        asyncio.run(agent.act("abandon_run"))
        self._add("Abandon Run", "PASSED", "")

        # --- 6f: Scan for raw keys in final state ---
        final_state = asyncio.run(agent.get_state())
        final_dict = self._state_to_dict(final_state)
        self._assert_no_unresolved_localization_keys("final-state", final_dict)

        self._add("No Raw Key", "PASSED",
                  self._save_state_snapshot("final-state", final_dict))

    def _build_card_detail_table(self) -> str:
        """Build card verification table from _card_results for the report."""
        if not hasattr(self, "_card_results") or not self._card_results:
            return ""
        rows = "| 卡牌 | ID | 预期伤害 | 实际伤害 | 预期格挡 | 实际格挡 | 状态 | 截图 |\n"
        rows += "|------|-----|---------|---------|---------|---------|------|------|\n"
        for r in self._card_results:
            rows += (
                f"| {r['name']} | {r['card_id']} "
                f"| {r['expected_damage']} | {r['actual_damage']} "
                f"| {r['expected_block']} | {r['actual_block']} "
                f"| {r['status']} | before: {r['screenshot_before']}<br>after: {r['screenshot_after']} |\n"
            )
        return rows

    def _write_report(self) -> None:
        """Generate test-report.md in the artifact directory."""
        rows = "".join(
            f"| {r.name} | {r.status} | {r.evidence} |\n" for r in self.results
        ) or "| No checks executed | BLOCKED | test-report.md |\n"

        # Try to discover git branch and commit
        branch = ""
        commit = ""
        try:
            r = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, cwd=str(self._mod_project_path),
            )
            branch = r.stdout.decode().strip()
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["git", "log", "-1", "--format=%h %s"],
                capture_output=True, cwd=str(self._mod_project_path),
            )
            commit = r.stdout.decode().strip()
        except Exception:
            pass

        report = f"""# Test Report: {self._task_id}

## 测试结论

{self._conclusion}

## 环境

- Repo: {self._mod_project_path}
- Branch: {branch}
- Commit: {commit}
- STS2 version: N/A (not detected on this platform)
- BaseLib version: N/A (not detected on this platform)
- OS: {_detect_os()}
- Test runner: STS2-AUTOTEST (autotest agent-test)
- Infra path: {self._infra_path}

## 测试结果

| 测试项 | 结果 | 证据 |
|---|---|---|
{rows}
## 失败详情

{self._failure_details}

## 阻塞详情

{self._blocked_details}

## 附件

- artifact dir: {self._artifact_dir}
- build log: build.log
- localization log: localization-check.log
- deploy log: deploy.log
- launch log: launch.log
- state snapshots: state/
- screenshots: screenshots/

## 建议

- FAILED：交回 Developer Agent 修复。
- BLOCKED：先补齐环境、游戏、自动化接口或构建目标。
"""
        self._report_path.write_text(report, encoding="utf-8")

    def _status_to_html_report_result(self, status: str) -> str:
        mapping = {
            "PASSED": "通过",
            "FAILED": "失败",
            "BLOCKED": "阻塞",
            "SKIPPED": "跳过",
        }
        return mapping.get(status, "跳过")

    def _build_html_report_card_results(self) -> list[dict[str, Any]]:
        card_results = getattr(self, "_card_results", []) or []
        entries: list[dict[str, Any]] = []
        for result in card_results:
            exp: dict[str, Any] = {}
            if result.get("expected_damage"):
                exp["伤害"] = result["expected_damage"]
            if result.get("expected_block"):
                exp["格挡"] = result["expected_block"]
            status = str(result.get("status", "TEXT_ONLY"))
            report_result = {
                "OK": "通过",
                "FAIL": "失败",
                "TEXT_ONLY": "跳过",
            }.get(status, "跳过")
            before_path = self._normalize_html_artifact_path(result.get("screenshot_before", ""))
            after_path = self._normalize_html_artifact_path(result.get("screenshot_after", ""))
            entry: dict[str, Any] = {
                "card_id": result.get("card_id", ""),
                "name": result.get("name", ""),
                "cost": result.get("energy_cost"),
                "exp": exp,
                "result": report_result,
                "screenshot_before": before_path,
                "screenshot_after": after_path,
                "state_before": self._normalize_html_artifact_path(f"state/card-{result.get('card_id', '')}-before.json"),
                "state_after": self._normalize_html_artifact_path(f"state/card-{result.get('card_id', '')}-after.json") if after_path else "",
            }
            before_ocr = self._get_screenshot_ocr_payload(before_path)
            if before_ocr is not None:
                entry["screenshot_before_ocr"] = before_ocr
            after_ocr = self._get_screenshot_ocr_payload(after_path)
            if after_ocr is not None:
                entry["screenshot_after_ocr"] = after_ocr
            entries.append(entry)
        return entries

    def _get_screenshot_ocr_payload(self, screenshot_path: str) -> dict[str, Any] | None:
        analysis_by_path = getattr(self, "_screenshot_ocr", {}) or {}
        analysis = analysis_by_path.get(screenshot_path)
        if isinstance(analysis, ScreenshotOcrAnalysis):
            return analysis.model_dump(mode="json")
        if isinstance(analysis, dict):
            return analysis
        return None

    def _normalize_html_artifact_path(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        path = Path(text)
        if path.is_absolute():
            try:
                return str(path.relative_to(self._artifact_dir))
            except ValueError:
                return text
        candidate = self._artifact_dir / path
        if candidate.exists():
            return str(path)
        parts = path.parts
        for anchor in ("screenshots", "state"):
            if anchor in parts:
                return str(Path(*parts[parts.index(anchor):]))
        return text

    def _get_visual_qa_engine(self) -> VisualQaEngine:
        engine = getattr(self, "_visual_qa_engine", None)
        if isinstance(engine, VisualQaEngine):
            return engine

        framework_config = getattr(self, "_framework_config", None)
        provider_name = getattr(framework_config, "visual_qa_ocr_provider", "disabled")
        if not getattr(framework_config, "visual_qa_enabled", True):
            provider = DisabledOcrProvider()
        elif provider_name == "tesseract":
            provider = TesseractOcrProvider(
                command=getattr(framework_config, "visual_qa_tesseract_cmd", "tesseract"),
                lang=getattr(framework_config, "visual_qa_tesseract_lang", "chi_sim+eng"),
                timeout_seconds=getattr(
                    framework_config,
                    "visual_qa_timeout_seconds",
                    10.0,
                ),
            )
        else:
            provider = DisabledOcrProvider()

        health_enabled = getattr(framework_config, "visual_qa_health_enabled", True)
        health_provider = getattr(framework_config, "visual_qa_health_provider", "disabled")
        health_detector = ScreenshotHealthDetector(
            cv2_module="auto" if health_enabled and health_provider == "opencv" else None,
            low_variance_threshold=getattr(
                framework_config,
                "visual_qa_low_variance_threshold",
                1.0,
            ),
            low_brightness_threshold=getattr(
                framework_config,
                "visual_qa_low_brightness_threshold",
                5.0,
            ),
            high_brightness_threshold=getattr(
                framework_config,
                "visual_qa_high_brightness_threshold",
                250.0,
            ),
        )

        engine = VisualQaEngine(provider, health_detector=health_detector)
        self._visual_qa_engine = engine
        return engine

    def _analyze_html_report_screenshots(self) -> None:
        engine = self._get_visual_qa_engine()
        cache: dict[str, ScreenshotOcrAnalysis] = getattr(self, "_screenshot_ocr", {}) or {}
        for result in getattr(self, "_card_results", []) or []:
            for key in ("screenshot_before", "screenshot_after"):
                normalized = self._normalize_html_artifact_path(result.get(key, ""))
                if not normalized or normalized in cache:
                    continue
                cache[normalized] = engine.analyze_screenshot(self._artifact_dir / normalized)
        self._screenshot_ocr = cache

    def _build_html_report_config(self) -> dict[str, Any]:
        self._analyze_html_report_screenshots()
        card_results = self._build_html_report_card_results()
        test_cases: list[dict[str, Any]] = []
        for result in self.results:
            case = {
                "id": result.name,
                "name": result.name,
                "scenario": result.details or "see evidence",
                "assertions": [],
                "actual": result.details or "",
                "result": self._status_to_html_report_result(result.status),
                "steps": [
                    {
                        "name": "Execute",
                        "result": self._status_to_html_report_result(result.status),
                        "detail": result.evidence,
                    }
                ],
            }
            if result.name == "Card Smoke Test" and card_results:
                case["card_results"] = card_results
            test_cases.append(case)
        return {
            "test_run_id": self._task_id,
            "test_cases": test_cases,
            "card_results": card_results,
            "metadata": {
                "game": "Slay the Spire 2",
                "runner": "STS2-AUTOTEST",
                "repo": str(self._mod_project_path.name),
            },
            "_config_dir": str(self._artifact_dir),
        }

    def _build_visual_qa_report_payload(self) -> dict[str, Any]:
        self._analyze_html_report_screenshots()
        analysis_by_path = getattr(self, "_screenshot_ocr", {}) or {}
        return build_visual_qa_payload(
            test_run_id=self._task_id,
            analyses_by_path=analysis_by_path,
        )

    def _write_json_artifact(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = path.with_suffix(path.suffix + ".tmp")
        tmp_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(str(tmp_file), str(path))


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------



    def _generate_html_report(self) -> None:
        """Generate HTML test report from the current run data."""
        try:
            config = self._build_html_report_config()
            config_path = self._artifact_dir / "test-results.json"
            self._write_json_artifact(config_path, config)
            visual_qa_path = self._artifact_dir / "visual-qa.json"
            self._write_json_artifact(visual_qa_path, self._build_visual_qa_report_payload())
            output_path = self._artifact_dir / "test-report.html"
            write_html_report(config_path, output_path)
        except Exception as exc:
            print(f"[agent-test] HTML report generation skipped: {exc}")
class _Blocked(Exception):
    """Non-code failure: missing env, game, or automation interface."""


class _Failed(Exception):
    """Real test failure that should be sent back to Developer."""
