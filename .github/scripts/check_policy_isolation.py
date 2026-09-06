#!/usr/bin/env python3
"""对照 base SHA 的 CODEOWNERS，阻止政策文件与功能文件混批（issue #81）。

命中政策 glob 又出现非附带文件 → 退出 1。
读不到清单 / diff / 分类不确定 → 退出 2（fail-closed，不降级）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pr_path_matcher import (
    ACCOMPANYING_PATTERNS,
    PatternError,
    codeowners_has_self_ref,
    load_codeowners_patterns,
    normalize_repo_path,
    parse_codeowners_patterns,
    path_matches_any,
    read_text_file,
)

EXIT_OK = 0
EXIT_MIXED = 1
EXIT_CLOSED = 2


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


def classify_policy_isolation(
    changed_files: list[str],
    base_patterns: list[str],
    *,
    head_codeowners_text: str | None,
) -> tuple[int, str]:
    """返回 (exit_code, message)。"""

    try:
        policy: list[str] = []
        others: list[str] = []
        for path in changed_files:
            if path_matches_any(path, base_patterns):
                policy.append(path)
            else:
                others.append(path)
        extra = [path for path in others if not path_matches_any(path, ACCOMPANYING_PATTERNS)]
    except PatternError as exc:
        return EXIT_CLOSED, f"::error::政策分类不确定: {exc}"

    if any(path == ".github/CODEOWNERS" for path in changed_files):
        if head_codeowners_text is None:
            return EXIT_CLOSED, "::error::无法读取 HEAD CODEOWNERS（自指校验）"
        try:
            head_patterns = parse_codeowners_patterns(head_codeowners_text)
            if not codeowners_has_self_ref(head_patterns):
                return (
                    EXIT_MIXED,
                    "::error::CODEOWNERS 去掉了对自身的覆盖（摘保护），混合检查失败",
                )
        except PatternError as exc:
            return EXIT_CLOSED, f"::error::HEAD CODEOWNERS 分类不确定: {exc}"

    if policy and extra:
        extra_list = ", ".join(extra)
        policy_list = ", ".join(policy)
        return (
            EXIT_MIXED,
            "::error::政策文件与功能文件混在同一 PR："
            f"政策={policy_list}；非附带={extra_list}",
        )
    if policy:
        return EXIT_OK, f"政策隔离通过（政策文件 {len(policy)}，附带 {len(others)}）"
    return EXIT_OK, "政策隔离通过（本 PR 未命中 CODEOWNERS）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True, help="base SHA checkout 根目录")
    parser.add_argument("--changed-files", type=Path, required=True, help="一行一个变更路径")
    parser.add_argument("--head-dir", type=Path, default=Path("."), help="PR HEAD 根目录")
    args = parser.parse_args(argv)

    base_patterns = load_codeowners_patterns(args.base_dir)
    if base_patterns is None:
        print("::error::读不到 base CODEOWNERS，混合检查 fail-closed", file=sys.stderr)
        return EXIT_CLOSED

    changed = read_changed_files(args.changed_files)
    if changed is None:
        print("::error::读不到变更文件清单或路径不确定，混合检查 fail-closed", file=sys.stderr)
        return EXIT_CLOSED

    head_codeowners = read_text_file(args.head_dir / ".github" / "CODEOWNERS")
    code, message = classify_policy_isolation(
        changed,
        base_patterns,
        head_codeowners_text=head_codeowners,
    )
    stream = sys.stderr if code else sys.stdout
    print(message, file=stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
