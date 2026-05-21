---
status: active
createdAt: '2026-05-21'
inputDocuments: [docs/beta-roadmap.md, _bmad-output/planning-artifacts/epics.md]
---

# STS2-AUTOTEST - Beta Epic Breakdown

本文档把 `docs/beta-roadmap.md` 转换为 BMad sprint-planning 可追踪的 Epic / Story 输入源。
MVP Epic 1-4 继续以 `_bmad-output/planning-artifacts/epics.md` 为权威来源；本文件只覆盖 Beta 阶段新增或延展工作。

## Beta 状态口径

- 已有可运行代码与自动化验证的条目标为 `done`。
- 已有部分代码但接口、实机验证或验收范围仍未闭环的条目标为 `in-progress`。
- 仅有路线图或预留桩代码的条目标为 `backlog`。
- Story 4.8 安全沙箱保持原 key `4-8-security-sandbox-beta`，不在 Beta Epic 中重复创建新 key。

## Epic 5: Beta 运行健壮性与操作者控制（Runtime Resilience & Operator Control）

把 MVP 的崩溃终止、进度输出和会话控制升级为 Beta 可长时间运行的恢复与控制能力。

### Story 5.1: 崩溃三级恢复（Crash Three-Level Recovery）

As a 开发者,
I want 游戏崩溃后按“重启游戏 -> 重启 Steam+游戏 -> 停止重试”的顺序恢复,
So that 单次崩溃不会终止整个测试会话。

**验收标准（Acceptance Criteria）：**

**Given** 第一次非 P0 游戏崩溃
**When** RecoveryStrategy 决策
**Then** 返回 GAME_RESTART 并重建适配器。

**Given** 第二次连续同类崩溃
**When** RecoveryStrategy 决策
**Then** 返回 FULL_RESTART 并重启 Steam 与游戏。

**Given** 第三次连续同类崩溃
**When** RecoveryStrategy 决策
**Then** 标记 deterministic_fail 并继续处理后续用例。

**FRs:** FR5, FR29, FR36, B1

### Story 5.2: 弹窗自动处置（Popup Auto Disposal）

As a 开发者,
I want 框架识别并处置常见 Steam / 游戏弹窗,
So that 恢复流程不会被 EULA、更新、广告等弹窗卡住。

**验收标准（Acceptance Criteria）：**

**Given** 已识别弹窗类型
**When** 弹窗阻塞测试执行
**Then** 框架自动处置并记录截图。

**Given** 崩溃弹窗
**When** 采集证据
**Then** 保留现场，不自动关闭。

**FRs:** B2

### Story 5.3: 四小时无人值守运行验证（Four-Hour Unattended Runtime Validation）

As a QA 操作者,
I want 框架连续无人值守运行至少 4 小时,
So that Beta 阶段可以支撑长时间回归。

**验收标准（Acceptance Criteria）：**

**Given** 稳定本地环境
**When** 运行 Beta smoke / regression suite
**Then** 连续运行时间达到 4 小时且无框架级资源泄漏。

**FRs:** NFR17-Beta, B3

### Story 5.4: 本地测试队列暂停继续（Local Test Queue Pause Resume）

As a 开发者,
I want 管理本地测试队列并支持暂停/继续,
So that 多个测试会话不会互相抢占 Steam 单账号资源。

**验收标准（Acceptance Criteria）：**

**Given** 多个测试会话请求
**When** 队列中已有运行项
**Then** 后续请求按优先级和 FIFO 规则排队。

**Given** 用户暂停队列
**When** 当前用例结束
**Then** 队列停止调度新用例，直到用户继续。

**FRs:** FR65, B4

### Story 5.5: 实时进度暂停继续（Realtime Progress Pause Resume）

As a 操作者,
I want 查看实时进度并暂停/继续运行,
So that 我可以安全介入长时间测试。

**验收标准（Acceptance Criteria）：**

**Given** 测试会话运行中
**When** 进度刷新
**Then** 显示当前用例、步骤、游戏状态和恢复状态。

**Given** 用户请求暂停
**When** 当前步骤到达安全点
**Then** 框架保存进度并暂停执行。

**FRs:** FR60, FR63, B5

### Story 5.6: 游戏场景覆盖率报告（Game Scene Coverage Report）

As a QA 操作者,
I want 按游戏场景维度查看覆盖率,
So that 我知道 Beta 回归覆盖了哪些关键流程。

**验收标准（Acceptance Criteria）：**

**Given** 测试会话完成
**When** 生成报告
**Then** 按战斗、地图、商店、休息点、事件、角色选择等维度展示覆盖情况。

**FRs:** B6

### Story 5.7: 异步 Artifact ZIP 打包（Async Artifact ZIP Packaging）

As a CI/CD 操作者,
I want Evidence Pack ZIP 打包不阻塞会话结束路径,
So that 大量截图或日志不会拖慢测试收尾。

**验收标准（Acceptance Criteria）：**

**Given** 测试会话结束
**When** 需要导出 ZIP artifact
**Then** 打包任务异步执行，并在失败时保留原始 evidence pack。

**FRs:** FR54, B18

## Epic 6: Beta 适配器与真实环境集成（Adapters & Real Environment Integration）

把 MVP 的 CLI 适配器拓展为双适配器体系，并补齐真实 CLI / 游戏运行态验证。

### Story 6.1: AgentAdapter HTTP 基线（AgentAdapter HTTP Baseline）

As a 开发者,
I want 通过 HTTP 端点对接 STS2-Agent,
So that 框架具备 AgentAdapter 的基础控制能力。

**验收标准（Acceptance Criteria）：**

**Given** STS2-Agent HTTP 服务可达
**When** 调用 health/state/actions/act/wait 接口
**Then** AgentAdapter 返回统一的 GameAdapterProtocol 结果。

**FRs:** FR8, FR9, B7

### Story 6.2: AgentAdapter MCP-native 控制（AgentAdapter MCP-Native Control）

As an AI agent,
I want 通过 MCP-native 工具控制 STS2-Agent,
So that 多人冒烟和 agent handoff 能使用统一的适配器抽象。

**验收标准（Acceptance Criteria）：**

**Given** MCP Server 可用
**When** AgentAdapter 选择 MCP 控制路径
**Then** 行为与 HTTP 基线保持同一错误模型和状态模型。

**FRs:** FR8, FR9, B7

### Story 6.3: 适配器能力发现契约（Adapter Capabilities Contract）

As a 调度器,
I want 通过统一能力发现接口查询适配器特性,
So that Orchestrator 可以在运行时选择安全的能力路径。

**验收标准（Acceptance Criteria）：**

**Given** 任一适配器实例
**When** 查询 capabilities 或 get_capabilities
**Then** 返回 Capabilities，且接口形态在 Protocol、实现和文档中一致。

**FRs:** FR25, B14

### Story 6.4: CliModAdapter 真实 CLI 集成测试（Real CliModAdapter CLI Integration Tests）

As a 开发者,
I want 在真实 STS2-Cli-Mod CLI 上运行集成测试,
So that mock 测试之外也能证明 CLI 命令格式和版本握手有效。

**验收标准（Acceptance Criteria）：**

**Given** sts2 CLI 可发现
**When** 运行 integration 测试
**Then** CLI-only 层验证版本、ping、无游戏状态和参数构造路径。

**FRs:** FR8, FR50, B19

### Story 6.5: 真实游戏运行态 CLI 冒烟（Game-Running CLI Smoke Validation）

As a QA 操作者,
I want 在真实游戏运行时执行 CLI 冒烟测试,
So that 状态读取、可用动作和基础生命周期经过实机验证。

**验收标准（Acceptance Criteria）：**

**Given** 游戏已运行且 CLI 可连接
**When** 运行 game-required integration tests
**Then** 状态读取、动作列表和生命周期测试不被跳过并全部通过。

**FRs:** FR9, FR10, B19

### Story 6.6: CliModAdapter 缓存竞态修复（CliModAdapter Cache Race Fix）

As a 框架维护者,
I want CliModAdapter 缓存访问线程安全,
So that 并发或桥接调用不会读到不一致状态。

**验收标准（Acceptance Criteria）：**

**Given** 多个调用并发访问状态缓存
**When** act/get_state 交错执行
**Then** 缓存失效与刷新保持一致。

**FRs:** B22

### Story 6.7: start_session 进程检查点补齐（Start Session Process Checkpoints）

As a 操作者,
I want start_session 明确检查 Steam PID、Game PID 和窗口状态,
So that 启动失败能定位到具体阶段。

**验收标准（Acceptance Criteria）：**

**Given** 启动 Steam 或游戏失败
**When** start_session 返回失败
**Then** 报告失败检查点和可操作原因。

**FRs:** FR1, FR33, FR34, B23

### Story 6.8: doctor Steam 登录态与版本检查（Doctor Steam Login And Adapter Version Checks）

As a 开发者,
I want doctor 检查 Steam 登录态和适配器版本,
So that 运行前能发现常见环境问题。

**验收标准（Acceptance Criteria）：**

**Given** Steam 未登录或适配器版本不兼容
**When** 执行 autotest doctor
**Then** 输出结构化失败项和修复建议。

**FRs:** FR62, B24

## Epic 7: Beta 自然语言测试流水线（Natural Language Test Pipeline）

把 Markdown 测试规格转为可审查、可编译、可执行的 pytest 用例。

### Story 7.1: Markdown 规格审查编译运行流水线（Markdown Spec Review Compile Run Pipeline）

As a 测试作者,
I want 从 Markdown 规格生成 pytest 测试,
So that 自然语言用例可以进入自动化执行闭环。

**验收标准（Acceptance Criteria）：**

**Given** specs 目录存在 case/suite Markdown
**When** 执行 review -> compile -> run
**Then** 生成审查报告、pytest 文件，并可由 CLI 统一调度。

**FRs:** B25

### Story 7.2: 生成代码 DSL 动作覆盖（Generated DSL Action Coverage）

As a 测试作者,
I want 生成器覆盖常用游戏语义动作,
So that 生成测试不需要大量手工补代码。

**验收标准（Acceptance Criteria）：**

**Given** Markdown 步骤包含进入事件、推进对话、选择节点等动作
**When** 编译测试
**Then** 生成对应 Fluent DSL 调用或明确 TODO。

**FRs:** B25

### Story 7.3: 默认规格与用户手册（Default Specs And User Manual）

As a 新用户,
I want 默认 specs 和用户手册说明自然语言流水线,
So that 我可以快速跑通首个用例集。

**验收标准（Acceptance Criteria）：**

**Given** 新检出仓库
**When** 阅读用户手册并运行默认 specs
**Then** 能完成 review/compile/run 的最小闭环。

**FRs:** B25

### Story 7.4: 规格流水线真实回归（Spec Pipeline Real Regression）

As a 维护者,
I want 规格流水线有端到端集成回归,
So that parser、reviewer、generator 和 CLI 变更不会断裂。

**验收标准（Acceptance Criteria）：**

**Given** 示例 Markdown specs
**When** 运行 integration 测试
**Then** review 和 compile 产物语法有效，生成测试可收集。

**FRs:** B25

## Epic 8: Beta CI/CD 与修复建议（CI/CD & Repair Workflow）

把本地 CLI 能力扩展到自托管 Runner、PR 注释和失败修复建议。

### Story 8.1: CI 流水线与 PR 注释（CI Pipeline And PR Commenting）

As a CI/CD 操作者,
I want GitHub Actions 或 Azure 自托管 Runner 运行测试并回写 PR 注释,
So that 回归结果能进入代码评审流程。

**验收标准（Acceptance Criteria）：**

**Given** Windows 自托管 Runner
**When** PR 触发测试
**Then** 上传 evidence artifact 并在 PR 中给出摘要。

**FRs:** B11

### Story 8.2: Level 2 修复建议 patch.diff（Level 2 Repair Advisor Patch Diff）

As a 开发者,
I want 从 crash pack 生成修复建议和 patch.diff,
So that 失败后能快速进入人工确认的修复循环。

**验收标准（Acceptance Criteria）：**

**Given** crash pack 包含日志、截图和状态
**When** Repair Advisor 分析失败
**Then** 生成可读根因、重跑建议和隔离分支 patch.diff。

**FRs:** B10

### Story 8.3: 桌面通知（Desktop Notification）

As a 本地操作者,
I want 测试完成后收到桌面通知,
So that 长时间运行不需要一直盯着终端。

**验收标准（Acceptance Criteria）：**

**Given** 测试会话结束
**When** 生成结果摘要
**Then** 桌面通知显示 PASS/FAIL/DETERMINISTIC_FAIL 计数。

**FRs:** B13

### Story 8.4: Health Check HTTP 端点（Health Check HTTP Endpoint）

As a CI/CD 操作者,
I want 通过 HTTP 端点检查 Runner 健康,
So that 外部编排器无需 shell 登录也能判断环境就绪。

**验收标准（Acceptance Criteria）：**

**Given** health 服务启动
**When** 请求 HTTP endpoint
**Then** 返回 doctor 等价的结构化 JSON 和健康退出语义。

**FRs:** FR53, B17

## Epic 9: Beta 视觉与多人 QA（Visual & Multiplayer QA）

补齐 Beta 阶段的视觉语义判断和双 Runner 多人冒烟能力。

### Story 9.1: Visual QA Engine

As a QA 操作者,
I want OCR、OpenCV 和 VLM 组合判断画面语义,
So that 截图不只验证“非纯色”，还验证 UI 和渲染是否正确。

**验收标准（Acceptance Criteria）：**

**Given** 截图和期望 UI 状态
**When** Visual QA Engine 运行
**Then** 能识别至少 3 类 UI/渲染问题。

**FRs:** B8

### Story 9.2: 双 Runner 多人冒烟（Dual Runner Multiplayer Smoke）

As a QA 操作者,
I want 双 Runner 编排多人大厅与地图投票冒烟测试,
So that 多人功能进入 Beta 自动化覆盖。

**验收标准（Acceptance Criteria）：**

**Given** 两台 Runner 和两个账号
**When** 执行多人 smoke suite
**Then** 主机建房、客户端加入、地图投票和一场战斗通过。

**FRs:** B9

## Epic 10: Beta 技术债清理（Technical Debt Burn-down）

集中处理 MVP 留下的影响 Beta 稳定性和类型安全的技术债。

### Story 10.1: hooks 多 session 泄漏修复（Hooks Multi-Session Leak Fix）

As a 维护者,
I want pytest hooks 和 fixtures 不依赖可泄漏的模块级可变状态,
So that 多次 session 运行不会互相污染。

**验收标准（Acceptance Criteria）：**

**Given** 同一进程内多次启动测试 session
**When** 上一个 session 已 teardown
**Then** 新 session 不复用旧状态、旧 hook 或旧适配器。

**FRs:** B20

### Story 10.2: assert_that loop 生命周期修复（Assert That Loop Lifecycle Fix）

As a 测试作者,
I want assert_that 在未传入 loop 时也正确管理事件循环,
So that 同步测试不会留下未关闭 loop。

**验收标准（Acceptance Criteria）：**

**Given** 用户未向 define() 传入 loop
**When** 调用 assert_that()
**Then** 框架使用明确生命周期的 loop，并在需要时关闭。

**FRs:** B21

### Story 10.3: setup 场景自动验证（Setup Scenario Auto Validation）

As a 测试作者,
I want setup() 构造场景后自动验证状态,
So that 构造失败不会进入错误的断言阶段。

**验收标准（Acceptance Criteria）：**

**Given** setup() 执行场景构造动作
**When** 构造动作完成
**Then** 自动读取状态并验证符合预期起始条件。

**FRs:** FR18

### Story 10.4: on_error handler 类型安全（On Error Handler Type Safety）

As a 维护者,
I want on_error handler 使用严格类型签名,
So that 失败回调不会吞掉错误或产生运行时签名问题。

**验收标准（Acceptance Criteria）：**

**Given** 用户注册 on_error handler
**When** assert_that 失败
**Then** handler 接收明确类型的上下文对象。

**FRs:** FR15

### Story 10.5: `_coerce_types` type ignore 移除（Remove _coerce_types Type Ignore）

As a 维护者,
I want 移除 `_coerce_types` 中遗留的 type ignore,
So that 配置加载维持 mypy strict 零逃逸。

**验收标准（Acceptance Criteria）：**

**Given** 配置环境变量需要类型转换
**When** mypy strict 运行
**Then** 不依赖未解释的 type ignore。

**FRs:** FR37, FR39
