# 自然语言测试流水线主计划

> **面向代理执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项执行本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**目标：** 将自然语言测试规格完整接入 STS2-AUTOTEST 主链路，并把首场战斗冒烟场景推进到真实可执行闭环。

**架构：** 面向用户的输入是 `specs/cases/*.md` 与 `specs/suites/*.md`，框架先审查规格，再编译为 `TestSpec` / `SuiteSpec`，最后生成 `pytest + Fluent DSL` 测试文件交给 pytest 执行。CLI 保持 `review` / `compile` / `run` 分层命令，同时由 `run --all` 负责编排全流程。

**技术栈：** Python 3.11、dataclasses、argparse、pytest、现有 `TestOrchestrator`、`ActionDescriptor`、`Fluent DSL`、`CliModAdapter`

---

## 当前状态

以下基线已经完成，可作为后续任务起点：

- 已存在规格模型与解析/审查/生成器雏形：
  - `src/sts2_autotest/common/spec_models.py`
  - `src/sts2_autotest/core/markdown_parser.py`
  - `src/sts2_autotest/core/spec_reviewer.py`
  - `src/sts2_autotest/core/code_generator.py`
- 已存在默认规格目录与首批样板：
  - `specs/cases/`
  - `specs/suites/`
- CLI 已支持：
  - `autotest review`
  - `autotest compile`
  - `autotest run --all` 调度 review/compile/pytest
- 首批 DSL 原语已接回：
  - `return_to_menu`
  - `choose_game_mode`
  - `start_new_run`
  - `select_character`
  - `embark`
  - `choose_event`
  - `advance_dialogue`
  - `choose_map_node`
  - `skip_card_reward`
- 当前验证状态：
  - 相关单元测试通过
  - 生成测试可以被 pytest 收集
  - 尚未打通真实游戏环境中的首场战斗通过

---

### Task 1: 收敛规格资产与文档入口

**Files:**
- Create: `docs/natural-language-testing/index.md`
- Modify: `docs/natural-language-testing/2026-05-18-natural-language-test-pipeline-overview.md`
- Modify: `docs/user-manual.md`
- Test: `tests/unit/test_default_specs.py`

- [ ] **Step 1: 写失败测试，明确默认规格目录必须稳定存在**

```python
from pathlib import Path


def test_default_spec_directories_are_present() -> None:
    assert Path("specs/cases").is_dir()
    assert Path("specs/suites").is_dir()
```

- [ ] **Step 2: 运行测试确认当前行为**

Run: `python -m pytest tests/unit/test_default_specs.py -q --basetemp .pytest-tmp-task1`
Expected: PASS；如果失败，先修复缺失目录或样板规格。

- [ ] **Step 3: 新增规格文档索引**

```md
# 自然语言测试文档索引

- [总体方案](./2026-05-18-natural-language-test-pipeline-overview.md)
- [实现计划](../superpowers/plans/2026-05-18-natural-language-test-pipeline-master-plan.md)
```

- [ ] **Step 4: 在用户手册中补充统一入口**

```md
## 自然语言测试工作流

1. 编写 `specs/cases/*.md` 或 `specs/suites/*.md`
2. 运行 `autotest review`
3. 查看审查结果与修订建议
4. 运行 `autotest compile`
5. 运行 `autotest run --all`
```

- [ ] **Step 5: 回跑测试**

Run: `python -m pytest tests/unit/test_default_specs.py -q --basetemp .pytest-tmp-task1`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add docs/natural-language-testing/index.md docs/natural-language-testing/2026-05-18-natural-language-test-pipeline-overview.md docs/user-manual.md tests/unit/test_default_specs.py
git commit -m "docs: add natural language testing entry points"
```

### Task 2: 固化审查产物输出格式

**Files:**
- Modify: `src/sts2_autotest/cli/main.py`
- Modify: `src/sts2_autotest/core/spec_reviewer.py`
- Modify: `src/sts2_autotest/common/spec_models.py`
- Test: `tests/unit/test_cli_spec_commands.py`
- Test: `tests/unit/test_spec_reviewer.py`

- [ ] **Step 1: 先写失败测试，要求 review 可输出报告文件**

```python
from argparse import Namespace
from pathlib import Path

from sts2_autotest.cli.main import review_cmd


def test_review_cmd_writes_report_file(tmp_path: Path) -> None:
    out = tmp_path / "review.txt"
    args = Namespace(command="review", spec_dir=None, project=None, output=str(out))
    rc = review_cmd(args)
    assert rc in (0, 1)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Review complete" in text
```

- [ ] **Step 2: 再写失败测试，要求 revised draft 有结构化摘要**

```python
from sts2_autotest.common.spec_models import TestSpec
from sts2_autotest.core.spec_reviewer import SpecReviewer


def test_revised_draft_contains_changes_summary() -> None:
    reviewer = SpecReviewer()
    spec = TestSpec(id="TC-001", title="模糊规格", steps=["适当选择奖励"])
    report = reviewer.review(spec)
    draft = reviewer.generate_revised_draft(spec, report)
    assert draft.changes_summary
```

- [ ] **Step 3: 运行测试确认失败点**

Run: `python -m pytest tests/unit/test_cli_spec_commands.py tests/unit/test_spec_reviewer.py -q --basetemp .pytest-tmp-task2`
Expected: 至少一个测试失败，暴露输出格式或 draft 内容不足。

- [ ] **Step 4: 最小实现输出规范**

```python
report_path = getattr(args, "output", None)
if report_path:
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(output + "\n")
```

```python
return RevisedDraft(
    spec_id=spec.id,
    original_path=spec.source_path,
    markdown_content=markdown,
    changes_summary=changes,
)
```

- [ ] **Step 5: 回跑测试**

Run: `python -m pytest tests/unit/test_cli_spec_commands.py tests/unit/test_spec_reviewer.py -q --basetemp .pytest-tmp-task2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sts2_autotest/cli/main.py src/sts2_autotest/core/spec_reviewer.py src/sts2_autotest/common/spec_models.py tests/unit/test_cli_spec_commands.py tests/unit/test_spec_reviewer.py
git commit -m "feat: stabilize review outputs and revised drafts"
```

### Task 3: 扩展首战关键步骤到真正可执行的战斗策略 DSL

**Files:**
- Modify: `src/sts2_autotest/dsl/assertions.py`
- Modify: `src/sts2_autotest/dsl/__init__.py`
- Modify: `src/sts2_autotest/core/code_generator.py`
- Test: `tests/unit/test_code_generator.py`
- Test: `tests/unit/test_fluent_api.py`
- Test: `tests/unit/test_e2e_first_battle.py`

- [ ] **Step 1: 先写失败测试，要求生成器支持“战斗循环”而不是只会 `end_turn()`**

```python
from sts2_autotest.common.spec_models import TestSpec
from sts2_autotest.core.code_generator import CodeGenerator


def test_generate_case_maps_basic_combat_policy() -> None:
    spec = TestSpec(
        id="TC-COMBAT-POLICY",
        title="基础战斗策略",
        steps=["进入首次战斗", "按基础策略完成战斗"],
    )
    code = CodeGenerator().generate_case_test(spec)
    assert "combat_basic_policy()" in code
```

- [ ] **Step 2: 再写失败测试，要求 DSL 暴露新原语**

```python
from sts2_autotest.dsl import combat_basic_policy


def test_combat_basic_policy_descriptor() -> None:
    descriptor = combat_basic_policy()
    assert descriptor.action_type == "combat_basic_policy"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_code_generator.py tests/unit/test_fluent_api.py -q --basetemp .pytest-tmp-task3`
Expected: FAIL，提示 `combat_basic_policy` 尚不存在。

- [ ] **Step 4: 最小实现首战策略原语与映射**

```python
def combat_basic_policy() -> ActionDescriptor:
    return ActionDescriptor(action_type="combat_basic_policy")
```

```python
if "基础策略完成战斗" in step or "首次战斗" in step:
    return "combat_basic_policy()"
```

- [ ] **Step 5: 回跑测试**

Run: `python -m pytest tests/unit/test_code_generator.py tests/unit/test_fluent_api.py tests/unit/test_e2e_first_battle.py -q --basetemp .pytest-tmp-task3`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sts2_autotest/dsl/assertions.py src/sts2_autotest/dsl/__init__.py src/sts2_autotest/core/code_generator.py tests/unit/test_code_generator.py tests/unit/test_fluent_api.py tests/unit/test_e2e_first_battle.py
git commit -m "feat: add first battle combat policy dsl primitive"
```

### Task 4: 让 compile 结果更贴近真实 suite 执行

**Files:**
- Modify: `src/sts2_autotest/core/code_generator.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_code_generator.py`
- Test: `tests/unit/test_cli_spec_commands.py`
- Test: `tests/integration/test_spec_pipeline_e2e.py`

- [ ] **Step 1: 先写失败测试，要求 suite 编译产物带清晰的 case 来源**

```python
from sts2_autotest.common.spec_models import SuiteSpec, TestSpec
from sts2_autotest.core.code_generator import CodeGenerator


def test_suite_generation_keeps_case_ids_visible() -> None:
    suite = SuiteSpec(id="SUITE-001", title="Smoke", includes=["TC-001"])
    code = CodeGenerator().generate_suite_test(
        suite,
        {"TC-001": TestSpec(id="TC-001", title="One", steps=["返回主菜单"])},
    )
    assert "TC-001" in code
```

- [ ] **Step 2: 写失败测试，要求 `compile` 在默认输出目录可重复运行**

```python
from argparse import Namespace

from sts2_autotest.cli.main import compile_cmd


def test_compile_cmd_is_idempotent() -> None:
    args = Namespace(command="compile", spec_dir=None, output_dir="tests/generated", project=None)
    assert compile_cmd(args) == 0
    assert compile_cmd(args) == 0
```

- [ ] **Step 3: 运行测试确认问题**

Run: `python -m pytest tests/unit/test_code_generator.py tests/unit/test_cli_spec_commands.py tests/integration/test_spec_pipeline_e2e.py -q --basetemp .pytest-tmp-task4`
Expected: 若存在重复输出或 suite 可读性问题，则测试失败。

- [ ] **Step 4: 最小修复编译幂等性与 suite 可读性**

```python
path.mkdir(parents=True, exist_ok=True)
```

```python
parts.append(f"    # Included case: {case_id}")
```

- [ ] **Step 5: 回跑测试**

Run: `python -m pytest tests/unit/test_code_generator.py tests/unit/test_cli_spec_commands.py tests/integration/test_spec_pipeline_e2e.py -q --basetemp .pytest-tmp-task4`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sts2_autotest/core/code_generator.py src/sts2_autotest/cli/main.py tests/unit/test_code_generator.py tests/unit/test_cli_spec_commands.py tests/integration/test_spec_pipeline_e2e.py
git commit -m "feat: improve suite generation and compile idempotence"
```

### Task 5: 真实游戏环境下打通首战 smoke

**Files:**
- Modify: `specs/cases/TC-FINISH-FIRST-BATTLE.md`
- Modify: `src/sts2_autotest/core/code_generator.py`
- Modify: `tests/e2e_first_battle.py`
- Test: `tests/generated/test_tc_finish_first_battle.py`
- Test: `tests/generated/test_suite_first_battle_smoke.py`

- [ ] **Step 1: 明确真实可执行规格，把过于笼统的步骤改细**

```md
## When
1. 选择地图节点 (2, 1)
2. 进入首次战斗
3. 按基础策略完成战斗
4. 跳过卡牌奖励
```

- [ ] **Step 2: 先跑编译，确认生成测试反映新规格**

Run: `python -m pytest tests/unit/test_code_generator.py -q --basetemp .pytest-tmp-task5a`
Expected: PASS

Run: `python -c "from argparse import Namespace; from sts2_autotest.cli.main import compile_cmd; raise SystemExit(compile_cmd(Namespace(command='compile', spec_dir=None, output_dir='tests/generated', project=None)))"`
Expected: 生成更新后的 `tests/generated/test_tc_finish_first_battle.py`

- [ ] **Step 3: 在真实环境执行首战 smoke**

Run: `python -m pytest tests/generated/test_tc_finish_first_battle.py tests/generated/test_suite_first_battle_smoke.py -v --basetemp .pytest-tmp-task5b`
Expected: 在真实游戏环境中通过；如果失败，记录失败屏幕、动作和能力缺口。

- [ ] **Step 4: 用旧脚本只做对照，不再作为正式入口**

```python
"""迁移参考脚本，不再作为框架正式执行入口。"""
```

- [ ] **Step 5: Commit**

```bash
git add specs/cases/TC-FINISH-FIRST-BATTLE.md src/sts2_autotest/core/code_generator.py tests/e2e_first_battle.py tests/generated
git commit -m "feat: execute first battle smoke through generated tests"
```

### Task 6: 最终验证与收尾

**Files:**
- Modify: `docs/user-manual.md`
- Modify: `docs/natural-language-testing/2026-05-18-natural-language-test-pipeline-overview.md`
- Test: `tests/unit/test_default_specs.py`
- Test: `tests/generated/`

- [ ] **Step 1: 跑目标单元回归**

Run: `python -m pytest tests/unit/test_code_generator.py tests/unit/test_fluent_api.py tests/unit/test_default_specs.py tests/unit/test_cli_spec_commands.py tests/unit/test_markdown_parser.py tests/unit/test_spec_reviewer.py -q --basetemp .pytest-tmp-task6a`
Expected: PASS

- [ ] **Step 2: 跑生成测试收集或执行**

Run: `python -m pytest tests/generated --collect-only -q --basetemp .pytest-tmp-task6b`
Expected: 成功收集首批 case 与 suite

- [ ] **Step 3: 跑类型和分层检查**

Run: `mypy src/sts2_autotest --strict`
Expected: `Success: no issues found`

Run: `lint-imports`
Expected: contracts kept

- [ ] **Step 4: 更新文档中的“当前进度”段**

```md
- 已打通 `spec -> compile -> pytest collect`
- 已补回首批默认规格
- 已补首批首战 DSL 原语
- 待完成真实游戏环境首战 smoke 通过
```

- [ ] **Step 5: Commit**

```bash
git add docs/user-manual.md docs/natural-language-testing/2026-05-18-natural-language-test-pipeline-overview.md
git commit -m "docs: finalize natural language pipeline progress"
```

## 自检

### Spec coverage

- 规格分层：已覆盖 `case + suite`
- 命令分层：已覆盖 `review + compile + run + run --all`
- 主链路约束：已覆盖生成测试必须走 Fluent DSL
- 首批闭环：已覆盖默认规格、生成器、首战 smoke
- 真实执行缺口：已单独拆成 Task 5

### Placeholder scan

- 本计划未使用 `TODO` / `TBD` / “类似 Task N”
- 每个任务都给出了文件、命令和最小代码片段

### Type consistency

- 统一使用现有命名：
  - `TestSpec`
  - `SuiteSpec`
  - `SpecReviewer`
  - `CodeGenerator`
  - `compile_cmd`
  - `review_cmd`
