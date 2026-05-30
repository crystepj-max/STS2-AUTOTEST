"""STS2-Cli-Mod CLI discovery — locate the sts2 executable.

Searches common installation paths, environment variables, and PATH
to find the STS2-Cli-Mod CLI executable. Used by CliModAdapter and
autotest doctor for automatic CLI discovery.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from sts2_autotest.common.logging import get_logger

logger = get_logger("adapters.discovery")


def discover_sts2_cli() -> str | None:
    """Attempt to locate the sts2 CLI executable.

    Search order:
    1. STS2_CLI_PATH environment variable
    2. System PATH (shutil.which)
    3. Common installation directories

    Returns the executable path if found, None otherwise.
    """
    # 1. Environment variable
    env_path = os.environ.get("STS2_CLI_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    # 2. System PATH
    which_path = shutil.which("sts2")
    if which_path:
        return which_path

    # 3. Common installation directories
    for candidate in _common_paths():
        if candidate.is_file():
            return str(candidate)

    return None


def _common_paths() -> list[Path]:
    """Generate common STS2-Cli-Mod installation paths."""
    paths: list[Path] = []

    # User profile level
    try:
        home = Path.home()
        paths.extend([
            home / ".local" / "bin" / "sts2",
            home / ".sts2-cli-mod" / "sts2.exe",
            home / "AppData" / "Local" / "STS2-Cli-Mod" / "sts2.exe",
            home / "AppData" / "Local" / "Programs" / "STS2-Cli-Mod" / "sts2.exe",
        ])
    except RuntimeError:
        pass

    # Steam workshop / game directory level
    for steam_root in _steam_roots():
        game_dir = steam_root / "steamapps" / "common" / "Slay the Spire 2"
        paths.extend([
            game_dir / "sts2.exe",
            game_dir / "sts2",
            game_dir / "Mods" / "STS2-Cli-Mod" / "sts2.exe",
        ])
        # Workshop mod location
        workshop = steam_root / "steamapps" / "workshop" / "content"
        if workshop.exists():
            try:
                for content_dir in workshop.iterdir():
                    mod_dir = content_dir
                    if mod_dir.is_dir():
                        paths.append(mod_dir / "sts2.exe")
            except OSError:
                logger.warning("Cannot read workshop directory: %s", workshop)

    # Program Files level (Windows)
    for pf in [Path("C:/Program Files"), Path("C:/Program Files (x86)")]:
        paths.append(pf / "STS2-Cli-Mod" / "sts2.exe")

    return paths


def _steam_roots() -> list[Path]:
    """Return likely Steam installation root directories."""
    roots: list[Path] = []
    for p in [
        Path("C:/Program Files (x86)/Steam"),
        Path("C:/Program Files/Steam"),
    ]:
        if p.is_dir():
            roots.append(p)

    # Linux Steam
    try:
        linux_steam = Path.home() / ".steam" / "steam"
        if linux_steam.is_dir():
            roots.append(linux_steam)
    except RuntimeError:
        pass

    # macOS Steam
    try:
        macos_steam = Path.home() / "Library" / "Application Support" / "Steam"
        if macos_steam.is_dir():
            roots.append(macos_steam)
    except RuntimeError:
        pass

    # Check Steam library folders via libraryfolders.vdf
    for vdf_candidate in [
        Path("C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf"),
        Path.home() / "Library" / "Application Support" / "Steam" / "steamapps" / "libraryfolders.vdf",
        Path.home() / ".steam" / "steam" / "steamapps" / "libraryfolders.vdf",
    ]:
        if vdf_candidate.exists():
            try:
                content = vdf_candidate.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    line = line.strip()
                    if '"path"' in line:
                        start = line.find('"', line.find('"path"') + 6)
                        end = line.rfind('"')
                        if start > 0 and end > start:
                            lib_path = Path(line[start + 1:end].replace("\\\\", "/"))
                            if lib_path.is_dir():
                                roots.append(lib_path)
            except OSError:
                pass
            break

    return roots


def steam_roots() -> list[Path]:
    """Public alias for _steam_roots()."""
    return _steam_roots()


def find_game_dir(roots: list[Path] | None = None) -> Path | None:
    """Locate the Slay the Spire 2 game directory. Searches each Steam library root."""
    if roots is None:
        roots = _steam_roots()
    for root in roots:
        candidate = root / "steamapps" / "common" / "Slay the Spire 2"
        if candidate.is_dir():
            return candidate
    return None
