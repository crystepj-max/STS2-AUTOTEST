"""CI Deploy Gawain：Godot 无头 export 预热、瞬时错误重试与产物校验。

针对实测失败模式（run 32539377847）：
  savepack 已 DONE，随后 EditorSettings::get_singleton() 报错，
  ``--export-pack`` 以 exit -1 结束（MSB3073）。属间歇性，同机稍后可成功。

策略：
1. 发布前 headless 预热，初始化 EditorSettings
2. 对 EditorSettings / MSB3073 / export-pack exit -1 类日志最多重试一次
3. 全程写入 deploy 日志，供 workflow 失败时上传
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_ARTIFACTS: tuple[str, ...] = (
    "Gawain.dll",
    "Gawain.json",
    "Gawain.pdb",
    "gawain.pck",
)


def resolve_godot_bin(explicit: str | None = None) -> Path | None:
    """解析可执行的 Godot Mono 路径。"""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("GODOT_PATH", "").strip()
    if env:
        candidates.append(env)
    candidates.extend(
        [
            "/Applications/Godot_mono.app/Contents/MacOS/Godot",
            "/Applications/Godot.app/Contents/MacOS/Godot",
            str(Path.home() / "Applications/Godot.app/Contents/MacOS/Godot"),
            str(Path.home() / "Applications/Godot_mono.app/Contents/MacOS/Godot"),
        ]
    )
    seen: set[str] = set()
    for raw in candidates:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        path = Path(raw).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def resolve_game_paths(
    game_dir: str | None = None,
) -> tuple[Path, Path]:
    """返回 (Sts2InstallDir, ModsPath)。"""
    raw = (game_dir or os.environ.get("STS2_GAME_DIR") or "").strip()
    if not raw:
        raw = str(
            Path.home()
            / "Library/Application Support/Steam/steamapps/common/Slay the Spire 2"
        )
    install = Path(raw).expanduser()
    mods = install / "SlayTheSpire2.app/Contents/MacOS/mods"
    return install, mods


def is_transient_godot_export_error(log_text: str) -> bool:
    """判定是否为可重试的 Godot 无头 export 瞬时失败。"""
    if not log_text or "EditorSettings" not in log_text:
        return False
    return (
        "MSB3073" in log_text
        or "export-pack" in log_text
        or "EditorSettings::get_singleton" in log_text
    )


def warmup_godot(
    godot: Path,
    project_dir: Path,
    *,
    log_path: Path,
    timeout: float = 120.0,
) -> int:
    """Headless 预热：拉起编辑器路径一次以初始化 EditorSettings。"""
    cmd = [
        str(godot),
        "--headless",
        "--path",
        str(project_dir),
        "--quit-after",
        "1",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## warmup: {' '.join(cmd)}\n")
        handle.flush()
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(project_dir),
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            handle.write("## warmup: TIMEOUT\n")
            return 124
        handle.write(f"## warmup: exit={completed.returncode}\n")
        return completed.returncode


def publish_gawain(
    project_dir: Path,
    *,
    godot: Path,
    game_dir: Path,
    mods_dir: Path,
    log_path: Path,
    timeout: float = 600.0,
) -> int:
    """执行 ``dotnet publish`` 并追加日志。"""
    cmd = [
        "dotnet",
        "publish",
        "Gawain.csproj",
        "-c",
        "Release",
        f"-p:Sts2InstallDir={game_dir}",
        f"-p:ModsPath={mods_dir}/",
        f"-p:GodotPath={godot}",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## publish: {' '.join(cmd)}\n")
        handle.flush()
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(project_dir),
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            handle.write("## publish: TIMEOUT\n")
            return 124
        handle.write(f"## publish: exit={completed.returncode}\n")
        return completed.returncode


def verify_deploy_artifacts(
    mods_dir: Path,
    *,
    mod_name: str = "Gawain",
    artifacts: tuple[str, ...] = DEFAULT_ARTIFACTS,
) -> list[str]:
    """返回缺失产物文件名列表（空=齐全）。"""
    target = mods_dir / mod_name
    missing: list[str] = []
    for name in artifacts:
        if not (target / name).is_file():
            missing.append(name)
    return missing


def deploy_with_retry(
    project_dir: Path,
    *,
    godot: Path,
    game_dir: Path,
    mods_dir: Path,
    log_path: Path,
    max_attempts: int = 2,
    sleep_seconds: float = 2.0,
) -> int:
    """预热 + publish；瞬时 Godot 错误时再试一次。"""
    log_path.write_text("", encoding="utf-8")
    warmup_rc = warmup_godot(godot, project_dir, log_path=log_path)
    if warmup_rc not in (0,):
        # 预热失败不阻断：部分环境 --quit-after 仍可能非零，但已初始化 settings
        print(
            f"::warning::Godot warmup exit={warmup_rc}; continuing to publish",
            file=sys.stderr,
        )

    last_rc = 1
    for attempt in range(1, max_attempts + 1):
        print(f"::group::dotnet publish attempt {attempt}/{max_attempts}")
        last_rc = publish_gawain(
            project_dir,
            godot=godot,
            game_dir=game_dir,
            mods_dir=mods_dir,
            log_path=log_path,
        )
        print("::endgroup::")
        if last_rc == 0:
            return 0
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if attempt < max_attempts and is_transient_godot_export_error(log_text):
            print(
                "::warning::Transient Godot export failure detected; "
                f"retrying after {sleep_seconds}s",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
            warmup_godot(godot, project_dir, log_path=log_path)
            continue
        break
    return last_rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy Gawain Mod with Godot warmup/retry")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("gawain-src"),
        help="Gawain 源码目录（含 Gawain.csproj）",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=Path("deploy-gawain.log"),
        help="部署日志路径（失败时由 workflow 上传）",
    )
    parser.add_argument("--godot", default="", help="Godot 可执行文件路径")
    parser.add_argument("--game-dir", default="", help="STS2 安装目录")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args(argv)

    project_dir = args.project_dir.expanduser().resolve()
    if not (project_dir / "Gawain.csproj").is_file():
        print(f"::error::Gawain.csproj not found under {project_dir}", file=sys.stderr)
        return 2

    godot = resolve_godot_bin(args.godot or None)
    if godot is None:
        print(
            "::error::Godot executable not found (set GODOT_PATH or install Godot Mono).",
            file=sys.stderr,
        )
        return 2

    game_dir, mods_dir = resolve_game_paths(args.game_dir or None)
    if not game_dir.is_dir():
        print(f"::error::STS2 game dir not found: {game_dir}", file=sys.stderr)
        return 2

    print(f"Godot: {godot}")
    print(f"Game:  {game_dir}")
    print(f"Mods:  {mods_dir}")

    rc = deploy_with_retry(
        project_dir,
        godot=godot,
        game_dir=game_dir,
        mods_dir=mods_dir,
        log_path=args.log_path.expanduser(),
        max_attempts=max(1, args.max_attempts),
    )
    if rc != 0:
        print(f"::error::dotnet publish failed (exit {rc}); see {args.log_path}", file=sys.stderr)
        return rc

    missing = verify_deploy_artifacts(mods_dir)
    if missing:
        print(
            f"::error::deploy artifact missing under {mods_dir / 'Gawain'}: {missing}",
            file=sys.stderr,
        )
        return 1

    print(f"Gawain deployed and verified: {mods_dir / 'Gawain'}/ ({', '.join(DEFAULT_ARTIFACTS)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
