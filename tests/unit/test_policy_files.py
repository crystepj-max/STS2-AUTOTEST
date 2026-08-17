"""单元测试：检查政策文件存在性与 CODEOWNERS 覆盖（issue-21 反向验证）。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 政策文件清单（决策 01/02/03/04/05 确定；新增文件必须同步登记到这里）
POLICY_PATHS = [
    ".github/workflows/ci-pr.yml",
    ".github/scripts/check_ruff_baseline.py",
    ".github/scripts/check_mypy_baseline.py",
    ".github/scripts/check_pytest_baseline.py",
    ".github/pytest-baseline.json",
    ".github/requirements-lint.txt",
    ".github/mypy-policy.ini",
    "pyproject.toml",
    "uv.lock",
]

# CODEOWNERS 中必须出现的受保护路径模式
CODEOWNER_PATTERNS = [
    ".github/workflows/",
    ".github/scripts/check_*_baseline.py",
    ".github/pytest-baseline.json",
    ".github/requirements-lint.txt",
    ".github/mypy-policy.ini",
    "pyproject.toml",
    "**/ruff.toml",
    "**/.ruff.toml",
    "uv.lock",
    "docs/process/quality-gate-governance.md",
]


def test_policy_files_exist() -> None:
    """政策文件必须存在；删除任一文件都应被测试抓住。"""

    missing = [p for p in POLICY_PATHS if not (REPO_ROOT / p).is_file()]
    assert missing == [], f"政策文件缺失: {missing}"


def test_codeowners_exists() -> None:
    codeowners = REPO_ROOT / ".github/CODEOWNERS"
    assert codeowners.is_file(), ".github/CODEOWNERS 不存在"


def test_codeowners_covers_all_policy_patterns() -> None:
    """CODEOWNERS 必须覆盖全部政策路径模式，防止保护被悄然移除。"""

    codeowners = (REPO_ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    uncovered = [
        pattern
        for pattern in CODEOWNER_PATTERNS
        if pattern not in codeowners
    ]
    assert uncovered == [], f"CODEOWNERS 未覆盖: {uncovered}"


def test_policy_change_template_exists() -> None:
    template = REPO_ROOT / ".github/PULL_REQUEST_TEMPLATE" / "policy_change.md"
    assert template.is_file(), "独立政策变更 PR 模板缺失"
# 普通功能变更（探针）
