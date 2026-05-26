# Epic Story Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 中文说明：执行本计划时，必须逐 Story 推进；每个 Story 结束前运行单元测试、mypy strict 和 import-linter。

**Goal:** 梳理 STS2-AUTOTEST 当前 Epic 与 Story 状态，并给出从当前 Beta 进度到可稳定验收的执行顺序。

**Architecture:** 当前主线已经从 MVP 进入 Beta。B25 自然语言测试流水线框架侧已经基本收口，仅保留真实 Steam/游戏恢复后的 case、suite、`autotest run --all` 端到端 PASS 验证；执行策略是先清掉剩余技术债，再补齐运行控制、CI/CD、安全沙箱与视觉/多人 QA。每个 Story 都保持现有层级隔离，不新增跨层 import。

**Tech Stack:** Python 3.11+、pytest、mypy strict、import-linter、Click CLI、pydantic v2、STS2-Cli-Mod、STS2-Agent。

---

## 2026-05-21 更新

B25 自然语言测试流水线框架侧待办已经基本收口。当前不再把 B25 作为开发主线，只保留 `TODO-B25-STEAM-REAL-RUN`：真实 Steam/游戏链路恢复后，补跑真实 `case`、真实 `suite`、`autotest run --all` 的端到端 PASS 验证。

## 2026-05-24 更新

当前主工作树位于 `codex/b25-natural-language-test-pipeline`，本地仍有 Epic 5/B25 并行未提交改动。Epic 10 技术债清理已经在独立分支 `codex/epic10-tech-debt-local` 提交并推送，提交为 `9f0885e 收口 Epic 10 技术债`；2026-05-24 抓取远端后确认，`9f0885e` 已通过 PR 合入 `origin/main`，且 `origin/codex/b25-natural-language-test-pipeline` 也已经包含该提交。因此 Epic 10 在远端主线口径下为 `done`。

B25 口径同步更新：框架侧视为完成，`sprint-status.yaml` 中 Epic 7 可标记为 `done`；真实 Steam/游戏端到端 PASS 仍保留为独立验收 TODO，不阻塞 Epic 5 继续推进。

## 当前事实来源

- MVP Epic/Story 来源：`_bmad-output/planning-artifacts/epics.md`
- Beta Epic/Story 来源：`_bmad-output/planning-artifacts/beta-epics.md`
- 当前状态来源：`_bmad-output/implementation-artifacts/sprint-status.yaml`
- 架构执行顺序来源：`_bmad-output/planning-artifacts/architecture.md`
- Beta 路线图来源：`docs/beta-roadmap.md`
- B25 剩余项来源：`docs/natural-language-testing/2026-05-20-b25-remaining-work.md`

## 总体执行顺序

1. Epic 10 已合并；并行 Epic 5 工作继续基于已包含 `9f0885e` 的远端分支推进，本地脏工作树不做强制快进。
2. 完成 Beta 阶段运行健壮性：Epic 5。
3. 补齐 Beta 安全与运维边界：Epic 4.8、Epic 8。
4. 推进视觉与多人 QA：Epic 9。
5. 真实 Steam/游戏链路恢复后，单独执行 B25 真实端到端 PASS 验证。
6. 每完成一个 Story，执行固定质量门：`python -m pytest tests/unit/ -q --tb=short`、`mypy src/sts2_autotest --strict`、`lint-imports`。

## 状态标记说明

| 状态 | 含义 |
|------|------|
| done | Story 已完成，可按需回归 |
| framework-done | 框架侧已经收口，仅保留真实环境验证或运行记录更新 |
| review | 实现已提交/推送，等待 PR、代码审查或合并进当前主线 |
| in-progress | 当前仍有框架侧开发或技术债未收口 |
| backlog | 尚未开始 |

## MVP Epic 对应 Story

| Epic | Story | 当前状态 | 执行结论 |
|------|-------|----------|----------|
| Epic 1 基础与游戏控制 | 1.1 项目脚手架与共享数据模型 | done | 仅保留回归验证 |
| Epic 1 基础与游戏控制 | 1.2 配置系统 | done | 仅保留回归验证 |
| Epic 1 基础与游戏控制 | 1.3 适配器协议与 CliModAdapter | done | 由 Epic 6 真实 CLI 测试继续兜底 |
| Epic 1 基础与游戏控制 | 1.4 状态引擎与转移验证 | done | 由 B25 状态护栏继续兜底 |
| Epic 1 基础与游戏控制 | 1.5 Steam 与游戏进程控制器 | done | 由 Epic 5/6 长跑和真实启动链路继续验证 |
| Epic 2 测试编写与执行 | 2.1 测试编排引擎核心 | done | 由 B25 和 B1 恢复流继续验证 |
| Epic 2 测试编写与执行 | 2.2 动作模型与游戏命令执行 | done | 由 B25 生成动作覆盖继续扩展 |
| Epic 2 测试编写与执行 | 2.3 Fluent API | done | 由 Epic 7/10 类型安全继续增强 |
| Epic 2 测试编写与执行 | 2.4 测试数据 Fixture | done | 仅保留回归验证 |
| Epic 2 测试编写与执行 | 2.5 pytest 插件与异步桥接 | done | 由 Epic 10 lifecycle 修复继续兜底 |
| Epic 2 测试编写与执行 | 2.6 自定义 pytest Markers 与生命周期钩子 | done | 由 Epic 10 hooks 泄漏修复继续兜底 |
| Epic 2 测试编写与执行 | 2.7 测试隔离与资源清理 | done | 由 Epic 5 长跑验证继续兜底 |
| Epic 2 测试编写与执行 | 2.8 CLI 入口点 | done | 由 Epic 7/8 CLI 扩展继续验证 |
| Epic 3 证据与可观测性 | 3.1 截图系统 | done | 由 Epic 9 视觉 QA 继续增强 |
| Epic 3 证据与可观测性 | 3.2 日志采集器 | done | 由 Epic 8/9 失败诊断继续增强 |
| Epic 3 证据与可观测性 | 3.3 证据打包器 | done | 由 Epic 5.7/8.1 ZIP 与 CI 产物继续增强 |
| Epic 3 证据与可观测性 | 3.4 指标收集器 | done | 由 Epic 8 health endpoint 继续增强 |
| Epic 4 健壮性与运维安全 | 4.1 环境预检系统 | done | 由 Epic 6.8 doctor 增强继续兜底 |
| Epic 4 健壮性与运维安全 | 4.2 恢复策略 | done | B1 已完成，后续由 4h 长跑验证 |
| Epic 4 健壮性与运维安全 | 4.3 僵尸会话检测与终止 | done | 后续由 Epic 5.3 长跑验证 |
| Epic 4 健壮性与运维安全 | 4.4 边界情况处理与优雅降级 | done | 后续由 Epic 5/8 真实场景验证 |
| Epic 4 健壮性与运维安全 | 4.5 进度持久化与断点续跑 | done | 后续由 Epic 5.5 暂停/继续增强 |
| Epic 4 健壮性与运维安全 | 4.6 并发控制与会话队列 | done | 后续由 Epic 5.4 完整队列语义增强 |
| Epic 4 健壮性与运维安全 | 4.7 CI/CD 健康检查与产物打包 | done | 后续由 Epic 8 HTTP 与 CI 增强 |
| Epic 4 健壮性与运维安全 | 4.8 安全沙箱 | backlog | Beta 阶段执行，建议排在 Epic 8 前后 |

## Beta Epic 对应 Story

| Epic | Story | 当前状态 | 推荐顺序 |
|------|-------|----------|----------|
| Epic 5 运行健壮性与操作者控制 | 5.1 B1 崩溃三级恢复 | done | 已完成，作为长跑验证前置 |
| Epic 5 运行健壮性与操作者控制 | 5.2 B2 弹窗自动处置 | backlog | 5 |
| Epic 5 运行健壮性与操作者控制 | 5.3 B3 四小时无人值守运行验证 | backlog | 9 |
| Epic 5 运行健壮性与操作者控制 | 5.4 B4/B16 本地测试队列暂停继续 | backlog | 6 |
| Epic 5 运行健壮性与操作者控制 | 5.5 B5 实时进度暂停继续 | backlog | 7 |
| Epic 5 运行健壮性与操作者控制 | 5.6 B6 游戏场景覆盖率报告 | backlog | 8 |
| Epic 5 运行健壮性与操作者控制 | 5.7 B18 异步 Artifact ZIP 打包 | backlog | 4 |
| Epic 6 适配器与真实环境集成 | 6.1 B7.1 AgentAdapter HTTP 基线 | done | 仅回归 |
| Epic 6 适配器与真实环境集成 | 6.2 B7.2 AgentAdapter MCP-native 控制 | done | 仅回归 |
| Epic 6 适配器与真实环境集成 | 6.3 B14 适配器能力发现契约 | done | 仅回归 |
| Epic 6 适配器与真实环境集成 | 6.4 B19.1 CliModAdapter 真实 CLI 集成测试 | done | 真实 CLI 回归入口 |
| Epic 6 适配器与真实环境集成 | 6.5 B19.2 真实游戏运行态 CLI 冒烟 | done | 真实游戏回归入口 |
| Epic 6 适配器与真实环境集成 | 6.6 B22 CliModAdapter 缓存竞态修复 | done | 仅回归 |
| Epic 6 适配器与真实环境集成 | 6.7 B23 start_session 进程检查点补齐 | done | 仅回归 |
| Epic 6 适配器与真实环境集成 | 6.8 B24 doctor Steam 登录态与版本检查 | done | 仅回归 |
| Epic 7 自然语言测试流水线 | 7.1 B25.1 Markdown 规格审查编译运行流水线 | done | 仅回归 |
| Epic 7 自然语言测试流水线 | 7.2 B25.2 生成代码 DSL 动作覆盖 | framework-done | 真实游戏恢复后回归 |
| Epic 7 自然语言测试流水线 | 7.3 B25.3 默认规格与用户手册 | framework-done | 真实游戏恢复后回归 |
| Epic 7 自然语言测试流水线 | 7.4 B25.4 规格流水线真实回归 | framework-done | `TODO-B25-STEAM-REAL-RUN`：真实 case/suite/run --all PASS |
| Epic 8 CI/CD 与修复建议 | 8.1 B11 CI 流水线与 PR 注释 | backlog | 11 |
| Epic 8 CI/CD 与修复建议 | 8.2 B10 Level 2 修复建议 patch.diff | backlog | 12 |
| Epic 8 CI/CD 与修复建议 | 8.3 B13 桌面通知 | backlog | 14 |
| Epic 8 CI/CD 与修复建议 | 8.4 B17 Health Check HTTP 端点 | backlog | 10 |
| Epic 9 视觉与多人 QA | 9.1 B8 Visual QA Engine | backlog | 15 |
| Epic 9 视觉与多人 QA | 9.2 B9 双 Runner 多人冒烟 | backlog | 16 |
| Epic 10 技术债清理 | 10.1 B20 hooks 多 session 泄漏修复 | done | 已合入远端主线：`9f0885e` |
| Epic 10 技术债清理 | 10.2 B21 assert_that loop 生命周期修复 | done | 已合入远端主线：`9f0885e` |
| Epic 10 技术债清理 | 10.3 B26 setup 场景自动验证 | done | 已合入远端主线：`9f0885e` |
| Epic 10 技术债清理 | 10.4 B27 on_error handler 类型安全 | done | 已合入远端主线：`9f0885e` |
| Epic 10 技术债清理 | 10.5 B28 `_coerce_types` type ignore 移除 | done | 已合入远端主线：`9f0885e` |

---

## Milestone 1: 收敛 Epic 10，登记 B25 真实端到端验证

**目标：** B25 框架侧待办已经基本收口，不再作为当前开发阻塞；本阶段只清掉 Epic 10 技术债，并记录 B25 唯一保留的真实 Steam/游戏端到端 PASS 验证。

**Files:**
- Modify: `docs/superpowers/plans/2026-05-21-epic-story-execution-plan.md`
- Modify: `_bmad-output/implementation-artifacts/sprint-status.yaml`
- Modify: `src/sts2_autotest/core/orchestrator.py`
- Modify: `src/sts2_autotest/dsl/fluent.py`
- Modify: `src/sts2_autotest/pytest_plugin/hooks.py`
- Modify: `src/sts2_autotest/config/loader.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_action_execution.py`
- Test: `tests/unit/test_fluent_api.py`
- Test: `tests/unit/test_pytest_plugin.py`
- Test: `tests/unit/test_config_schema.py`

- [x] **Step 1: 登记 `TODO-B25-STEAM-REAL-RUN`**

框架侧已经完成后，保留真实环境恢复后的验证任务：至少 1 个真实 `case` PASS、至少 1 个真实 `suite` PASS、至少 1 次 `autotest run --all` 真实端到端完成。真实 Steam/游戏不可用时不阻塞后续 Beta 框架开发。恢复后验收命令：

```powershell
python -m pytest tests/integration/ -m requires_game -q --tb=short
```

- [ ] **Step 2: 可选回归 B25 框架侧闭环**

该步骤不是 B25 开发待办，只是在后续大改前后确认 review、compile、run 的框架侧闭环仍可用。验收命令：

```powershell
python -m pytest tests/unit/test_code_generator.py tests/unit/test_fluent_api.py tests/unit/test_default_specs.py tests/unit/test_cli_spec_commands.py -q --tb=short
```

- [x] **Step 3: 完成 Story 10.1-10.5**

一次性清掉 Beta 技术债：hooks 多 session 泄漏、`assert_that` loop 生命周期、setup 场景自动验证、on_error handler 类型安全、`_coerce_types` type ignore 移除。每项必须带对应单元测试。验收命令：

2026-05-24 状态：已在 `codex/epic10-tech-debt-local` 提交并推送，提交为 `9f0885e 收口 Epic 10 技术债`。抓取远端后确认该提交已进入 `origin/main` 与 `origin/codex/b25-natural-language-test-pipeline`，Epic 10 转为 `done`。

```powershell
python -m pytest tests/unit/test_pytest_plugin.py tests/unit/test_fluent_api.py tests/unit/test_config_schema.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

## Milestone 2: 完成 Epic 5 运行控制

**目标：** 把 Beta 的运行体验从“可恢复”推进到“可长时间值守、可暂停继续、可追踪覆盖”。

**Files:**
- Modify: `src/sts2_autotest/core/recovery.py`
- Modify: `src/sts2_autotest/core/progress.py`
- Modify: `src/sts2_autotest/core/session_queue.py`
- Modify: `src/sts2_autotest/core/orchestrator.py`
- Modify: `src/sts2_autotest/evidence/packager.py`
- Modify: `src/sts2_autotest/evidence/metrics.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_recovery_strategy.py`
- Test: `tests/unit/test_progress.py`
- Test: `tests/unit/test_session_queue.py`
- Test: `tests/unit/test_orchestrator.py`
- Test: `tests/unit/test_packager.py`
- Test: `tests/unit/test_metrics.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Story 5.7 异步 Artifact ZIP 打包**

先拆出打包异步化，降低后续长跑验证的尾部阻塞风险。验收命令：

```powershell
python -m pytest tests/unit/test_packager.py tests/unit/test_orchestrator.py -q --tb=short
```

- [ ] **Step 2: Story 5.2 弹窗自动处置**

为 Steam 更新、EULA、崩溃弹窗建立可配置识别与降级行为；崩溃弹窗保留现场，不做破坏性关闭。验收命令：

```powershell
python -m pytest tests/unit/test_recovery_strategy.py tests/unit/test_steam.py -q --tb=short
```

- [ ] **Step 3: Story 5.4 与 5.5 队列暂停继续、实时进度暂停继续**

统一 `SessionQueue` 与 `ProgressStore` 的暂停/继续语义，让 CLI 输出、进度持久化和运行状态一致。验收命令：

```powershell
python -m pytest tests/unit/test_session_queue.py tests/unit/test_progress.py tests/unit/test_cli.py -q --tb=short
```

- [ ] **Step 4: Story 5.6 游戏场景覆盖率报告**

从已有执行结果和 suite summary 提取场景维度覆盖，输出本地 JSON/Markdown 报告。验收命令：

```powershell
python -m pytest tests/unit/test_metrics.py tests/unit/test_packager.py -q --tb=short
```

- [ ] **Step 5: Story 5.3 四小时无人值守运行验证**

在真实环境可用时执行 marked integration；真实游戏不可用时保留 mock 长跑回归作为最低门槛。验收命令：

```powershell
python -m pytest tests/integration/ -m requires_game -q --tb=short
```

## Milestone 3: 安全、CI/CD 与修复建议

**目标：** 补齐 Beta 对外部运行环境的安全边界、健康检查端点、CI 注释和修复建议产物。

**Files:**
- Modify: `src/sts2_autotest/core/steam.py`
- Modify: `src/sts2_autotest/core/precheck.py`
- Modify: `src/sts2_autotest/core/workspace.py`
- Modify: `src/sts2_autotest/evidence/packager.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Create or Modify: `tools/`
- Test: `tests/unit/test_steam.py`
- Test: `tests/unit/test_precheck.py`
- Test: `tests/unit/test_workspace.py`
- Test: `tests/unit/test_packager.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Story 4.8 安全沙箱**

实现 Windows Job Objects + ACL 的 Beta 版本，保留 localhost only 与输出目录白名单约束。验收命令：

```powershell
python -m pytest tests/unit/test_steam.py tests/unit/test_workspace.py -q --tb=short
```

- [ ] **Step 2: Story 8.4 Health Check HTTP 端点**

把 `autotest doctor` 能力扩展为本地 HTTP health endpoint，默认只绑定 localhost。验收命令：

```powershell
python -m pytest tests/unit/test_precheck.py tests/unit/test_cli.py -q --tb=short
```

- [ ] **Step 3: Story 8.1 CI 流水线与 PR 注释**

生成自托管 Runner 使用的 CI 脚本与结构化输出；PR 注释读取 evidence summary，不直接解析日志散文。验收命令：

```powershell
python -m pytest tests/unit/test_packager.py tests/unit/test_cli.py -q --tb=short
```

- [ ] **Step 4: Story 8.2 Level 2 修复建议 patch.diff**

从 crash pack 和语义失败报告生成 `patch.diff` 建议产物；不能自动合并。验收命令：

```powershell
python -m pytest tests/unit/test_spec_reviewer.py tests/unit/test_packager.py -q --tb=short
```

- [ ] **Step 5: Story 8.3 桌面通知**

通知只作为运行完成后的本地辅助，不参与测试结果判定。验收命令：

```powershell
python -m pytest tests/unit/test_cli.py -q --tb=short
```

## Milestone 4: 视觉与多人 QA

**目标：** 在核心链路稳定之后，补齐视觉语义审查和双 Runner 多人冒烟。

**Files:**
- Modify: `src/sts2_autotest/evidence/capture.py`
- Modify: `src/sts2_autotest/evidence/metrics.py`
- Modify: `src/sts2_autotest/core/session_queue.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_capture.py`
- Test: `tests/unit/test_metrics.py`
- Test: `tests/unit/test_session_queue.py`

- [ ] **Step 1: Story 9.1 Visual QA Engine**

在现有截图有效性校验之后增加 OCR/OpenCV/VLM 预留接口，MVP 仍保持无外网依赖的本地可测路径。验收命令：

```powershell
python -m pytest tests/unit/test_capture.py tests/unit/test_metrics.py -q --tb=short
```

- [ ] **Step 2: Story 9.2 双 Runner 多人冒烟**

基于 `SessionQueue` 与互斥锁实现双 Runner 编排，不绕过 Steam 单账号限制。验收命令：

```powershell
python -m pytest tests/unit/test_session_queue.py tests/unit/test_cli.py -q --tb=short
```

## 全局质量门

每个 Story 结束前必须执行：

```powershell
python -m pytest tests/unit/ -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

涉及真实游戏、真实 STS2-Cli-Mod 或 Steam 的 Story 额外执行：

```powershell
python -m pytest tests/integration/ -q --tb=short
```

如果真实游戏环境不可用，必须在 Story 记录中说明跳过原因，并至少运行对应 mock integration 或 focused unit suite。

## 风险与处理顺序

| 风险 | 处理 |
|------|------|
| 当前工作树存在大量未提交变更 | 每个 Story 开始前先执行 `git status --short`，只改当前 Story 文件 |
| B25 真实 Steam 链路暂缓 | 框架侧已收口，真实游戏 PASS 验证独立排队 |
| 安全沙箱可能影响真实游戏启动 | 先在单元测试和 mock integration 验证，再跑 requires_game |
| 视觉 QA 可能引入重依赖 | 第一版使用可选依赖和能力发现，不阻断核心测试 |
| CI/CD 与本地 runner 环境差异 | CI 脚本只面向 Windows 自托管 Runner，doctor endpoint 输出结构化 JSON |

## 执行检查清单

- [x] Epic 7 框架侧 Story 标记为完成，仅保留真实 Steam/游戏端到端 PASS 验证 TODO。
- [x] Epic 10 技术债清单全部转为 done。
- [ ] Epic 5 除已完成 B1 外全部 Story 转为 done。
- [ ] Story 4.8 安全沙箱转为 done。
- [ ] Epic 8 全部 Story 转为 done。
- [ ] Epic 9 全部 Story 转为 done。
- [ ] `sprint-status.yaml` 与实际完成状态一致。
- [ ] `docs/beta-roadmap.md` 更新完成状态和剩余风险。
- [ ] 全局质量门通过。
