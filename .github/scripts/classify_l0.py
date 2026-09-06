#!/usr/bin/env python3
"""L0 直通道分类（issue #81）。

全部 changed files 命中 **base** 白名单，且没有任何文件命中 **base** CODEOWNERS → L0。
读不到白名单 / CODEOWNERS / diff，或分类不确定 → 升级全套（退出 0，不把 Summary 判红）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pr_path_matcher import (
    PatternError,
    load_allowlist_patterns,
    load_codeowners_patterns,
    normalize_repo_path,
    path_matches_any,
    read_text_file,
)


def read_changed_files(path: Path) -> list[str] | None:
    text = read_text_file(path)
    if text is None:
        return None
    files: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        normalized = normalize_repo_path(raw)
        if normalized is None:
            return None
        files.append(normalized)
    return files


def classify_l0(
    changed_files: list[str],
    allowlist: list[str],
    codeowners: list[str],
) -> tuple[bool, str]:
    """返回 (is_l0, reason)。异常由调用方捕获后升级全套。"""

    if not changed_files:
        return False, "变更列表为空，升级全套"
    for path in changed_files:
        if path_matches_any(path, codeowners):
            return False, f"{path} 命中 base CODEOWNERS，非 L0"
        if not path_matches_any(path, allowlist):
            return False, f"{path} 未命中 base L0 白名单，非 L0"
    return True, f"全部 {len(changed_files)} 个文件命中白名单且未命中 CODEOWNERS"


def write_github_output(path: Path | None, is_l0: bool) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"l0={'true' if is_l0 else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--changed-files", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args(argv)

    allowlist = load_allowlist_patterns(args.base_dir)
    codeowners = load_codeowners_patterns(args.base_dir)
    changed = read_changed_files(args.changed_files)

    if allowlist is None or codeowners is None or changed is None:
        print("L0 分类读不到白名单/CODEOWNERS/diff，升级全套")
        write_github_output(args.github_output, False)
        return 0

    try:
        is_l0, reason = classify_l0(changed, allowlist, codeowners)
    except PatternError as exc:
        print(f"L0 分类不确定（{exc}），升级全套")
        write_github_output(args.github_output, False)
        return 0

    print(("L0" if is_l0 else "非 L0") + f"：{reason}")
    write_github_output(args.github_output, is_l0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
