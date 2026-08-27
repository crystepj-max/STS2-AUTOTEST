"""issue #81：政策隔离、Canonical、L0 分类与路径匹配。"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_canonical_pr as canonical_mod  # noqa: E402
import check_policy_isolation as isolation_mod  # noqa: E402
import classify_l0 as l0_mod  # noqa: E402
import pr_path_matcher as matcher  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_CODEOWNERS = """\
.github/CODEOWNERS @owner
.github/l0-allowlist.txt @owner
.github/workflows/ @owner
docs/process/quality-gate-governance.md @owner
pyproject.toml @owner
"""

BASE_ALLOWLIST = """\
docs/**
**/*.md
.gitignore
LICENSE
NOTICE
AUTHORS
CHANGELOG*
"""


def _write_changed(path: Path, files: list[str]) -> Path:
    path.write_text("\n".join(files) + "\n", encoding="utf-8")
    return path


def _write_base(root: Path, *, codeowners: str | None = BASE_CODEOWNERS, allowlist: str | None = BASE_ALLOWLIST) -> Path:
    github = root / ".github"
    github.mkdir(parents=True)
    if codeowners is not None:
        (github / "CODEOWNERS").write_text(codeowners, encoding="utf-8")
    if allowlist is not None:
        (github / "l0-allowlist.txt").write_text(allowlist, encoding="utf-8")
    return root


def test_matcher_md_and_docs_globs() -> None:
    assert matcher.path_matches("README.md", "*.md")
    assert matcher.path_matches("docs/process/a.md", "*.md")
    assert matcher.path_matches("docs/process/a.md", "docs/**")
    assert matcher.path_matches("docs/process/a.md", "**/*.md")
    assert not matcher.path_matches("src/foo.py", "*.md")
    assert matcher.path_matches(".github/workflows/ci-pr.yml", ".github/workflows/")
    assert matcher.path_matches("ruff.toml", "**/ruff.toml")
    assert matcher.path_matches("nested/ruff.toml", "**/ruff.toml")
    assert matcher.path_matches("tests/unit/test_policy_pr_gates.py", "tests/unit/test_policy*.py")
    assert matcher.path_matches("tests/unit/test_ci_ruff_baseline.py", "tests/unit/test_ci_*_baseline.py")
    assert not matcher.path_matches("tests/unit/test_orchestrator.py", "tests/unit/test_policy*.py")


def test_allowlist_does_not_match_itself() -> None:
    patterns = matcher.parse_allowlist_patterns(BASE_ALLOWLIST)
    assert not matcher.allowlist_matches_self(patterns)


def test_isolation_policy_only() -> None:
    patterns = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    code, _ = isolation_mod.classify_policy_isolation(
        [".github/workflows/ci-pr.yml"],
        patterns,
        head_codeowners_text=BASE_CODEOWNERS,
    )
    assert code == isolation_mod.EXIT_OK


def test_isolation_policy_plus_docs() -> None:
    patterns = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    code, _ = isolation_mod.classify_policy_isolation(
        [".github/workflows/ci-pr.yml", "docs/user-manual.md", "README.md"],
        patterns,
        head_codeowners_text=BASE_CODEOWNERS,
    )
    assert code == isolation_mod.EXIT_OK


def test_isolation_bootstrap_new_policy_scripts_with_legacy_codeowners() -> None:
    """base CODEOWNERS 尚未登记新脚本时，引导 PR 不得被当成政策+功能混批。"""

    legacy = """\
.github/workflows/ @owner
.github/scripts/check_*_baseline.py @owner
docs/process/quality-gate-governance.md @owner
"""
    patterns = matcher.parse_codeowners_patterns(legacy)
    code, message = isolation_mod.classify_policy_isolation(
        [
            ".github/workflows/ci-pr.yml",
            ".github/CODEOWNERS",
            ".github/l0-allowlist.txt",
            ".github/scripts/check_policy_isolation.py",
            ".github/scripts/check_canonical_pr.py",
            ".github/scripts/classify_l0.py",
            ".github/scripts/pr_path_matcher.py",
            "docs/process/quality-gate-governance.md",
            "tests/unit/test_policy_pr_gates.py",
            "AGENTS.md",
        ],
        patterns,
        head_codeowners_text=BASE_CODEOWNERS,
    )
    assert code == isolation_mod.EXIT_OK, message


def test_isolation_policy_plus_src_fails() -> None:
    patterns = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    code, message = isolation_mod.classify_policy_isolation(
        [".github/workflows/ci-pr.yml", "src/sts2_autotest/cli/main.py"],
        patterns,
        head_codeowners_text=BASE_CODEOWNERS,
    )
    assert code == isolation_mod.EXIT_MIXED
    assert "混在同一 PR" in message


def test_isolation_codeowners_drop_self_ref_fails() -> None:
    patterns = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    stripped = "pyproject.toml @owner\n"
    code, message = isolation_mod.classify_policy_isolation(
        [".github/CODEOWNERS"],
        patterns,
        head_codeowners_text=stripped,
    )
    assert code == isolation_mod.EXIT_MIXED
    assert "摘保护" in message


def test_isolation_missing_codeowners_fail_closed(tmp_path: Path) -> None:
    changed = _write_changed(tmp_path / "changed.txt", ["README.md"])
    rc = isolation_mod.main(
        ["--base-dir", str(tmp_path / "missing"), "--changed-files", str(changed)]
    )
    assert rc == isolation_mod.EXIT_CLOSED


def test_isolation_unreadable_diff_fail_closed(tmp_path: Path) -> None:
    base = _write_base(tmp_path / "base")
    rc = isolation_mod.main(
        [
            "--base-dir",
            str(base),
            "--changed-files",
            str(tmp_path / "no-such.txt"),
        ]
    )
    assert rc == isolation_mod.EXIT_CLOSED


def test_canonical_no_linked_issue_skips() -> None:
    code, message = canonical_mod.evaluate_canonical(
        pr_number=81,
        pr_body="just a patch",
        fetch_issue_body=lambda _n: "should not be called",
    )
    assert code == canonical_mod.EXIT_OK
    assert "跳过" in message


def test_canonical_empty_passes() -> None:
    body = "## Canonical\n\n- Agent:\n- Branch:\n- PR:\n"
    code, _ = canonical_mod.evaluate_canonical(
        pr_number=81,
        pr_body="Closes #80",
        fetch_issue_body=lambda n: body if n == 80 else None,
    )
    assert code == canonical_mod.EXIT_OK


def test_canonical_self_passes() -> None:
    body = "## Canonical\n\n- PR: https://github.com/crystepj-max/STS2-AUTOTEST/pull/85\n"
    code, _ = canonical_mod.evaluate_canonical(
        pr_number=85,
        pr_body="Closes #80",
        fetch_issue_body=lambda n: body if n == 80 else None,
    )
    assert code == canonical_mod.EXIT_OK


def test_canonical_other_pr_fails() -> None:
    body = "## Canonical\n\n- PR: #85\n"
    code, message = canonical_mod.evaluate_canonical(
        pr_number=99,
        pr_body="Fixes #80",
        fetch_issue_body=lambda n: body if n == 80 else None,
    )
    assert code == canonical_mod.EXIT_FAIL
    assert "#85" in message


def test_canonical_unreadable_issue_fail_closed() -> None:
    code, message = canonical_mod.evaluate_canonical(
        pr_number=81,
        pr_body="Part of #79",
        fetch_issue_body=lambda _n: None,
    )
    assert code == canonical_mod.EXIT_CLOSED
    assert "读不到" in message


def test_l0_allowlist_only() -> None:
    allow = matcher.parse_allowlist_patterns(BASE_ALLOWLIST)
    owners = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    is_l0, _ = l0_mod.classify_l0(["README.md", ".gitignore"], allow, owners)
    assert is_l0 is True


def test_l0_src_plus_md_is_not_l0() -> None:
    allow = matcher.parse_allowlist_patterns(BASE_ALLOWLIST)
    owners = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    is_l0, reason = l0_mod.classify_l0(["src/foo.py", "README.md"], allow, owners)
    assert is_l0 is False
    assert "src/foo.py" in reason


def test_l0_governance_md_is_not_l0() -> None:
    allow = matcher.parse_allowlist_patterns(BASE_ALLOWLIST)
    owners = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    is_l0, _ = l0_mod.classify_l0(["docs/process/quality-gate-governance.md"], allow, owners)
    assert is_l0 is False


def test_l0_reads_base_allowlist_not_head(tmp_path: Path) -> None:
    base = _write_base(tmp_path / "base")
    head_allow = tmp_path / "head" / ".github"
    head_allow.mkdir(parents=True)
    (head_allow / "l0-allowlist.txt").write_text("src/**\n**/*.md\n", encoding="utf-8")
    changed = _write_changed(tmp_path / "changed.txt", ["src/sts2_autotest/cli/main.py"])
    output = tmp_path / "out.txt"
    rc = l0_mod.main(
        [
            "--base-dir",
            str(base),
            "--changed-files",
            str(changed),
            "--github-output",
            str(output),
        ]
    )
    assert rc == 0
    assert "l0=false" in output.read_text(encoding="utf-8")


def test_l0_only_allowlist_file_is_not_l0() -> None:
    allow = matcher.parse_allowlist_patterns(BASE_ALLOWLIST)
    owners = matcher.parse_codeowners_patterns(BASE_CODEOWNERS)
    is_l0, _ = l0_mod.classify_l0([".github/l0-allowlist.txt"], allow, owners)
    assert is_l0 is False


def test_l0_unreadable_upgrades_to_full(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    changed = _write_changed(tmp_path / "changed.txt", ["README.md"])
    output = tmp_path / "out.txt"
    rc = l0_mod.main(
        [
            "--base-dir",
            str(base),
            "--changed-files",
            str(changed),
            "--github-output",
            str(output),
        ]
    )
    assert rc == 0
    assert "l0=false" in output.read_text(encoding="utf-8")


def test_invariants_real_codeowners_lists_allowlist() -> None:
    text = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    patterns = matcher.parse_codeowners_patterns(text)
    assert matcher.path_matches_any(".github/l0-allowlist.txt", patterns)
    assert matcher.codeowners_has_self_ref(patterns)


def test_invariants_real_allowlist_excludes_self_and_forbidden() -> None:
    text = (REPO_ROOT / ".github" / "l0-allowlist.txt").read_text(encoding="utf-8")
    patterns = matcher.parse_allowlist_patterns(text)
    assert not matcher.allowlist_matches_self(patterns)
    assert matcher.allowlist_hits_forbidden(patterns) == []


def test_ci_pr_has_no_paths_ignore_and_keeps_job_name() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "ci-pr.yml").read_text(encoding="utf-8")
    assert "paths-ignore:" not in text
    workflow = yaml.safe_load(text)
    assert workflow["jobs"]["validation"]["name"] == "PR Check Summary"


def test_ci_pr_enforce_recognizes_l0() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "ci-pr.yml").read_text(encoding="utf-8")
    assert "steps.classify.outputs.l0" in text
    assert "check_policy_isolation.py" in text
    assert "classify_l0.py" in text
    assert "check_canonical_pr.py" in text


def test_canonical_cli_self(tmp_path: Path) -> None:
    rc = canonical_mod.main(
        [
            "--pr-number",
            "85",
            "--pr-body",
            "Closes #80",
            "--issue-body",
            "80::## Canonical\n- PR: #85\n",
        ]
    )
    assert rc == 0


def test_isolation_cli_policy_plus_src(tmp_path: Path) -> None:
    base = _write_base(tmp_path / "base")
    head = _write_base(tmp_path / "head")
    changed = _write_changed(
        tmp_path / "changed.txt",
        [".github/workflows/ci-pr.yml", "src/sts2_autotest/cli/main.py"],
    )
    rc = isolation_mod.main(
        [
            "--base-dir",
            str(base),
            "--head-dir",
            str(head),
            "--changed-files",
            str(changed),
        ]
    )
    assert rc == isolation_mod.EXIT_MIXED
