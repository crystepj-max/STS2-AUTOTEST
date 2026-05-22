# 用例注册机制技术方案

**日期:** 2026-05-15
**状态:** 草案

## 问题

`Orchestrator.execute_case(case_id: str)` 收到一个字符串，但没有从 `case_id` 到用户测试逻辑的映射。当前实现只是一个 probe 桩。

框架已有全部底层能力（`ActionDescriptor`、`FluentBuilder`、`execute_action()`/`execute_action_sequence()`），缺失的是用例注册/发现机制。

## 核心原则

- **框架管编排，用例管流程** — 注册机制只做"名字→逻辑"映射，不内置任何游戏知识
- **尊重复杂控制流** — 不强制用例拆成扁平 `ActionDescriptor` 列表，支持回调函数形态
- **CLI 和 pytest 两条路径都能消费** — 同一个注册表，`autotest run` 和 `pytest` 都能用
- **最小增量** — 不动已有模块的公开接口，只在 `core/` 加一个轻量模块

## 框架 vs 用例边界

```
框架（STS2-AUTOTEST）                    用例
├── 适配器抽象                            ├── 引导逻辑
├── 状态机验证                            ├── 角色/地图/战斗策略
├── 编排生命周期                          ├── 出牌/结束回合时机
├── 错误分类 + 恢复策略                   ├── 奖励跳过/选择逻辑
├── 证据采集                              └── 断言
├── 进度持久化 + 断点续跑
├── CLI + pytest 集成
├── 配置管理
└── CaseRegistry（用例注册）              ← 新增
```

## 核心模型

```python
# core/case_registry.py

from typing import Awaitable, Callable
from dataclasses import dataclass, field

from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator

# 程序化用例签名
CaseRunner = Callable[[TestOrchestrator], Awaitable[TestResult]]


@dataclass
class CaseDefinition:
    case_id: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    actions: list[ActionDescriptor] = field(default_factory=list)  # 简单线性
    runner: CaseRunner | None = None                               # 复杂有状态
```

- **`actions`** — 静态 `ActionDescriptor` 序列，适合"选人→出航→出牌"这种步骤明确的场景
- **`runner`** — 异步回调函数，框架把 `orchestrator` 注入，用例内爱写多复杂的逻辑都行（循环、条件、轮询等待、动态决策）

## 注册表

```python
class CaseRegistry:
    _cases: dict[str, CaseDefinition] = {}

    @classmethod
    def register(cls, case: CaseDefinition) -> None:
        """注册一个用例。重复 case_id 报错。"""

    @classmethod
    def resolve(cls, case_id: str) -> CaseDefinition:
        """按 ID 查找。找不到抛 STS2Error。"""

    @classmethod
    def list_all(cls) -> list[str]:
        """列出全部已注册 case_id。"""

    @classmethod
    def list_by_tags(cls, tags: set[str]) -> list[str]:
        """按 tag 筛选。交集语义。"""

    @classmethod
    def clear(cls) -> None:
        """清空注册表（测试用）。"""
```

## 两种用例形态

### 简单线性用例

适合步骤明确、无分支的验证场景：

```python
CaseRegistry.register(CaseDefinition(
    case_id="char-select",
    description="验证角色选择画面可达",
    tags=["smoke", "menu"],
    actions=[
        ActionDescriptor(action_type="new_run"),
        ActionDescriptor(action_type="select_character",
                         params={"character_id": "IRONCLAD"}),
    ],
))
```

### 程序化用例

适合 `e2e_first_battle.py` 这种需要根据游戏状态动态决策的流程：

```python
async def first_battle_runner(orch: TestOrchestrator) -> TestResult:
    """端到端冒烟测试：从开局跑到第一场战斗结束。"""
    await bootstrap_to_fresh_start(orch)
    await select_character(orch, "IRONCLAD")
    await embark_and_navigate(orch)
    await combat_loop(orch, max_turns=15)
    await handle_rewards(orch)
    return TestResult(case_id="first-battle", status="pass")

CaseRegistry.register(CaseDefinition(
    case_id="first-battle",
    description="端到端冒烟测试：从开局跑到第一场战斗结束",
    tags=["smoke", "e2e", "combat"],
    runner=first_battle_runner,
))
```

用例函数签名是 `async (orchestrator) -> TestResult`。`orchestrator` 暴露全部底层能力（`adapter`、`execute_action()`、`execute_action_sequence()`），用例可以自由组合。

## Orchestrator 改动

仅改 `execute_case()`——从桩变为查询 + 分发：

```python
async def execute_case(self, case_id: str) -> TestResult:
    from sts2_autotest.core.case_registry import CaseRegistry

    definition = CaseRegistry.resolve(case_id)
    # 找不到 → 抛 STS2Error，由 _handle_failure 统一处理

    if definition.runner is not None:
        return await definition.runner(self)

    if definition.actions:
        await self.execute_action_sequence(definition.actions)
        return TestResult(case_id=case_id, status="pass")

    return TestResult(case_id=case_id, status="fail",
                      detail="Case has no actions or runner")
```

`run_all()` / `run_cases()` / `run_failed()` 无需改动——它们已经遍历 `case_ids` 逐个调 `execute_case()`。

## CLI 路径

```
autotest run --cases first-battle
  → CLI run_cmd()
    → _run_orchestrator(["first-battle"], ...)
      → orch.run_all(["first-battle"])
        → orch.execute_case("first-battle")
          → CaseRegistry.resolve("first-battle")
          → definition.runner(orch)    # 用例逻辑在此执行
```

### --all 发现机制

`--all` 时需扫描用户声明的用例目录并 import 其中的模块，触发 `CaseRegistry.register()`：

```yaml
# sts2-autotest.yaml
case_directories:
  - tests/cases
```

CLI 在 `run_cmd` 中：
1. 读取配置中的 `case_directories`
2. 遍历目录中的 `.py` 文件，动态 import
3. 这些文件在顶层执行 `CaseRegistry.register(...)`
4. 调用 `CaseRegistry.list_all()` 获取全部 case_id
5. 传给 `_run_orchestrator(all_ids, ...)`

### --suite 套件

CLI 已有的 `--suite` 参数，通过 JSON 文件定义：

```json
{
  "suite_name": "smoke",
  "description": "冒烟测试套件",
  "case_ids": ["char-select", "first-battle"]
}
```

CLI `--suite smoke` → 读文件 → 展开 `case_ids` → 传给 `run_all()`。纯 CLI 层逻辑，不涉及 CaseRegistry。

### --cases 找不到

```python
# 在 run_cmd 中提前校验
invalid = [cid for cid in args.cases if cid not in CaseRegistry.list_all()]
if invalid:
    print(f"[autotest] Unknown case IDs: {', '.join(invalid)}")
    return 1
```

## pytest 路径

pytest 路径不经 CaseRegistry——用户直接用 fixture：

```python
# tests/test_my_case.py
def test_first_battle(autotest):
    """autotest fixture 已注入 TestOrchestrator，直接调 adapter"""
    state = autotest.adapter.get_state()  # 通过 _session_loop 桥接
    # ...
```

或者也可以从 pytest 测试中调用 CaseRegistry 的 runner：

```python
from sts2_autotest.core.case_registry import CaseRegistry

def test_first_battle(autotest, _session_loop):
    case = CaseRegistry.resolve("first-battle")
    result = _session_loop.run_until_complete(case.runner(autotest))
    assert result.passed
```

两条路径不互斥——同一个用例可以在 CLI 中用 CaseRegistry 注册，同时在 pytest 中用 fixture 直接调。

## e2e_first_battle.py 迁移路径

1. 把 `async def main()` 签名为 `async def first_battle_runner(orch: TestOrchestrator) -> TestResult`
2. 函数体内 `adapter` → `orch.adapter`，`act()` → `orch.execute_action(ActionDescriptor(...))`
3. 文件末尾加 `CaseRegistry.register(CaseDefinition(...))`
4. 文件放入 `tests/cases/`（或 `case_directories` 配置的目录）
5. 执行 `autotest run --cases first-battle`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/case_registry.py` | 新增 | `CaseDefinition`、`CaseRunner`、`CaseRegistry` |
| `core/orchestrator.py` | 修改 | `execute_case()` 从桩改为查注册表分发 |
| `cli/main.py` | 修改 | `--all` 加目录扫描 + import；`--cases` 加预校验 |
| `config/schema.py` | 修改 | 加 `case_directories: list[str]` 字段 |
| `tests/unit/test_case_registry.py` | 新增 | 注册/解析/列表/去重等单元测试 |

## 不动

- `ActionDescriptor`、`FluentBuilder`、`TestResult`
- `Orchestrator.execute_action()` / `execute_action_sequence()` / `run_all()` / `run_failed()`
- `pytest_plugin/` 全部 fixture 和 plugin
- `adapters/`、`evidence/`、`dsl/`、`state_engine.py`

## 非目标

以下不在本方案范围：

- **热加载/重载** — 用例文件修改后需重启进程，MVP 不做热加载
- **用例间依赖** — 每个用例独立，不定义依赖关系
- **用例参数化** — 如需多参数组合，推荐 pytest `@parametrize`，CaseRegistry 不做二次封装
- **用例版本管理** — 不追踪用例变更历史
