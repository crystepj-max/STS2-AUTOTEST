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
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import asyncio
import json
from sts2_autotest.adapters.agent import AgentAdapter

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

_IS_MACOS = platform.system() == "Darwin"
_IS_WINDOWS = platform.system() == "Windows"
_IS_LINUX = platform.system() == "Linux"

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
    _EXCLUDE_PARTS = {".git", ".claude", ".agent-runs", "bin", "obj", "node_modules", ".godot"}
    candidates: list[Path] = []
    for pattern in ["**/bin/Release", "**/bin/Debug"]:
        for d in project_path.glob(pattern):
            if not d.is_dir():
                continue
            if _EXCLUDE_PARTS & set(d.relative_to(project_path).parts):
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
        """Capture full-screen screenshot and save to screenshot dir.

        Uses mss to grab the primary monitor. Saves as PNG.
        Returns the relative evidence path (for the report).
        Returns empty string on failure (non-blocking).
        """
        try:
            import mss
            path = self._screenshot_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
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
        rc, out = _run_command("dotnet build", ["dotnet", "build", str(target), "--no-restore"], build_log)
        if rc != 0:
            raise _Failed(f"dotnet build failed. See build.log.\n{out[:500]}")

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

        # Launch via steam:// protocol
        if _IS_MACOS:
            subprocess.Popen(["open", f"steam://rungameid/{self._steam_app_id}"])
        elif _IS_WINDOWS:
            os.startfile(f"steam://rungameid/{self._steam_app_id}")  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", f"steam://rungameid/{self._steam_app_id}"])
        launch_log.write_text(f"Started steam://rungameid/{self._steam_app_id}\n", encoding="utf-8")
        self._add("Launch Game", "PASSED", "launch.log")

    # ------------------------------------------------------------------
    # Step 6: Game smoke test
    # ------------------------------------------------------------------


    def _navigate_to_first_combat(self, agent: AgentAdapter) -> dict:
        """Navigate from MAIN_MENU to first combat. Raises _Failed on failure."""
        nav_steps = [
            ("open_character_select", {}),
            ("select_character", {"option_index": 0}),
            ("embark", {}),
            ("choose_map_node", {"option_index": 0}),
        ]
        for action_name, params in nav_steps:
            result = asyncio.run(agent.act(action_name, params))
            if result.status != "success":
                raise _Failed(f"Navigation failed at '{action_name}': {result.detail}")
            asyncio.run(agent.wait_until_actionable(timeout=15))
        state = asyncio.run(agent.get_state())
        state_dict = dict(state) if hasattr(state, "__dict__") else {}
        if state_dict.get("screen") != "COMBAT":
            raise _Failed(f"Expected COMBAT screen, got {state_dict.get('screen')}")
        return state_dict

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
        before_dict = dict(before) if hasattr(before, "__dict__") else {}
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
        after_dict = dict(after) if hasattr(after, "__dict__") else {}
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
            card_index = card.get("index", 0)
            card_result = self._verify_card_and_screenshot(agent, card, card_index, 0)
            self._card_results.append(card_result)

        passed_count = sum(1 for r in self._card_results if r["status"] == "OK")
        failed = [r for r in self._card_results if r["status"] != "OK"]

        if failed:
            detail = "; ".join(
                f"{r['name']}({r['card_id']}): {r['error']}" for r in failed
            )
            raise _Failed(f"Card verification: {len(failed)} failed ({detail})")

        self._add("Card Smoke Test", "PASSED",
                  f"Verified {passed_count} cards; "
                  f"screenshots in automation/autotest/output/{self._task_id}/screenshots/")

        # --- 6e: Clean up ---
        asyncio.run(agent.act("abandon_run"))
        self._add("Abandon Run", "PASSED", "")

        # --- 6f: Scan for raw keys in final state ---
        final_state = asyncio.run(agent.get_state())
        final_dict = dict(final_state) if hasattr(final_state, "__dict__") else {}
        final_json = json.dumps(final_dict)
        raw_patterns = ["GAWAIN_", "MISSING", "missing localization", "KeyNotFound"]
        for pattern in raw_patterns:
            if pattern.lower() in final_json.lower():
                raise _Failed(f"Raw key found after combat: {pattern}")

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


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------


class _Blocked(Exception):
    """Non-code failure: missing env, game, or automation interface."""


class _Failed(Exception):
    """Real test failure that should be sent back to Developer."""
