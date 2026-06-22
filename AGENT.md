# AGENT.md

本文件是本仓库给 Codex、Claude Code、通用 coding agent 和委派子 agent 的初始化工作指南。进入仓库后先读本文件，再按任务读取对应的权威资料。

## 项目概览

STS2-AUTOTEST 是面向《杀戮尖塔 2》（Slay the Spire 2）Mod 的端到端自动化测试编排框架。它位于游戏控制工具（STS2-Cli-Mod、STS2-Agent）与 pytest 测试执行之间，负责状态管理、动作编排、断言 DSL、pytest 集成、证据采集、日志/截图/指标打包和适配器抽象。

项目约束：

- Python `>=3.11`，主要运行平台 Windows 11，开发可在 macOS 上进行。
- `src/` layout，构建后端为 `hatchling`。
- 文档、代码注释、架构说明默认使用中文；保留英文术语时需配中文解释。
- 设计目标是本地开发测试，不是生产服务器部署。
- 不直接读写游戏进程内存；所有游戏交互必须通过适配器接口。

## 首次进入必读

- 项目说明与红线：`CLAUDE.md`（最完整的项目说明，深度细节以此为准）
- Test Agent 角色规则：`AGENTS.md`
- 权威路线图：`docs/beta-roadmap.md`
- 用户手册：`docs/user-manual.md`
- Beta Epics/Story 拆解：`_bmad-output/planning-artifacts/beta-epics.md`
- Sprint 状态：`_bmad-output/implementation-artifacts/sprint-status.yaml`
- STS2-Cli-Mod CLI 参考：`docs/sts2-cli-mod-reference.md`

## 当前交付状态

截至 `2026-06-17`：

- Epic 1（Foundation & Game Control）：`done`
- Epic 2（Test Authoring & Execution）：`done`
- Epic 3（Evidence & Observability）：`done`
- Epic 4（Resilience & Operational Safety）：`done`
- Epic 5（Runtime Control）：`done`
- Beta 扩展（B7 AgentAdapter、B8 Visual QA 稳定版、B10 修复建议、B11 CI/CD、B13 桌面通知、B17 Health Check HTTP、B25 NL 流水线）：`done`

剩余事项以 `docs/beta-roadmap.md` 为准（主要为：真实环境验收补跑、B15 安全沙箱、B9 多人冒烟、B6 覆盖率报告）。

## 必须暂停并请求决策的情况

遇到以下任一情况，停止推进并向用户说明、请求决策：

- 验收标准与当前实现或安全工程实践冲突。
- 需要改变 public API 名称、签名、导出类型。
- 共享数据模型或跨模块边界发生变化。
- 需要引入 shortcut、stub 或推迟工作（deferred work）。
- 无法写测试证明某条验收标准。
- 多个改动来源对同一文件的所有权发生冲突。

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
- 错误统一归类为 adapter、game、assertion、crash、timeout、session 等类型，并向上层提供结构化上下文。
- 持久化文件使用同目录临时文件 + `os.replace()` 原子写入。
- 所有 timeout、阈值、策略参数应收敛到 `config/schema.py`，不要散落魔法数字。
- pytest 用户测试保持同步函数，框架内部用 session scope event loop 桥接 async 调用。

## 目录速览

```text
src/sts2_autotest/common/         共享模型、错误、状态、证据类型、Protocol 接口、日志
src/sts2_autotest/config/         配置 schema、加载、校验错误
src/sts2_autotest/adapters/       适配器协议、CliModAdapter、AgentAdapter、CLI 发现
src/sts2_autotest/core/           状态机、orchestrator、恢复策略、修复建议、桌面通知、用例注册表、Steam 控制、规格流水线、Test Agent 工作流
src/sts2_autotest/dsl/            Fluent API、断言、fixture 加载、失败处理动作
src/sts2_autotest/pytest_plugin/  pytest plugin、fixtures、markers、hooks
src/sts2_autotest/evidence/       截图、日志、packager、metrics
src/sts2_autotest/cli/            `autotest` 命令入口 + health_server（B17）+ MCP 测试服务（B11）
src/sts2_autotest/report_html.py  HTML 测试报告生成（gen-report）
tests/unit/                       单元测试，原则上与 src 文件 1:1 对应
tests/integration/                真实 STS2-Cli-Mod smoke / 集成测试
tests/generated/                  NL 规格编译后的 pytest 文件
```

`autotest` 子命令：`run / review / compile / doctor / report / queue / progress / agent-test / serve / serve-mcp / gen-report`。

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
- 提交前记录 `pytest`、`mypy strict`、`lint-imports` 证据。
- 若当前 runner 无法执行 `mypy`，可接受用户、CI 或其他来源提供的外部证据，但必须明确写明。

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

测试命名应描述行为，而不是实现细节。对验收标准的覆盖要能追溯到具体测试。

## 当前已知风险和技术债

- `setup()` 场景构造仍缺自动验证（见 `docs/beta-roadmap.md` 遗留技术债）。
- `metrics._resource_usage` 字典无上限增长，长跑场景需关注边界。
- 真实环境验收（B25 端到端、Epic5 4h 长跑）尚待在 Windows 真机补跑。

> Epic10 已修复早期的 hooks 多 session 泄漏、`on_error` 签名、缓存竞态、`_coerce_types` 类型债等，不再是活跃风险。

## 工作区安全

- 这个仓库经常有多个 agent 或用户同时改动。开始前查看 `git status --short`。
- 不要 revert、reset、checkout 或删除你没有创建的改动。
- 只改当前任务需要的文件。
- 如果必须改公共 API、共享数据模型或架构边界，先停下来请求决策。
- 结束时向用户说明修改过的文件和未能运行的验证命令。
