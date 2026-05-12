# AGENT.md

本文件是本仓库给 Codex、Claude Code、通用 coding agent 和委派子 agent 的初始化工作指南。进入仓库后先读本文件，再按任务类型读取对应的协议、BMAD story 和模板。

## 项目概览

STS2-AUTOTEST 是面向《杀戮尖塔 2》（Slay the Spire 2）Mod 的端到端自动化测试编排框架。它位于游戏控制工具（STS2-Cli-Mod、后续 STS2-Agent）与 pytest 测试执行之间，负责状态管理、动作编排、断言 DSL、pytest 集成、证据采集、日志/截图/指标打包和适配器抽象。

项目约束：

- Python `>=3.11`，目标平台 Windows 11，本地交互式环境优先。
- `src/` layout，构建后端为 `hatchling`。
- 文档、代码注释、架构说明默认使用中文；保留英文术语时需配中文解释。
- 设计目标是本地开发测试，不是生产服务器部署。
- 不直接读写游戏进程内存；所有游戏交互必须通过适配器接口。

## 首次进入必读

按顺序读取：

1. `AGENT.md`
2. `.agent-collab/AGENT_PROTOCOL.md`
3. `.agent-collab/WORKFLOW_ADAPTER.md`
4. `.agent-collab/state/board.md`
5. `.agent-collab/state/next-action.md`
6. 当前任务对应的 `_bmad-output/implementation-artifacts/*.md`
7. 需要写 handoff/review/decision 时，再读 `.agent-collab/templates/` 下对应模板

长期权威资料：

- PRD：`_bmad-output/planning-artifacts/prd.md`
- 架构：`_bmad-output/planning-artifacts/architecture.md`
- Epic/Story 拆解：`_bmad-output/planning-artifacts/epics.md`
- STS2-Cli-Mod CLI 参考：`docs/sts2-cli-mod-reference.md`
- 推迟事项：`_bmad-output/implementation-artifacts/deferred-work.md`

## 当前交付状态

截至 `2026-05-12`：

- Epic 1（Foundation & Game Control）：`done`
- Epic 2（Test Authoring & Execution）：`done`
- Epic 3（Evidence & Observability）：`done`
- Epic 4（Resilience & Operational Safety）：`backlog`

Epic 4 开始前要特别注意 Epic 3 回顾中列出的准备项：

- 补齐 Story 3-1 的 Dev Agent Record。
- Story `.md` 中的 `status` 必须和 `sprint-status.yaml` 同步。
- 优先处理真实 STS2-Cli-Mod 接入与集成测试相关风险。
- 性能敏感代码在开发阶段说明复杂度，不等审查发现。

## 协作协议

本仓库使用 ACP（Agent Collaboration Protocol）+ BMAD。

ACP 状态流：

```text
READY -> IN_PROGRESS -> HANDOFF -> REVIEWING -> CHANGES_REQUESTED -> APPROVED -> VERIFIED -> WORKFLOW_UPDATE_READY -> DONE
```

常用 action type：

- `IMPLEMENT`：实现 story、修复 review finding、写 `DEV_DONE` / `FIX_DONE`。
- `REVIEW`：交叉审查实现、AC 覆盖、测试、架构边界。
- `VERIFY`：运行或记录验证证据。
- `DECIDE`：处理公共 API、数据模型、架构边界、流程冲突。
- `COORDINATE`：所有 gate 通过后更新 BMAD story 和 `sprint-status.yaml`。

协作规则：

- 顶层 Codex / Claude Code 是 generalist agent，可按用户或当前流程需要实现、审查、验证或协调。
- 委派子 agent 必须有明确 role、scope、expected output 和 boundary。
- 默认 active development window 是 `2`，除非 `.agent-collab/state/board.md` 另有规定。
- 实现者不能作为自己实现的唯一 reviewer。
- 实现者完成 story 后写 handoff 并停止，不自行把刚实现的 story 标记为 `done`。
- 最终 BMAD story 状态和 `sprint-status.yaml` 只由协调 agent 在 gate 通过后更新。
- append-only 协作记录优先，不编辑其他 agent 的 mailbox 文件。

Mailbox 约定：

```text
.agent-collab/inbox/<agent-name>/
```

Codex 写 `.agent-collab/inbox/codex/`，Claude Code 写 `.agent-collab/inbox/claude-code/`。长期 review / decision / handoff 记录写 `.agent-collab/log/` 对应目录。

## 必须暂停并请求决策的情况

遇到以下任一情况，停止推进并写 `DECISION_REQUEST`：

- 验收标准与当前实现或安全工程实践冲突。
- 需要改变 public API 名称、签名、导出类型。
- 共享数据模型或跨模块边界发生变化。
- 需要引入 shortcut、stub、deferred work。
- 无法写测试证明某条 AC。
- workflow artifact 缺失或与 sprint state 矛盾。
- 多个 agent 的文件所有权发生冲突。

## 架构边界

import-linter 通过 `.importlinter` 强制层级隔离：

```text
evidence | dsl | pytest_plugin | cli | config
core
adapters
common
```

核心含义：

- `common/` 是唯一共享层，可被其他包导入。
- `adapters/` 可依赖 `common/`。
- `core/` 可依赖 `adapters/` 和 `common/`。
- 顶层功能包 `evidence`、`dsl`、`pytest_plugin`、`cli`、`config` 只能沿上述方向依赖，不允许随意横向导入。
- 向 `common/` 添加新内容要谨慎，原则上仅放置被至少 3 个模块引用的类型、枚举或工具；`logging.py` 是例外。

重要设计决策：

- `GameState` 使用 Pydantic v2 冻结模型，保持运行时不可变。
- `GameScreen` 是权威状态枚举，状态转移校验集中在 `core/state_engine.py`。
- 适配器采用 Protocol + ABC 思路，Orchestrator 依赖协议，不依赖具体适配器内部。
- `CliModAdapter` 通过 `asyncio.to_thread()` 包装同步 CLI 调用。
- 错误统一归类为 adapter、game、assertion、crash、timeout 等类型，并向上层提供结构化上下文。
- 持久化文件使用同目录临时文件 + `os.replace()` 原子写入。
- 所有 timeout、阈值、策略参数应收敛到 `config/schema.py`，不要散落魔法数字。
- pytest 用户测试保持同步函数，框架内部用 session scope event loop 桥接 async 调用。

## 目录速览

```text
src/sts2_autotest/common/         共享模型、错误、状态、证据类型、日志
src/sts2_autotest/config/         配置 schema、加载、校验错误
src/sts2_autotest/adapters/       适配器协议、CliModAdapter、CLI 发现
src/sts2_autotest/core/           状态机、orchestrator、动作模型、Steam 控制、证据 hooks
src/sts2_autotest/dsl/            Fluent API、断言、fixture 加载、失败处理动作
src/sts2_autotest/pytest_plugin/  pytest plugin、fixtures、markers、hooks
src/sts2_autotest/evidence/       截图、日志、packager、metrics
src/sts2_autotest/cli/            `autotest` 命令入口
tests/unit/                       单元测试，原则上与 src 文件 1:1 对应
tests/integration/                真实 STS2-Cli-Mod smoke / 集成测试
```

## 开发命令

安装：

```powershell
pip install -e ".[dev]"
```

默认验证：

```powershell
python -m pytest tests/unit/ -q
python -m mypy src\sts2_autotest --strict
lint-imports
```

补充命令：

```powershell
python -m pytest tests/unit/ -v
python -m pytest tests/integration/ -q
```

本地策略：

- 修改代码后至少运行相关单元测试。
- story/review 完成前记录 `pytest`、`mypy strict`、`lint-imports` 证据。
- 若当前 runner 无法执行 `mypy`，可接受用户、CI 或其他 agent 提供的外部证据，但必须写入 `VERIFY_RESULT` 或 review 记录。

已批准的常见命令前缀：

- `python -m pytest`
- `lint-imports`

## 编码规范

- 函数、变量、模块、包：`snake_case`
- 类：`PascalCase`
- 常量、枚举成员：`UPPER_SNAKE`
- JSON key：`snake_case`
- `src/sts2_autotest/` 中公共 API 必须有类型注解，遵守 `mypy --strict`。
- 禁止裸 `except:`；外部调用必须有 timeout。
- 资源生命周期使用上下文管理器，`__exit__` 需要考虑超时和强制清理。
- 禁止使用 Pydantic `model_construct()`。
- Windows-only 路径或进程行为要清楚标注，不要假装跨平台。
- 新增或修改源文件时同步检查对应 `tests/unit/test_*.py`。
- 代码注释少而准，只解释不显然的约束或决策。

## 测试策略

优先级：

1. 行为单元测试：mock 游戏、mock CLI、mock 文件系统边界。
2. 架构边界：`lint-imports` 必须通过。
3. 类型边界：`mypy src\sts2_autotest --strict`。
4. 集成测试：需要真实 STS2-Cli-Mod 或游戏环境时，默认不作为无环境阻塞项；必须明确标注跳过原因或外部依赖。

测试命名应描述行为，而不是实现细节。对 AC 的覆盖要能在 handoff/review 中追溯到具体测试。

## 自动协作循环

当用户要求启动自动协作任务时，使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/run-agent-loop.ps1 -Task "<task description>"
```

从当前 watcher 生成的下一步继续：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/run-agent-loop.ps1 -FromNextAction
```

半自动扫描：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .agent-collab/tools/watch-agent-collab.ps1 -Once
```

循环约定：

- ClaudeCode 通常负责 IMPLEMENT，写 `DEV_DONE` / `FIX_DONE`。
- Codex 通常负责 REVIEW，写 `REVIEW`。
- `CHANGES_REQUESTED` / `BLOCKED` 返回实现者修复。
- `APPROVED` 后由 coordinator 执行 BMAD 状态处理。
- 循环结束后读 `.agent-collab/state/last-loop-summary.md` 再向用户汇报。

## Handoff / Review 最低要求

实现 handoff 必须包含：

- story id 和标题。
- 变更文件。
- AC 覆盖表。
- 新增或修改的测试。
- 验证命令和结果。
- Known Shortcuts，若无写 `None`。
- Open Decision Requests，若无写 `None`。
- BMAD story artifact 是否存在、sprint status 是否一致。

review 必须检查：

- story artifact 和 sprint status 是否匹配。
- 每条 AC 是否有实现和测试证据。
- public API、数据模型、架构边界是否受影响。
- import-linter 合同是否受影响。
- type-check 和测试证据是否充分。
- Critical / High finding 不得 approve。

## 当前已知风险和技术债

优先关注：

- `CliModAdapter` 真实 CLI 接入与集成测试仍是 Epic 4 前置风险。
- `setup()` 场景构造自动验证不足。
- `on_error` handler 类型签名偏松。
- Story 1.2 `_coerce_types` 仍有低优先级类型债。
- `metrics._resource_usage` 字典无上限增长，适合 Epic 4 边界场景处理。
- `packager._copy_log` 存在低优先级死代码清理项。
- hooks 模块级可变状态在未来并发/多 session 场景有泄漏风险。
- CLI `--resume`、更完整的 `doctor` 检查和进度持久化属于 Epic 4 范围。

## 工作区安全

- 这个仓库经常有多个 agent 或用户同时改动。开始前查看 `git status --short`。
- 不要 revert、reset、checkout 或删除你没有创建的改动。
- 只改当前任务需要的文件。
- 如果必须改协作协议、BMAD 状态或公共 API，先确认是否触发 `DECISION_REQUEST`。
- 结束时向用户说明修改过的文件和未能运行的验证命令。
