# 自然语言测试流水线实施计划

> **面向代理执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项执行本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 为 STS2-AUTOTEST 建立首条自然语言测试闭环：Markdown 用例/用例集规格 -> 审查报告 + 修订建议稿 -> 编译为 `TestSpec` / `SuiteSpec` -> 生成 `pytest + fixture + Fluent DSL` 测试 -> 通过 `autotest review|compile|run` 和 `autotest run --all` 完成统一调度。

**架构：** 以 Markdown 规格作为面向用户的权威输入，先编译为 `common/` 中的不可变共享模型，再由 `core/` 提供解析、审查、编译服务，最后由 CLI 负责编排发现与 pytest 执行。生成后的测试必须只走 `pytest fixture -> TestOrchestrator -> ActionDescriptor -> Fluent DSL` 主链路，禁止再生成直接操作 adapter 的旁路脚本。

**技术栈：** Python 3.11、dataclasses、argparse、pytest、现有 `Fluent DSL`、现有 pytest 插件、窄格式行解析 Markdown。

---

### 任务 1：新增共享规格模型与工作区路径配置

**文件：**
- 新建：`src/sts2_autotest/common/spec_models.py`
- 修改：`src/sts2_autotest/common/__init__.py`
- 测试：`tests/unit/test_spec_models.py`

- [ ] **步骤 1：先写失败测试**

```python
from sts2_autotest.common.spec_models import (
    ExecutionMode,
    IssueCategory,
    ReviewIssue,
    ReviewReport,
    RevisedDraft,
    SuiteSpec,
    TestSpec,
    WorkspacePaths,
)


def test_test_spec_defaults() -> None:
    spec = TestSpec(id="TC-001", title="Minimal")
    assert spec.priority == "P3"
    assert spec.tags == []
    assert spec.givens == []
    assert spec.steps == []
    assert spec.assertions == []


def test_suite_spec_defaults() -> None:
    suite = SuiteSpec(id="SUITE-001", title="Smoke")
    assert suite.execution_mode is ExecutionMode.SEQUENTIAL_SHARED_SESSION
    assert suite.includes == []


def test_review_report_summary_counts() -> None:
    issues = [
        ReviewIssue(IssueCategory.AMBIGUITY, "When step 1", "模糊", "明确动作"),
        ReviewIssue(IssueCategory.MISSING, "Start State", "缺失", "补充稳定状态"),
    ]
    report = ReviewReport(spec_id="TC-001", issues=issues)
    assert report.summary["total"] == 2
    assert report.summary["ambiguity"] == 1
    assert report.summary["missing"] == 1


def test_workspace_paths_defaults() -> None:
    paths = WorkspacePaths()
    assert paths.case_dir == "specs/cases"
    assert paths.suite_dir == "specs/suites"
    assert paths.generated_test_dir == "tests/generated"
```

- [ ] **步骤 2：运行测试，确认先失败**

运行：

```powershell
python -m pytest tests/unit/test_spec_models.py -q
```

预期：因为 `sts2_autotest.common.spec_models` 不存在而失败。

- [ ] **步骤 3：实现共享模型**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExecutionMode(StrEnum):
    SEQUENTIAL_SHARED_SESSION = "sequential_shared_session"
    ISOLATED_CASES = "isolated_cases"


class IssueCategory(StrEnum):
    AMBIGUITY = "ambiguity"
    MISSING = "missing"
    UNIMPLEMENTABLE = "unimplementable"
    CAPABILITY_GAP = "capability_gap"


@dataclass(frozen=True)
class TestSpec:
    id: str
    title: str
    tags: list[str] = field(default_factory=list)
    priority: str = "P3"
    start_state: list[str] = field(default_factory=list)
    end_state: list[str] = field(default_factory=list)
    givens: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    fallback_policies: list[str] = field(default_factory=list)
    capability_requirements: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SuiteSpec:
    id: str
    title: str
    tags: list[str] = field(default_factory=list)
    priority: str = "P3"
    goal: str = ""
    execution_mode: ExecutionMode = ExecutionMode.SEQUENTIAL_SHARED_SESSION
    includes: list[str] = field(default_factory=list)
    suite_assertions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewIssue:
    category: IssueCategory
    location: str
    description: str
    suggestion: str


@dataclass(frozen=True)
class ReviewReport:
    spec_id: str
    issues: list[ReviewIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def summary(self) -> dict[str, int]:
        counts = {category.value: 0 for category in IssueCategory}
        for issue in self.issues:
            counts[issue.category.value] += 1
        counts["total"] = len(self.issues)
        return counts


@dataclass(frozen=True)
class RevisedDraft:
    spec_id: str
    markdown: str


@dataclass(frozen=True)
class WorkspacePaths:
    case_dir: str = "specs/cases"
    suite_dir: str = "specs/suites"
    generated_test_dir: str = "tests/generated"
    review_output_dir: str = "tests/generated/review"
```

- [ ] **步骤 4：从 `common` 导出这些模型**

```python
from sts2_autotest.common.spec_models import (
    ExecutionMode,
    IssueCategory,
    ReviewIssue,
    ReviewReport,
    RevisedDraft,
    SuiteSpec,
    TestSpec,
    WorkspacePaths,
)
```

- [ ] **步骤 5：重新运行测试，确认转绿**

运行：

```powershell
python -m pytest tests/unit/test_spec_models.py -q
```

预期：`4 passed`。

- [ ] **步骤 6：提交**

```powershell
git add src/sts2_autotest/common/spec_models.py src/sts2_autotest/common/__init__.py tests/unit/test_spec_models.py
git commit -m "feat: add natural language spec models"
```

### 任务 2：新增 Markdown 规格模板与窄格式解析器

**文件：**
- 新建：`specs/cases/TC-PREPARE-NEW-RUN.md`
- 新建：`specs/cases/TC-RESOLVE-NEOW.md`
- 新建：`specs/cases/TC-FINISH-FIRST-BATTLE.md`
- 新建：`specs/suites/SUITE-FIRST-BATTLE-SMOKE.md`
- 新建：`src/sts2_autotest/core/spec_parser.py`
- 测试：`tests/unit/test_spec_parser.py`

- [ ] **步骤 1：先写 case / suite 解析测试**

```python
from pathlib import Path

from sts2_autotest.core.spec_parser import parse_case_spec, parse_suite_spec


def test_parse_prepare_new_run_case(tmp_path: Path) -> None:
    spec_file = tmp_path / "TC-PREPARE-NEW-RUN.md"
    spec_file.write_text(
        "# TC-PREPARE-NEW-RUN 进入新局地图\n\n"
        "## Metadata\n"
        "- id: TC-PREPARE-NEW-RUN\n"
        "- level: case\n"
        "- tags: smoke, bootstrap\n"
        "- priority: P0\n\n"
        "## Start State\n"
        "- 任意可恢复状态\n\n"
        "## End State\n"
        "- 到达地图\n\n"
        "## Given\n"
        "- 已安装 STS2-Cli-Mod\n\n"
        "## When\n"
        "1. 启动 Steam\n"
        "2. 启动游戏\n\n"
        "## Then\n"
        "- 不应出现 crash\n",
        encoding="utf-8",
    )
    spec = parse_case_spec(spec_file)
    assert spec.id == "TC-PREPARE-NEW-RUN"
    assert spec.tags == ["smoke", "bootstrap"]
    assert spec.steps == ["启动 Steam", "启动游戏"]


def test_parse_suite_spec(tmp_path: Path) -> None:
    spec_file = tmp_path / "SUITE-FIRST-BATTLE-SMOKE.md"
    spec_file.write_text(
        "# SUITE-FIRST-BATTLE-SMOKE 首次战斗冒烟\n\n"
        "## Metadata\n"
        "- id: SUITE-FIRST-BATTLE-SMOKE\n"
        "- level: suite\n"
        "- tags: smoke, first_battle\n\n"
        "## Goal\n"
        "- 验证主链路\n\n"
        "## Mode\n"
        "- execution: sequential_shared_session\n\n"
        "## Includes\n"
        "1. TC-PREPARE-NEW-RUN\n"
        "2. TC-RESOLVE-NEOW\n",
        encoding="utf-8",
    )
    suite = parse_suite_spec(spec_file)
    assert suite.includes == ["TC-PREPARE-NEW-RUN", "TC-RESOLVE-NEOW"]
```

- [ ] **步骤 2：运行解析测试，确认先失败**

运行：

```powershell
python -m pytest tests/unit/test_spec_parser.py -q
```

预期：因为 `sts2_autotest.core.spec_parser` 不存在而失败。

- [ ] **步骤 3：补齐首批真实 Markdown 规格**

```md
# TC-RESOLVE-NEOW 首个节点处理

## Metadata
- id: TC-RESOLVE-NEOW
- level: case
- tags: smoke, event
- priority: P0

## Start State
- 当前位于 Neow 初始事件，且存在可执行事件动作

## End State
- 当前位于地图界面，且首个可达节点可选

## Given
- 已进入新 run
- 事件为初始祝福/开局事件

## When
1. 若存在 `choose_event`，则选择 `choice 0`
2. 若存在 `advance_dialogue`，则持续推进直到地图出现

## Then
- 不应出现 crash
- 最终应位于地图界面
- 应能识别至少一个可达节点
```

- [ ] **步骤 4：实现窄格式解析器**

```python
from __future__ import annotations

from pathlib import Path

from sts2_autotest.common.spec_models import ExecutionMode, SuiteSpec, TestSpec


def parse_case_spec(path: Path) -> TestSpec:
    sections = _parse_sections(path.read_text(encoding="utf-8"))
    metadata = _parse_kv_list(sections["Metadata"])
    return TestSpec(
        id=metadata["id"],
        title=_parse_title(path.read_text(encoding="utf-8"))[1],
        tags=_split_csv(metadata.get("tags", "")),
        priority=metadata.get("priority", "P3"),
        start_state=_parse_bullets(sections.get("Start State", "")),
        end_state=_parse_bullets(sections.get("End State", "")),
        givens=_parse_bullets(sections.get("Given", "")),
        steps=_parse_ordered(sections.get("When", "")),
        assertions=_parse_bullets(sections.get("Then", "")),
    )


def parse_suite_spec(path: Path) -> SuiteSpec:
    sections = _parse_sections(path.read_text(encoding="utf-8"))
    metadata = _parse_kv_list(sections["Metadata"])
    mode = _parse_kv_list(sections.get("Mode", "")).get(
        "execution", ExecutionMode.SEQUENTIAL_SHARED_SESSION.value
    )
    return SuiteSpec(
        id=metadata["id"],
        title=_parse_title(path.read_text(encoding="utf-8"))[1],
        tags=_split_csv(metadata.get("tags", "")),
        priority=metadata.get("priority", "P3"),
        goal=_first_bullet(sections.get("Goal", "")),
        execution_mode=ExecutionMode(mode),
        includes=_parse_ordered(sections.get("Includes", "")),
        suite_assertions=_parse_bullets(sections.get("Then", "")),
    )
```

- [ ] **步骤 5：重新运行解析测试，确认转绿**

运行：

```powershell
python -m pytest tests/unit/test_spec_parser.py -q
```

预期：`2 passed`。

- [ ] **步骤 6：提交**

```powershell
git add specs/cases specs/suites src/sts2_autotest/core/spec_parser.py tests/unit/test_spec_parser.py
git commit -m "feat: add markdown test spec parser"
```

### 任务 3：新增规格审查器与修订建议稿生成器

**文件：**
- 新建：`src/sts2_autotest/core/spec_review.py`
- 测试：`tests/unit/test_spec_review.py`

- [ ] **步骤 1：先写失败测试**

```python
from sts2_autotest.common.spec_models import TestSpec
from sts2_autotest.core.spec_review import review_case_spec


def test_review_flags_ambiguity() -> None:
    spec = TestSpec(
        id="TC-001",
        title="模糊动作",
        steps=["适当选择奖励"],
        assertions=["正常完成战斗"],
    )
    report, draft = review_case_spec(spec)
    assert not report.passed
    assert any(issue.category.value == "ambiguity" for issue in report.issues)
    assert "适当选择" not in draft.markdown


def test_review_flags_missing_start_and_end_state() -> None:
    spec = TestSpec(id="TC-002", title="缺起止状态")
    report, _draft = review_case_spec(spec)
    assert any(issue.category.value == "missing" for issue in report.issues)
```

- [ ] **步骤 2：运行测试，确认先失败**

运行：

```powershell
python -m pytest tests/unit/test_spec_review.py -q
```

预期：因为 `sts2_autotest.core.spec_review` 不存在而失败。

- [ ] **步骤 3：实现审查器**

```python
from __future__ import annotations

from sts2_autotest.common.spec_models import (
    IssueCategory,
    ReviewIssue,
    ReviewReport,
    RevisedDraft,
    TestSpec,
)

AMBIGUOUS_TOKENS = ("适当", "正常", "尽快", "处理当前")


def review_case_spec(spec: TestSpec) -> tuple[ReviewReport, RevisedDraft]:
    issues: list[ReviewIssue] = []
    if not spec.start_state:
        issues.append(ReviewIssue(IssueCategory.MISSING, "Start State", "缺少起始稳定状态", "补充 Start State"))
    if not spec.end_state:
        issues.append(ReviewIssue(IssueCategory.MISSING, "End State", "缺少结束稳定状态", "补充 End State"))
    for index, step in enumerate(spec.steps, start=1):
        if any(token in step for token in AMBIGUOUS_TOKENS):
            issues.append(
                ReviewIssue(
                    IssueCategory.AMBIGUITY,
                    f"When step {index}",
                    f"步骤包含模糊表达: {step}",
                    _rewrite_step(step),
                )
            )
    draft = RevisedDraft(spec_id=spec.id, markdown=_render_revised_markdown(spec, issues))
    return ReviewReport(spec_id=spec.id, issues=issues), draft
```

- [ ] **步骤 4：重新运行测试，确认转绿**

运行：

```powershell
python -m pytest tests/unit/test_spec_review.py -q
```

预期：`2 passed`。

- [ ] **步骤 5：提交**

```powershell
git add src/sts2_autotest/core/spec_review.py tests/unit/test_spec_review.py
git commit -m "feat: add spec reviewer and revised draft generation"
```

### 任务 4：新增面向 Fluent DSL 的测试代码生成器

**文件：**
- 新建：`src/sts2_autotest/core/spec_codegen.py`
- 测试：`tests/unit/test_spec_codegen.py`

- [ ] **步骤 1：先写失败测试**

```python
from sts2_autotest.common.spec_models import TestSpec
from sts2_autotest.core.spec_codegen import generate_pytest_case


def test_generate_pytest_case_uses_fluent_dsl() -> None:
    spec = TestSpec(
        id="TC-PREPARE-NEW-RUN",
        title="进入新局地图",
        steps=["启动 Steam", "启动游戏", "选择 Ironclad"],
        assertions=["不应出现 crash", "最终应位于地图界面"],
    )
    code = generate_pytest_case(spec)
    assert "define(" in code
    assert "autotest" in code
    assert "ActionDescriptor" not in code
    assert "CliModAdapter" not in code
```

- [ ] **步骤 2：运行测试，确认先失败**

运行：

```powershell
python -m pytest tests/unit/test_spec_codegen.py -q
```

预期：因为 `sts2_autotest.core.spec_codegen` 不存在而失败。

- [ ] **步骤 3：实现生成器**

```python
from __future__ import annotations

from textwrap import dedent

from sts2_autotest.common.spec_models import TestSpec


STEP_MAP = {
    "启动 Steam": "ensure_steam_running()",
    "启动游戏": "ensure_game_running()",
    "选择标准模式": 'select_mode("standard")',
    "选择 Ironclad": 'select_character("IRONCLAD")',
}

ASSERTION_MAP = {
    "不应出现 crash": "no_crash_detected()",
    "最终应位于地图界面": "game_reached_state(GameScreen.MAP)",
}


def generate_pytest_case(spec: TestSpec) -> str:
    action_lines = [STEP_MAP[step] for step in spec.steps if step in STEP_MAP]
    assertion_lines = [ASSERTION_MAP[item] for item in spec.assertions if item in ASSERTION_MAP]
    joined_actions = ",\n            ".join(action_lines)
    joined_assertions = ",\n            ".join(assertion_lines)
    return dedent(
        f'''\
        from sts2_autotest.common.state import GameScreen
        from sts2_autotest.dsl import define, ensure_game_running, ensure_steam_running, select_character, select_mode
        from sts2_autotest.dsl import game_reached_state, no_crash_detected


        def test_{spec.id.lower().replace("-", "_")}(autotest, _session_loop):
            result = (
                define("{spec.id}", autotest, _session_loop)
                .setup(
                    {joined_actions}
                )
                .assert_that(
                    {joined_assertions}
                )
            )
            assert result.passed, result.failures
        '''
    )
```

- [ ] **步骤 4：重新运行测试，确认转绿**

运行：

```powershell
python -m pytest tests/unit/test_spec_codegen.py -q
```

预期：`1 passed`。

- [ ] **步骤 5：提交**

```powershell
git add src/sts2_autotest/core/spec_codegen.py tests/unit/test_spec_codegen.py
git commit -m "feat: add fluent dsl pytest generator"
```

### 任务 5：新增 `review` / `compile` 命令，并把 `run --all` 改成分层调度

**文件：**
- 修改：`src/sts2_autotest/cli/main.py`
- 测试：`tests/unit/test_cli.py`

- [ ] **步骤 1：先写 CLI 失败测试**

```python
from sts2_autotest.cli.main import _create_parser


def test_parser_has_review_and_compile_commands() -> None:
    parser = _create_parser()
    args = parser.parse_args(["review"])
    assert args.command == "review"
    args = parser.parse_args(["compile"])
    assert args.command == "compile"


def test_run_all_keeps_all_flag() -> None:
    parser = _create_parser()
    args = parser.parse_args(["run", "--all"])
    assert args.command == "run"
    assert args.all is True
```

- [ ] **步骤 2：运行测试，确认先失败**

运行：

```powershell
python -m pytest tests/unit/test_cli.py -q
```

预期：因为 `review` 和 `compile` 子命令不存在而失败。

- [ ] **步骤 3：为解析器补上新命令**

```python
review = sub.add_parser("review", help="Review Markdown test specs")
review.add_argument("--all", action="store_true", help="Review all discovered specs")
review.add_argument("--case", help="Review a single case spec")
review.add_argument("--suite", help="Review a single suite spec")

compile_cmd = sub.add_parser("compile", help="Compile reviewed specs to pytest")
compile_cmd.add_argument("--all", action="store_true", help="Compile all approved specs")
compile_cmd.add_argument("--case", help="Compile a single case spec")
compile_cmd.add_argument("--suite", help="Compile a single suite spec")
```

- [ ] **步骤 4：补齐命令处理函数与分层 `run --all`**

```python
def review_cmd(args: Any) -> int:
    # 发现规格 -> 解析 -> 审查 -> 写出 review report 与 revised draft
    return 0


def compile_cmd(args: Any) -> int:
    # 读取通过审查的规格 -> 生成 pytest 文件到 tests/generated
    return 0


def run_cmd(args: Any) -> int:
    if args.all:
        if review_cmd(_Namespace(all=True)) != 0:
            return 1
        if compile_cmd(_Namespace(all=True)) != 0:
            return 1
        return _run_pytest_generated([])
    return _run_pytest_generated(_resolve_selected_targets(args))
```

- [ ] **步骤 5：重新运行 CLI 测试，确认转绿**

运行：

```powershell
python -m pytest tests/unit/test_cli.py -q
```

预期：新增解析测试和现有 CLI 测试一起通过。

- [ ] **步骤 6：提交**

```powershell
git add src/sts2_autotest/cli/main.py tests/unit/test_cli.py
git commit -m "feat: split review compile and run commands"
```

### 任务 6：补齐生成测试执行链路，打通首个闭环

**文件：**
- 修改：`src/sts2_autotest/dsl/__init__.py`
- 修改：`src/sts2_autotest/dsl/assertions.py`
- 新建：`tests/generated/.gitkeep`
- 测试：`tests/unit/test_generated_pipeline.py`

- [ ] **步骤 1：先写失败的流水线冒烟测试**

```python
from pathlib import Path

from sts2_autotest.cli.main import compile_cmd


def test_compile_writes_generated_pytest_file(tmp_path: Path) -> None:
    exit_code = compile_cmd(type("Args", (), {"all": True, "case": None, "suite": None})())
    generated = Path("tests/generated/test_tc_prepare_new_run.py")
    assert exit_code == 0
    assert generated.exists()
```

- [ ] **步骤 2：运行测试，确认先失败**

运行：

```powershell
python -m pytest tests/unit/test_generated_pipeline.py -q
```

预期：因为尚未写出生成文件而失败。

- [ ] **步骤 3：导出或新增生成测试依赖的 DSL 原语**

```python
from sts2_autotest.dsl.bootstrap import ensure_game_running, ensure_steam_running, select_character, select_mode
from sts2_autotest.dsl.assertions import game_reached_state, no_crash_detected
```

- [ ] **步骤 4：把编译输出接到 `tests/generated/` 并执行验证**

运行：

```powershell
python -m pytest tests/unit/test_spec_models.py tests/unit/test_spec_parser.py tests/unit/test_spec_review.py tests/unit/test_spec_codegen.py tests/unit/test_generated_pipeline.py -q
python -m pytest tests/generated -q
mypy src/sts2_autotest --strict
lint-imports
```

预期：

- 单元测试通过
- 生成后的测试可被 pytest 发现
- `mypy --strict` 输出 `Success: no issues found`
- `lint-imports` 输出 contracts kept

- [ ] **步骤 5：提交**

```powershell
git add src/sts2_autotest/dsl/__init__.py src/sts2_autotest/dsl/assertions.py tests/generated/.gitkeep tests/unit/test_generated_pipeline.py
git commit -m "feat: close first natural language test pipeline loop"
```

### 任务 7：最终验证与旧旁路脚本退场策略

**文件：**
- 修改：`docs/user-manual.md`
- 修改：`docs/natural-language-testing/2026-05-15-natural-language-test-pipeline-design.md`
- 读取：`tests/e2e_first_battle.py`

- [ ] **步骤 1：把新工作流写进文档**

```md
## 自然语言测试工作流

1. 编写 `specs/cases/*.md` 或 `specs/suites/*.md`
2. 运行 `autotest review --all`
3. 查看 `review report` 与 `revised draft`
4. 运行 `autotest compile --all`
5. 运行 `autotest run --all`
```

- [ ] **步骤 2：明确旧脚本处置方式**

二选一，并写入文档：

- 保留 `tests/e2e_first_battle.py` 仅作为迁移参考，并加模块级警告说明
- 或在生成后的 suite 覆盖同一路径后删除它

- [ ] **步骤 3：执行最终项目验证**

运行：

```powershell
python -m pytest tests/unit -q
python -m pytest tests/generated -q
mypy src/sts2_autotest --strict
lint-imports
```

预期：

- 目标测试全部通过
- mypy 通过
- import-linter 通过

- [ ] **步骤 4：提交**

```powershell
git add docs/user-manual.md docs/natural-language-testing/2026-05-15-natural-language-test-pipeline-design.md tests/e2e_first_battle.py
git commit -m "docs: describe natural language testing workflow"
```
