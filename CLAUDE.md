# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目概览

STS2-AUTOTEST 是一个面向杀戮尖塔 2（Slay the Spire 2）Mod 的端到端自动化测试编排框架。它填补了游戏控制工具（STS2-Cli-Mod、STS2-Agent）与测试执行之间的空白——提供状态管理、动作编排、断言 DSL、证据采集、适配器抽象和自然语言测试规格流水线。

Python >=3.11，主要运行平台 Windows 11，开发可在 macOS 上进行。src-layout 结构，hatchling 构建后端。当前处于 Beta 阶段。

## 交流语言

**默认使用中文交流。** 所有回复、文档、代码注释、架构文档、PRD 等默认用中文陈述。以下情况可保持英文原文，但需附加中文注释或 `()` 内说明：

- 编程语言关键字（如 `async`、`await`）
- 领域专有名词（如 Circuit Breaker、pytest）
- 遵循项目命名规范的文件名、模块名、类名、函数名、变量名（如 `GameScreen`、`sts2_autotest`）

## 开发命令

```bash
# 安装项目及开发依赖
pip install -e ".[dev]"

# 运行单元测试（不依赖真实游戏，纯逻辑 + mock）
python -m pytest tests/unit/ -v

# 运行集成测试（STS2-Cli-Mod CLI-only 层，需要 CLI 环境）
python -m pytest tests/integration/ -v

# 类型检查（src/ 强制 strict 模式）
mypy src/sts2_autotest --strict

# 导入层级隔离检查
lint-imports
```

项目无构建步骤，通过 editable 模式安装。每次修改后需运行测试、mypy 和 lint-imports。

## 架构：层级隔离

项目通过 import-linter 强制执行严格的层级隔离。依赖方向为单向：

```
evidence | dsl | pytest_plugin | cli | config
    ↓
  core
    ↓
 adapters
    ↓
 common
```

**规则：** `common/` 是唯一的共享层。所有其他包（`adapters`、`core`、`evidence`、`dsl`、`pytest_plugin`、`config`、`cli`）为平级关系——它们之间禁止互相导入。任何模块只能导入 `common/`（或同包内的模块）。该规则由 `.importlinter` 强制执行。

**common/ 入场规则：** 仅被 ≥3 个模块引用的类型/枚举/工具才能放入 `common/`。例外：`logging.py`（可无条件入场）。向 `common/` 添加新文件时需在 PR 中说明引用计数。

**跨包解耦模式：** 使用 `Protocol` 接口（定义在 `common/types.py`）解耦 `core/`、`evidence/` 与 `config/` 之间的依赖。例如 `ScreenCaptureSettings`、`RecoverySettings`、`EvidencePackagerSettings` 等 Protocol 让消费者只依赖协议而不导入具体实现。

## 包结构一览

```
src/sts2_autotest/
├── common/          # 共享层：状态模型、错误分类、证据模型、Protocol 接口、日志
│   ├── state.py     # GameScreen（15 种状态 StrEnum）+ GameState（frozen pydantic 模型）
│   ├── errors.py    # STS2Error + ErrorCategory（6 类）+ AdapterErrorSubType + SessionQueueError
│   ├── evidence.py  # EvidencePack + SummaryJson + RunInfo 等证据包模型
│   ├── types.py     # Capabilities + CaptureResult + 各种 Settings Protocol + SessionStatus
│   ├── spec_models.py # TestSpec/SuiteSpec/ReviewReport 等 NL 规格流水线数据模型
│   └── logging.py   # 统一日志工具
├── adapters/        # 适配器层：协议定义 + 两种适配器实现
│   ├── base.py      # GameAdapterProtocol（7 个核心 async 方法）+ ActionResult + HealthStatus
│   ├── cli_mod.py   # CliModAdapter：通过 sts2 CLI 子进程驱动游戏，含战斗自动策略
│   ├── agent.py     # AgentAdapter：通过 HTTP/MCP 接入 STS2-Agent，双传输支持
│   └── discovery.py # sts2 CLI 可执行文件自动发现 + Steam 游戏目录定位
├── core/            # 核心层：编排、状态机、恢复、规格处理
│   ├── orchestrator.py    # TestOrchestrator：会话生命周期管理，SIGINT 优雅退出
│   ├── state_engine.py    # StateEngine：状态转移验证 + 强制转移（恢复路径）
│   ├── recovery.py        # DefaultRecoveryStrategy：三级恢复（FAST_PATH → RECREATE → GAME_RESTART → FULL_RESTART → TERMINATE）
│   ├── action_model.py    # ActionDescriptor + TestResult
│   ├── code_generator.py  # Markdown 规格 → pytest 测试代码生成
│   ├── markdown_parser.py # Markdown 规格文件解析
│   ├── spec_reviewer.py   # NL 规格审查 + 自动修订稿生成
│   ├── data_validator.py  # 游戏状态语义验证
│   ├── watchdog.py        # 会话僵尸检测
│   ├── popup_disposal.py  # 弹窗自动分类处置
│   ├── session_queue.py   # 本地测试队列管理
│   ├── progress.py        # 断点续跑进度持久化
│   ├── steam.py           # Steam 控制器（启动/停止游戏与 Steam）
│   ├── precheck.py        # 运行前环境检查
│   ├── disk_guard.py      # 磁盘空间守护
│   ├── lock_manager.py    # 进程级互斥锁
│   ├── workspace.py       # 多 MOD 项目工作空间管理
│   ├── evidence_hooks.py  # EvidenceHooks Protocol + Stub/Real 实现
│   └── logs.py            # 日志收集
├── evidence/        # 证据与可观测性层
│   ├── capture.py   # 截图采集（mss）+ RGB 纯色校验
│   ├── logs.py      # 游戏日志收集
│   ├── metrics.py   # 指标事件收集（MetricsCollector）
│   └── packager.py  # 证据打包（异步 ZIP）
├── dsl/             # 断言 DSL 层
│   ├── fluent.py    # FluentBuilder：define() → given/when/then 链式 API
│   ├── assertions.py # 预制断言动作（advance_dialogue、choose_map_node 等）
│   ├── fixtures.py  # pytest fixtures
│   └── handlers.py  # handler 注册与执行
├── pytest_plugin/   # pytest 插件层
│   ├── plugin.py    # pytest 入口（hooks + fixtures 注册）
│   ├── fixtures.py  # Session-scoped adapter fixture + 异步桥接
│   ├── hooks.py     # pytest hooks（session start/end）
│   └── markers.py   # 自定义标记（@pytest.mark.integration、@pytest.mark.requires_game）
├── config/          # 配置层
│   ├── schema.py    # STS2Config（四层继承：默认 → YAML → 环境变量 → CLI 参数）+ 互斥校验
│   ├── loader.py    # 配置加载器（YAML + dotenv）
│   └── errors.py    # ConfigValidationError
└── cli/             # CLI 入口层
    └── main.py      # autotest run/doctor/report/review/compile/queue/progress 七个子命令
```

## 当前实现状态

**已完成并合入主干的全部功能：**

- **Epic 1**（基础与游戏控制）：项目脚手架、`common/` 数据模型、CliModAdapter、自动发现
- **Epic 2**（测试编写与执行）：TestOrchestrator、StateEngine、DSL fluent API、pytest 插件、CLI 入口
- **Epic 3**（证据与可观测性）：截图采集（RGB 校验）、日志收集、MetricsCollector、证据打包
- **Epic 4**（健壮性与运维）：RecoveryStrategy 三级恢复、Watchdog、DiskGuard、LockManager、Precheck、弹窗处置、SessionQueue、进度持久化
- **Epic 5**（运行时控制）：无人值守运行时、队列管理、实时进度与暂停/继续、异步打包
- **B25 NL 流水线**：Markdown 规格 → 审查（`spec_reviewer`）→ 修订稿 → 代码生成（`code_generator`）→ CLI 统一执行
- **B7 AgentAdapter**：HTTP + MCP 双传输、版本握手
- **B19 集成测试**：分层 CLI-only + requires_game 测试
- **Epic10 技术债**：hooks 泄漏修复、缓存竞态修复、start_session 补齐检查点

**测试覆盖：** 1067 个单元测试、24 个集成测试、端到端冒烟测试。共 ~10,500 行源代码。

**待实现（Beta 后续）：**

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P1 | 真实环境验收补跑 | B25 端到端 + Epic5 4h 长跑 |
| P2 | B15 安全沙箱（Windows Job Objects + ACL） | Story 4.8 推迟 |
| P2 | B17 Health Check HTTP 端点 | 扩展 doctor 为 HTTP 服务 |
| P3 | B11 CI/CD（GitHub Actions / 自托管 Runner） | PR 注释 + JUnit XML |
| P3 | B10 Level 2 修复建议（crash pack → patch.diff） | |
| P3 | B13 桌面通知 | 运行完成后通知 |
| P4 | B8 Visual QA Engine（OCR + OpenCV + VLM） | 视觉审查 |
| P4 | B9 多人冒烟测试 | 双 Runner 编排 |
| P5 | B6 覆盖率报告 | 按游戏场景维度 |

**权威路线图：** `docs/beta-roadmap.md`
**架构文档：** `_bmad-output/planning-artifacts/architecture.md`
**PRD：** `_bmad-output/planning-artifacts/prd.md`
**实现计划：** `_bmad-output/planning-artifacts/epics.md`

## 核心设计决策

- **不可变状态**：`GameState` 使用 `pydantic.BaseModel(frozen=True, extra="allow")`。每次 `get_state()` 调用返回新的不可变快照。`extra="allow"` 容忍游戏版本变更引入的未知字段（适配器版本缓冲区模式）。
- **错误模型**：6 种错误类别（`ErrorCategory` StrEnum）——adapter_error、game_error、assertion_error、crash_error、timeout_error、session_error。所有适配器层异常在向上传播前完成分类，Orchestrator 只看到分类后的错误。错误响应结构：`{type, message, detail, timestamp}`（ISO 8601 UTC）。
- **状态机**：`GameScreen` 是权威的 15 种状态 StrEnum（含 UNKNOWN），附带显式 `allowed_transitions` 映射。终止态（GAME_OVER、VICTORY、CRASHED、UNKNOWN）无允许的转移。状态校验集中在 `core/state_engine.py`。恢复路径使用 `force_transition()` 绕过验证并记录 WARNING。
- **适配器抽象**：Protocol + ABC 模式。`GameAdapterProtocol` 定义 7 个核心 async 方法（health_check、get_state、get_available_actions、act、wait_until_actionable、capture_bug_snapshot、cleanup）。Orchestrator 仅依赖 Protocol 接口，不依赖适配器内部实现。
- **双适配器**：CliModAdapter（同步子进程 + `asyncio.to_thread` 桥接）和 AgentAdapter（HTTP/MCP 双传输）。通过 `--adapter` CLI 参数或 `STS2_ADAPTER__*` 环境变量选择，互斥启用。
- **三级恢复策略**：不使用 Circuit Breaker（单进程架构无级联雪崩风险）。`DefaultRecoveryStrategy` 基于 P0 异常类型 + 连续失败历史决定恢复动作。P0（FileNotFoundError、OSError、VERSION_MISMATCH）立即终止；crash 类走 GAME_RESTART → FULL_RESTART → TERMINATE 渐进路径；timeout/adapter 类走 FAST_PATH → RECREATE → TERMINATE。连续 N 次同类异常升级为 `deterministic_fail` 并终止会话。
- **优雅降级**：适配器降级检测（连续 2 次失败标记 degraded），MVP 仅记录日志。
- **原子写入**：所有持久化文件使用 write-to-temp + `os.replace()`。临时文件在目标文件同一父目录下创建，避免跨分区 rename 失败。
- **pytest 异步桥接**：用户编写同步测试函数。框架在 session scope 管理事件循环，通过 `loop.run_until_complete()` 桥接异步适配器调用。
- **NL 测试流水线**：Markdown 规格（`docs/process/specs/`）→ 解析（`markdown_parser`）→ 审查（`spec_reviewer`，4 类 issue）→ 修订稿生成 → 代码生成（`code_generator`）→ pytest 文件。CLI `autotest run --all` 自动走 review → compile → pytest 流水线。

## 编码规范

- **命名**：函数/变量/模块/包用 snake_case；类用 PascalCase；常量/枚举用 UPPER_SNAKE；JSON 键用 snake_case
- **类型注解**：`src/sts2_autotest/` 中所有公共 API 必须加类型注解。强制 mypy strict 模式
- **错误处理**：`try/except` 主要在适配器层和 CLI 层使用。禁止裸 `except:` 捕获。所有外部调用必须有超时
- **资源清理**：用上下文管理器（`__enter__`/`__exit__`）管理资源生命周期。`__exit__` 必须包含 10 秒超时逻辑——超时则强制清理，防止僵尸进程累积
- **禁止魔法数字**：所有超时、阈值、策略参数统一走 `config/schema.py` 配置
- **测试与源码 1:1 对应**：`src/` 中每个源文件对应 `tests/unit/` 中的一个 `test_*.py` 文件
- **禁止 `model_construct()`**：永远不要使用 pydantic 的 `model_construct()`——它会跳过校验并破坏 `frozen` 语义
- **配置四层继承**：默认值 → YAML 文件 → 环境变量（`STS2_` 前缀，`__` 分隔符）→ CLI 参数。后者覆盖前者
- **适配器互斥**：CLI 和 Agent 适配器不能同时启用，由 `STS2Config._check_adapter_mutual_exclusion` 校验

## 测试分层

| 层级 | 路径 | 标记 | 依赖 | 用途 |
|------|------|------|------|------|
| 单元测试 | `tests/unit/` | 无 | 纯 mock | 逻辑验证 |
| 集成测试 | `tests/integration/` | `@pytest.mark.integration` | CLI 环境（部分需游戏运行 `@pytest.mark.requires_game`） | 真实适配器交互 |
| 生成测试 | `tests/generated/` | 无 | 规格流水线产物 | NL 规格编译后的 pytest 文件 |
| 端到端 | `tests/e2e_*.py` | 无 | 完整环境 | 冒烟测试 |

## 环境配置

参见 `.env.example`，核心配置项：

```bash
# 适配器选择
STS2_ADAPTER__CLI__ENABLED=true        # CLI 适配器（默认启用）
STS2_ADAPTER__AGENT__ENABLED=false     # Agent 适配器（Beta，默认禁用）
STS2_ADAPTER__AGENT__TRANSPORT=http    # Agent 传输方式：http 或 mcp
STS2_ADAPTER__AGENT__ENDPOINT=http://localhost:8080

# 执行参数
STS2_EXECUTION__GAME_TIMEOUT=60.0
STS2_EXECUTION__GAME_STARTUP_TIMEOUT=60.0
STS2_EXECUTION__MAX_RETRIES=3
```
