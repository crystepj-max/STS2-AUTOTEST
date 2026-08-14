# STS2-AUTOTEST Agent 指南

本文件面向 AI coding agent，假定读者对本项目一无所知。进入仓库后先读本文件；更深的细节以 `CLAUDE.md`（最完整的项目说明）和 `docs/` 下的权威文档为准。

## 项目概览

STS2-AUTOTEST 是面向《杀戮尖塔 2》（Slay the Spire 2）Mod 的端到端自动化测试编排框架，承担角色化多 Agent 流程中的 Test Agent 底座。它位于游戏控制工具（STS2-Cli-Mod、STS2-Agent）与 pytest 测试执行之间，提供状态管理、动作编排、断言 DSL、pytest 集成、证据采集（截图/日志/指标打包）、自然语言测试规格流水线，以及 CLI/MCP 双接入的统一任务入口。

关键约束：

- Python `>=3.11`，主要运行平台 Windows 11，开发可在 macOS 上进行。
- `src/` layout，构建后端 `hatchling`；无构建步骤，以 editable 模式安装。
- 设计目标是本地开发测试，不是生产服务器部署。
- 不直接读写游戏进程内存；所有游戏交互必须通过适配器接口。
- 不保存 Gawain 等业务 Mod 的实现代码；业务仓库只提供测试定义和目标分支。

## 交流语言

**默认使用中文交流。** 所有回复、文档、代码注释均使用中文，除非遇到以下情况可保留英文原文（需附加中文说明）：

- 编程语言关键字（如 `async`、`await`）
- 领域专有名词（如 Harmony Patch、Godot）
- 遵循项目命名规范的文件名、模块名、类名、函数名、变量名

## 仓库职责与边界

职责：

- 管理通用测试计划（`test-plans/`）。
- 执行 build、静态检查、localization check。
- 部署 Mod 到 STS2 mods 目录。
- 启动游戏并等待自动化接口可用。
- 执行 smoke test / regression test。
- 收集日志、截图、状态 JSON 和测试报告（输出到 `reports/`）。

目录边界：

- `src/sts2_autotest/`：框架源码（见下文模块划分）。
- `tests/unit/`：单元测试，原则上与 `src/` 文件 1:1 对应。
- `tests/integration/`：真实 STS2-Cli-Mod / 游戏环境的集成测试。
- `tests/generated/`：NL 规格编译后的 pytest 文件（流水线产物，勿手改）。
- `tests/fixtures/`：测试数据与样例输入；`tests/e2e_*.py`：端到端冒烟脚本。
- `test-plans/`：结构化测试计划（YAML，含检查项、证据与判定规则）。
- `scripts/`：测试执行与 runner 脚本；`reports/`：测试报告输出目录。
- `docs/process/specs/`：自然语言测试规格（Markdown），NL 流水线的输入。

## 技术栈

- 语言/运行时：Python ≥3.11；核心依赖见 `pyproject.toml`——pytest、pydantic v2、pyyaml、python-dotenv、psutil、mss（截图）、httpx、portalocker；可选 `[visual]`（opencv-python-headless）。
- CLI 入口：`autotest`（`sts2_autotest.cli.main:cli`），安装后自动注册 pytest 插件。
- 双适配器（互斥，`--adapter` 或 `STS2_ADAPTER__*` 环境变量选择）：
  - `CliModAdapter`（默认）：通过 `sts2` CLI 子进程驱动游戏，`asyncio.to_thread()` 桥接同步调用。
  - `AgentAdapter`（Beta）：通过 HTTP / MCP 接入 STS2-Agent。
- 包管理：仓库含 `uv.lock` 与 `.venv/`；文档标准安装方式为 `pip install -e ".[dev]"`。

## 构建与开发命令

```bash
# 安装（含 dev 依赖：ruff、mypy、import-linter、pytest-mock 等）
pip install -e ".[dev]"

# 默认验证三件套（每次改代码后至少跑相关单元测试；提交前三者都要有证据）
python -m pytest tests/unit/ -q
mypy src/sts2_autotest --strict
lint-imports

# 代码风格检查（CI 执行；仓库无 ruff 配置文件，使用默认规则）
ruff check src/ tests/

# 集成测试（需 STS2-Cli-Mod CLI 环境；无游戏时自动跳过而非失败）
python -m pytest tests/integration/ -q

# 环境文件忽略门禁（issue-23：.env 不得入库，仅 .env.example 可被跟踪）
bash scripts/check-env-gitignore.sh
```

验证基线（2026-07-17 实测）：`tests/unit/` 1552 个测试全部通过；`lint-imports` 契约通过。若当前环境无法执行某条命令，可接受用户、CI 或其他来源的外部证据，但必须明确写明。

## 架构与模块划分

import-linter（`.importlinter`）强制层级隔离，依赖方向单向：

```text
evidence | dsl | pytest_plugin | cli | config
core
adapters
common
```

- `common/` 是唯一共享层，可被所有包导入；仅当被 ≥3 个模块引用的类型/枚举/工具才允许进入（`logging.py` 例外）。
- 顶层功能包（`evidence`、`dsl`、`pytest_plugin`、`cli`、`config`）之间禁止横向导入，只能向下依赖 `core` → `adapters` → `common`。
- 跨包解耦使用定义在 `common/types.py` 的 `Protocol`（如 `ScreenCaptureSettings`、`RecoverySettings`）。

模块速览（`src/sts2_autotest/`）：

- `common/`：`state.py`（`GameScreen` StrEnum 权威状态枚举 + frozen `GameState`）、`errors.py`（6 类 `ErrorCategory`）、`evidence.py`、`types.py`、`spec_models.py`、`visual_qa.py`、`logging.py`。
- `adapters/`：`base.py`（`GameAdapterProtocol`，7 个核心 async 方法）、`cli_mod.py`、`agent.py`、`discovery.py`（sts2 CLI 与 Steam 目录自动发现）。
- `core/`：`orchestrator.py`（会话生命周期）、`state_engine.py`（状态转移校验）、`recovery.py`（渐进式恢复策略）、`navigation.py` / `journeys.py`（通用目标场景执行）、`run_service.py`（CLI/MCP 共用的统一任务服务：持久化、排队、幂等、取消/恢复）、`lifecycle.py`（游戏进程拉起与调试 API 注入）、`runtime_factory.py`、`markdown_parser.py` / `spec_reviewer.py` / `code_generator.py`（NL 规格流水线）、`steam.py`、`precheck.py`、`watchdog.py`、`disk_guard.py`、`lock_manager.py`、`session_queue.py`、`progress.py`、`popup_disposal.py`、`repair_advisor.py`、`notifier.py`、`case_registry.py`、`workspace.py`、`test_agent_runner.py`、`visual_qa.py`。
- `dsl/`：`fluent.py`（given/when/then 链式 API）、`assertions.py`、`fixtures.py`、`handlers.py`。
- `pytest_plugin/`：plugin、fixtures（session 级适配器 + 异步桥接）、hooks、markers。
- `evidence/`：`capture.py`（mss 截图 + RGB 校验）、`logs.py`、`metrics.py`、`packager.py`（异步 ZIP 打包）。
- `config/`：`schema.py`（`STS2Config` 四层继承：默认值 → YAML → 环境变量 → CLI 参数；适配器互斥校验）、`loader.py`、`errors.py`。
- `cli/`：`main.py`（autotest 入口）、`health_server.py`（`/health`、`/health/live`、`/health/ready`）、`mcp_server.py` / `mcp_protocol.py` / `mcp_tools.py`（MCP 测试服务）。
- `report_html.py`：HTML 报告生成（`gen-report`）。

核心设计决策：

- `GameState` 为 `pydantic.BaseModel(frozen=True, extra="allow")`，每次 `get_state()` 返回不可变快照。
- 所有适配器异常向上传播前归类为 6 类错误（adapter/game/assertion/crash/timeout/session），响应结构 `{type, message, detail, timestamp}`。
- 恢复不用 Circuit Breaker：`DefaultRecoveryStrategy` 按异常类型 + 连续失败历史走 FAST_PATH → RECREATE → GAME_RESTART → FULL_RESTART → TERMINATE 渐进路径；P0 异常立即终止。
- 持久化文件一律同目录临时文件 + `os.replace()` 原子写入。
- 用户 pytest 测试保持同步函数，框架用 session scope 事件循环桥接 async 适配器调用。

## CLI 命令一览

`run`（支持 `--all`、`--resume`、`--detach`、`--journey` 等）、`review`、`compile`、`doctor`（`--json`/`--ci`）、`report`、`queue`、`status`、`cancel`、`resume`、`capabilities`、`progress`、`agent-test`（构建 → 本地化检查 → 部署 → 冒烟 → 报告一键工作流）、`serve`（健康检查 HTTP）、`serve-mcp`、`gen-report`、`visual-qa`。详细用法见 `docs/user-manual.md`。

## 编码规范

- 命名：函数/变量/模块/包 `snake_case`；类 `PascalCase`；常量/枚举成员 `UPPER_SNAKE`；JSON key `snake_case`。
- `src/sts2_autotest/` 公共 API 必须有类型注解，遵守 `mypy --strict`（tests 不强制，见 `mypy.ini`）。
- 禁止裸 `except:`；所有外部调用必须有 timeout；`try/except` 主要在适配器层和 CLI 层。
- 资源生命周期用上下文管理器；`__exit__` 需含超时与强制清理逻辑（防僵尸进程）。
- 禁止 Pydantic `model_construct()`（跳过校验并破坏 frozen 语义）。
- 禁止魔法数字：timeout、阈值、策略参数统一收敛到 `config/schema.py`。
- Windows-only 路径或进程行为要清楚标注，不要假装跨平台。
- 新增/修改源文件时同步检查对应 `tests/unit/test_*.py`；代码注释少而准，只解释不显然的约束或决策。
- 环境变量配置模式：`STS2_<SECTION>__<KEY>`（双下划线分隔），模板见 `.env.example`；`.env` 不入库。

## 测试策略与分层

优先级：

1. 行为单元测试（`tests/unit/`，纯逻辑 + mock，与 src 文件 1:1 对应）。
2. 架构边界：`lint-imports` 必须通过。
3. 类型边界：`mypy src/sts2_autotest --strict`。
4. 集成测试（`tests/integration/`，`@pytest.mark.integration`；需真实游戏运行的标 `@pytest.mark.requires_game`，无环境时自动跳过，必须明确标注跳过原因或外部依赖）。
5. 生成测试（`tests/generated/`）与端到端脚本（`tests/e2e_*.py`）。

测试命名描述行为而非实现细节；验收标准覆盖要能追溯到具体测试。`sts2` CLI 不在 `PATH` 时设 `STS2_CLI_PATH`。

## CI/CD

GitHub Actions（`.github/workflows/`，均忽略 `docs/**` 与 `**.md` 变更）：

- `ci-pr.yml`：PR 触发，ruff / mypy / lint-imports / 单元测试矩阵，跑在自托管 macOS runner（标签 `["self-hosted","macos","autotest"]`）与 GitHub 托管 ubuntu/windows。
- `ci-main.yml`：push main 触发，quick-checks → CLI 集成测试（`-m "not requires_game"`）→ 构建并部署 Gawain Mod（.NET 9 `dotnet publish`）。
- `ci-game.yml`：`workflow_dispatch` 手动触发，先 `autotest doctor --ci`，再跑需真实游戏的测试。
- `ci-nightly.yml`：每晚 UTC 03:00 全量回归（亦可手动触发），自托管 runner 上最长跑 6 小时。

## 多 Agent 协议与 Test Agent 原则

本仓库遵守（位于兄弟仓库 `../sts2-dev-infra/agent-protocol/`）：`AGENT_CONTRACT.md`、`ROLE_TESTER.md`、`QUALITY_GATES.md`、`ARTIFACT_TEST_REPORT.md`。

Test Agent 原则：

- 优先脚本化和可复现，不依赖模型“感觉”。
- 没有日志、截图或状态 JSON 证据的测试项不得标记为 PASSED。
- 游戏无法启动、自动化接口不可用、环境缺失时标记 BLOCKED。
- 发现裸 Key、missing localization、崩溃、关键交互失败时标记 FAILED。

## 安全与工作区注意事项

- 密钥与本地配置只放 `.env`（已 gitignore），模板用 `.env.example`；仓库不得出现真实凭据。
- 这个仓库经常有多个 agent 或用户同时改动：开始前先看 `git status --short`；不要 revert/reset/checkout 或删除你没有创建的改动；只改当前任务需要的文件。
- 不直接读写游戏进程内存；游戏交互只走适配器。外部进程调用必须有 timeout，子进程清理要防僵尸。
- 遇到以下情况必须暂停并向用户请求决策：验收标准与实现或安全实践冲突；需要改 public API 名称/签名/导出类型；共享数据模型或跨模块边界变化；需要引入 shortcut、stub 或推迟工作；无法写测试证明某条验收标准；同一文件所有权冲突。
- 结束时向用户说明修改过的文件和未能运行的验证命令。

## 关键文档索引

- 最完整的项目说明与红线：`CLAUDE.md`；agent 初始化指南：`AGENT.md`。
- 用户手册：`docs/user-manual.md`；权威路线图：`docs/beta-roadmap.md`。
- STS2-Cli-Mod CLI 参考：`docs/sts2-cli-mod-reference.md`；Agent 适配器：`docs/agent-adapter-guide.md`。
- 跨 Agent：`docs/unified-run-contract.md`、`docs/cross-agent-acceptance.md`、`docs/platform-capability-inventory.md`。
- 自然语言测试：`docs/natural-language-testing/`；规格文件：`docs/process/specs/`。
- Beta Epics/Story：`_bmad-output/planning-artifacts/beta-epics.md`；Sprint 状态：`_bmad-output/implementation-artifacts/sprint-status.yaml`。

## Cursor Cloud specific instructions

- 运行环境是 **Linux 云 VM**，而本框架目标平台是 Windows 11 / macOS。真实游戏《杀戮尖塔 2》（经由 Steam）在此无法运行，因此所有 `@pytest.mark.requires_game` 测试、`autotest doctor` / `serve` 的 readiness 检查、以及任何驱动游戏的流程都属于 **BLOCKED**（缺环境，而非 bug）。可离线验证的核心功能：单元测试、`mypy`、`lint-imports`，以及 NL 规格流水线 `autotest review` / `autotest compile`（把 `docs/process/specs/` 的 Markdown 编译成 pytest 文件）。
- 依赖安装在仓库根的 **`.venv/`**（已 gitignore，由 update script 用 `pip install -e ".[dev,visual]"` 建立，含可选 `[visual]`=opencv 以让 `mypy --strict` 干净通过）。用 `source .venv/bin/activate` 或直接 `.venv/bin/<工具>`（如 `.venv/bin/pytest`、`.venv/bin/mypy`、`.venv/bin/autotest`）运行；命令本身见 README / 上文「构建与开发命令」，无需重复。
- 创建 venv 需要系统包 `python3.12-venv`（`apt install python3.12-venv`），已装入快照；update script 只做 venv + pip，不装系统依赖。
- 本 Linux VM 的单元测试基线：**1685 passed, 8 failed**。这 8 个失败均为平台/环境专属，非回归：`tests/unit/test_mcp_tools.py` 里 4 个用例硬编码 macOS 家目录路径（`/Users/chris/STS2-WORKSPACE/...`，在 Linux 上超出 `~/STS2-WORKSPACE` 白名单）；`tests/unit/test_smoke_card_validation.py` 里 4 个需要 macOS/Windows 的 Steam/Godot 可执行文件。
- `ruff check src/ tests/` 在默认规则下有大量既存 style 报错（仓库无 ruff 配置），非本环境引入；改代码时以不新增为准。
