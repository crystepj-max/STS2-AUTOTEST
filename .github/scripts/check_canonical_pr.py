#!/usr/bin/env python3
"""Canonical PR 检查（issue #81）。

没链 Issue → 跳过。
链了且 Canonical PR 空或就是本 PR → 通过。
已填且不是本 PR → 失败。
链了但读不到 Issue 正文 → fail-closed。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CLOSED = 2

_KEYWORD_HASH = re.compile(
    r"(?is)(?:^|[^A-Za-z])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|part[ \t]+of)"
    r"[ \t]+(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#(\d+)"
)
_KEYWORD_URL = re.compile(
    r"(?is)(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|part[ \t]+of)[ \t]+"
    r"https://github\.com/[^/\s]+/[^/\s]+/issues/(\d+)"
)
_PR_NUMBER = re.compile(r"(?:pull/|#)?(\d+)\s*$")
_CANONICAL_PR_LINE = re.compile(r"(?im)^[ \t]*[-*][ \t]*PR[ \t]*:[ \t]*(.*)$")


def parse_linked_issue_numbers(pr_body: str) -> list[int]:
    found: list[int] = []
    for match in _KEYWORD_HASH.finditer(pr_body or ""):
        found.append(int(match.group(1)))
    for match in _KEYWORD_URL.finditer(pr_body or ""):
        found.append(int(match.group(1)))
    # 去重且保序
    seen: set[int] = set()
    ordered: list[int] = []
    for number in found:
        if number not in seen:
            seen.add(number)
            ordered.append(number)
    return ordered


def parse_canonical_pr_number(issue_body: str) -> int | None:
    """从 Issue 正文 Canonical 段读取 PR 号；空则 None。无法解析已填内容时抛 ValueError。"""

    lines = (issue_body or "").splitlines()
    in_canonical = False
    raw_value: str | None = None
    for line in lines:
        heading = line.strip().lower()
        if heading.startswith("## "):
            title = heading[3:].strip()
            if title == "canonical":
                in_canonical = True
                continue
            if in_canonical:
                break
        if not in_canonical:
            continue
        match = _CANONICAL_PR_LINE.match(line)
        if match:
            raw_value = match.group(1).strip()
            break
    if raw_value is None:
        return None
    # 去掉 HTML 注释残留
    raw_value = re.sub(r"<!--.*?-->", "", raw_value).strip()
    if not raw_value or raw_value.lower() in {"n/a", "na", "tbd", "todo", "-", "—"}:
        return None
    match = re.search(r"github\.com/[^/\s]+/[^/\s]+/pull/(\d+)", raw_value)
    if match:
        return int(match.group(1))
    match = re.search(r"#(\d+)\s*$", raw_value)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"(\d+)", raw_value)
    if match:
        return int(match.group(1))
    raise ValueError(f"无法解析 Canonical PR: {raw_value!r}")


def evaluate_canonical(
    *,
    pr_number: int,
    pr_body: str,
    fetch_issue_body: Callable[[int], str | None],
) -> tuple[int, str]:
    linked = parse_linked_issue_numbers(pr_body)
    if not linked:
        return EXIT_OK, "Canonical 检查跳过（PR 未链 Issue）"

    for issue_number in linked:
        body = fetch_issue_body(issue_number)
        if body is None:
            return (
                EXIT_CLOSED,
                f"::error::读不到 Issue #{issue_number} 正文，Canonical 检查 fail-closed",
            )
        try:
            canonical = parse_canonical_pr_number(body)
        except ValueError as exc:
            return EXIT_CLOSED, f"::error::Issue #{issue_number} {exc}"
        if canonical is None:
            continue
        if canonical != pr_number:
            return (
                EXIT_FAIL,
                f"::error::Issue #{issue_number} 的 Canonical PR 是 #{canonical}，"
                f"不是本 PR #{pr_number}",
            )
    return EXIT_OK, f"Canonical 检查通过（链了 {linked}）"


def github_issue_fetcher(repo: str, token: str | None) -> Callable[[int], str | None]:
    def fetch(issue_number: int) -> str | None:
        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "sts2-autotest-canonical-check",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None
        body = payload.get("body")
        if body is None:
            return ""
        if not isinstance(body, str):
            return None
        return body

    return fetch


def _decode_pr_body(raw: str | None, as_json: bool) -> str:
    if not raw:
        return ""
    if not as_json:
        return raw
    loaded = json.loads(raw)
    if loaded is None:
        return ""
    if not isinstance(loaded, str):
        raise ValueError("PR_BODY JSON 不是字符串")
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--pr-body-json",
        default=os.environ.get("PR_BODY_JSON", ""),
        help="toJSON(github.event.pull_request.body) 的字符串",
    )
    parser.add_argument("--pr-body", default="", help="测试用原始正文（非 JSON）")
    parser.add_argument(
        "--issue-body",
        action="append",
        default=[],
        metavar="N::TEXT",
        help="测试注入：Issue 号与正文，格式 12::markdown",
    )
    args = parser.parse_args(argv)

    try:
        pr_body = args.pr_body or _decode_pr_body(args.pr_body_json, as_json=True)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"::error::无法解析 PR body: {exc}", file=sys.stderr)
        return EXIT_CLOSED

    injected: dict[int, str | None] = {}
    for item in args.issue_body:
        if "::" not in item:
            print("::error::--issue-body 格式应为 N::TEXT", file=sys.stderr)
            return EXIT_CLOSED
        number_text, body = item.split("::", 1)
        injected[int(number_text)] = body

    if injected:
        fetch = injected.get
    else:
        if not args.repo:
            print("::error::缺少 --repo / GITHUB_REPOSITORY", file=sys.stderr)
            return EXIT_CLOSED
        fetch = github_issue_fetcher(args.repo, os.environ.get("GITHUB_TOKEN"))

    code, message = evaluate_canonical(
        pr_number=args.pr_number,
        pr_body=pr_body,
        fetch_issue_body=fetch,
    )
    stream = sys.stderr if code else sys.stdout
    print(message, file=stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
