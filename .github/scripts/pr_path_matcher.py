#!/usr/bin/env python3
"""gitignore / CODEOWNERS 风格路径匹配（issue #81）。

混合检查与 L0 共用本匹配器。只使用标准库，不解析 diff 内容。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_CODEOWNERS_SELF = ".github/CODEOWNERS"
_ALLOWLIST_RELATIVE = ".github/l0-allowlist.txt"

# 政策 PR 允许附带的路径（硬编码，避免被 PR 内容改写）。
ACCOMPANYING_PATTERNS: tuple[str, ...] = (
    "docs/**",
    "*.md",
    "tests/unit/test_policy*.py",
    "tests/unit/test_ci_*_baseline.py",
)

# L0 白名单不得覆盖这些路径（不变量）。
FORBIDDEN_ALLOWLIST_PATHS: tuple[str, ...] = (
    "src/sts2_autotest/__init__.py",
    ".github/workflows/ci-pr.yml",
    "pyproject.toml",
    "uv.lock",
)


class PatternError(ValueError):
    """glob 无法确定地编译。"""


def normalize_repo_path(raw: str) -> str | None:
    """把变更路径收成仓库相对 POSIX。含 `..` 或空串则视为不确定。"""

    text = raw.strip().replace("\\", "/")
    if not text or text in {".", "./"}:
        return None
    while text.startswith("./"):
        text = text[2:]
    if text.startswith("/") or text.startswith("../") or "/../" in f"/{text}/":
        return None
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in parts) or not parts:
        return None
    return "/".join(parts)


def parse_codeowners_patterns(text: str) -> list[str]:
    """解析 CODEOWNERS：每行第一个字段为 glob，忽略注释与空行。"""

    patterns: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.split()[0])
    return patterns


def parse_allowlist_patterns(text: str) -> list[str]:
    """解析 L0 白名单：一行一个 glob，忽略注释与空行。"""

    patterns: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.split()[0])
    return patterns


def read_text_file(path: Path) -> str | None:
    """读文本；缺失或无法解码时返回 None（调用方决定 fail-closed / 升级）。"""

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_codeowners_patterns(root: Path) -> list[str] | None:
    text = read_text_file(root / _CODEOWNERS_SELF)
    if text is None:
        return None
    return parse_codeowners_patterns(text)


def load_allowlist_patterns(root: Path) -> list[str] | None:
    text = read_text_file(root / _ALLOWLIST_RELATIVE)
    if text is None:
        return None
    return parse_allowlist_patterns(text)


def compile_path_pattern(pattern: str) -> re.Pattern[str]:
    """把 gitignore/CODEOWNERS glob 编成整路径正则。"""

    source = pattern.strip().replace("\\", "/")
    if not source or source.startswith("#"):
        raise PatternError(f"empty pattern: {pattern!r}")
    if "\x00" in source:
        raise PatternError(f"invalid pattern: {pattern!r}")

    directory = source.endswith("/")
    if directory:
        source = source[:-1]
        if not source:
            raise PatternError(f"invalid pattern: {pattern!r}")

    anchored = source.startswith("/")
    if anchored:
        source = source[1:]
    # 含 slash 的模式相对仓库根；不含 slash 时匹配任意目录（gitignore）。
    match_in_any_dir = ("/" not in source) and not anchored

    regex = ["^"]
    if match_in_any_dir:
        regex.append("(?:.*/)?")

    i = 0
    length = len(source)
    while i < length:
        if source.startswith("**/", i):
            regex.append("(?:.*/)?")
            i += 3
            continue
        if source.startswith("**", i) and (i + 2 == length):
            regex.append(".*")
            i += 2
            continue
        char = source[i]
        if char == "*":
            regex.append("[^/]*")
            i += 1
            continue
        if char == "?":
            regex.append("[^/]")
            i += 1
            continue
        regex.append(re.escape(char))
        i += 1

    if directory:
        regex.append("(?:/.*)?")
    regex.append("$")
    return re.compile("".join(regex))


def path_matches(path: str, pattern: str) -> bool:
    normalized = normalize_repo_path(path)
    if normalized is None:
        raise PatternError(f"uncertain path: {path!r}")
    return compile_path_pattern(pattern).fullmatch(normalized) is not None


def path_matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


def codeowners_has_self_ref(patterns: Iterable[str]) -> bool:
    return any(path_matches(_CODEOWNERS_SELF, pattern) for pattern in patterns)


def allowlist_matches_self(patterns: Iterable[str]) -> bool:
    return any(path_matches(_ALLOWLIST_RELATIVE, pattern) for pattern in patterns)


def allowlist_hits_forbidden(patterns: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for forbidden in FORBIDDEN_ALLOWLIST_PATHS:
        if any(path_matches(forbidden, pattern) for pattern in patterns):
            hits.append(forbidden)
    return hits
