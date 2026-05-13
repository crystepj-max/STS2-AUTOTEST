# CLAUDE.md

## Agent Loop Startup

When the user asks to start the automated collaboration loop, run the local ACP/BMAD loop script instead of manually continuing the next handoff.

Recognize these startup phrases:

- `start-agent-loop: <task>`
- `start automated collaboration task: <task>`
- Chinese equivalent: start the automatic collaboration task
- `use .agent-collab/state/next-action.md`

For a new task, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/run-agent-loop.ps1 -Task "<task>"
```

To continue from the current generated next action, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/run-agent-loop.ps1 -FromNextAction
```

After the loop exits, read `.agent-collab/state/last-loop-summary.md` and summarize it for user acceptance.

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目概览

STS2-AUTOTEST 是一个面向杀戮尖塔 2（Slay the Spire 2）Mod 的端到端自动化测试编排框架。它填补了游戏控制工具（STS2-Cli-Mod、STS2-Agent）与测试执行之间的空白——提供状态管理、动作编排、断言 DSL、证据采集和适配器抽象。

Python >=3.11，仅 Windows 11，本地优先。src-layout 结构，hatchling 构建后端。

## 语言规范

所有文档（CLAUDE.md、代码注释、架构文档、PRD 等）默认用中文陈述。以下情况可保持英文原文，但需附加中文注释或 `()` 内说明：

- 编程语言关键字（如 `async`、`await`）
- 领域专有名词（如 Circuit Breaker、pytest）
- 遵循项目命名规范的文件名、模块名、类名、函数名、变量名（如 `GameScreen`、`sts2_autotest`）

## 开发命令

```bash
# 安装项目及开发依赖
pip install -e ".[dev]"

# 运行单元测试（不依赖真实游戏，纯逻辑 + mock）
python -m pytest tests/unit/ -v

# 类型检查（src/ 强制 strict 模式）
mypy src/sts2_autotest --strict

# 导入层级隔离检查
lint-imports
```

项目无构建步骤，通过 editable 模式安装。每次修改后需运行测试、mypy 和 lint-imports。

## 架构：层级隔离

项目通过 import-linter 强制执行严格的层级隔离。依赖方向为单向：

```
common/ ← adapters/ ← core/ ← evidence/
                         ↑         ↑
                        dsl/ ← pytest_plugin/
                         ↑
                       cli/
```

**规则：** `common/` 是唯一的共享层。所有其他包（`adapters`、`core`、`evidence`、`dsl`、`pytest_plugin`、`config`、`cli`）为平级关系——它们之间禁止互相导入。任何模块只能导入 `common/`。该规则由 `.importlinter` 强制执行。

**common/ 入场规则：** 仅被 ≥3 个模块引用的类型/枚举/工具才能放入 `common/`。例外：`logging.py`（可无条件入场）。向 `common/` 添加新文件时需在 PR 中说明引用计数。

## 核心设计决策

- **不可变状态**：`GameState` 使用 `pydantic.BaseModel(frozen=True, extra="allow")`。每次 `get_state()` 调用返回新的不可变快照。`extra="allow"` 容忍游戏版本变更引入的未知字段（适配器版本缓冲区模式）。
- **错误模型**：5 种错误类别（`ErrorCategory` StrEnum）——adapter、game、assertion、crash、timeout。所有适配器层异常在向上传播前完成分类，Orchestrator 只看到分类后的错误。错误响应结构：`{type, message, detail, timestamp}`（ISO 8601 UTC）。
- **状态机**：`GameScreen` 是权威的 15 种状态 StrEnum，附带显式 `allowed_transitions` 映射。终止态（GAME_OVER、VICTORY、CRASHED）无允许的转移。状态校验集中在 `core/state_engine.py`（尚未实现）。
- **适配器抽象**：Protocol + ABC 模式。6 个核心方法构成公共交集。`Capabilities` dataclass 支持运行时动态能力发现。Orchestrator 仅依赖 Protocol 接口，不依赖适配器内部实现。
- **优雅降级**：不使用 Circuit Breaker（单进程架构无级联雪崩风险）。改为 `RecoveryStrategy`（纯函数）基于异常类型 + 失败历史决定恢复动作。连续 3 次同类异常触发会话终止。
- **原子写入**：所有持久化文件使用 write-to-temp + `os.replace()`。临时文件在目标文件同一父目录下创建，避免跨分区 rename 失败。
- **pytest 异步桥接**：用户编写同步测试函数。框架在 session scope 管理事件循环，通过 `loop.run_until_complete()` 桥接异步适配器调用。

## 当前实现状态

**已完成（Epic 1, Story 1.1）：**
- 项目脚手架：`pyproject.toml`、`mypy.ini`、`.importlinter`
- `common/` 数据模型：`state.py`（GameScreen 枚举 + GameState 冻结模型）、`errors.py`（STS2Error + ErrorCategory）、`evidence.py`（EvidencePack + SummaryJson）、`types.py`（Capabilities）、`logging.py`
- 62 个单元测试，覆盖所有 common/ 模块
- 所有子包的桩 `__init__.py`

**尚未实现**（均为桩代码，不含实际逻辑）：
- `cli/main.py`（桩——引用 "Story 2.8"）
- `pytest_plugin/plugin.py`（桩——引用 "Story 2.5"）
- `core/`、`adapters/`、`evidence/`、`dsl/`、`config/` 的全部

**权威架构文档：** `_bmad-output/planning-artifacts/architecture.md`
**PRD：** `_bmad-output/planning-artifacts/prd.md`
**实现计划：** `_bmad-output/planning-artifacts/epics.md`

## 编码规范

- **命名**：函数/变量/模块/包用 snake_case；类用 PascalCase；常量/枚举用 UPPER_SNAKE；JSON 键用 snake_case
- **类型注解**：`src/sts2_autotest/` 中所有公共 API 必须加类型注解。强制 mypy strict 模式
- **错误处理**：`try/except` 仅在适配器层使用。禁止裸 `except:` 捕获。所有外部调用必须有超时
- **资源清理**：用上下文管理器（`__enter__`/`__exit__`）管理资源生命周期。`__exit__` 必须包含 10 秒超时逻辑——超时则强制清理，防止僵尸进程累积
- **禁止魔法数字**：所有超时、阈值、策略参数统一走 `config/schema.py` 配置
- **测试与源码 1:1 对应**：`src/` 中每个源文件对应 `tests/unit/` 中的一个 `test_*.py` 文件
- **禁止 `model_construct()`**：永远不要使用 pydantic 的 `model_construct()`——它会跳过校验并破坏 `frozen` 语义
