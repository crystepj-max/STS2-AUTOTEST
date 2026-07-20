# STS2-AUTOTEST BETA 阶段路线图

> 维护更新：2026-07-16
> 当前状态：基础能力与跨 Agent 统一任务入口已落地；具体客户端和真实游戏链路仍有验收边界。
> 说明：下文带历史日期的测试数量和合并记录是当日快照，不作为当前数量；当前验收边界以 `docs/cross-agent-acceptance.md` 和最新测试产物为准。

## 当前状态（2026-07-16）

- 跨 Agent 统一任务入口已支持 CLI/MCP 两种接入、幂等提交、状态查询、取消、恢复和报告读取。
- 通用目标场景已落地，整章遍历复用目标场景能力，不再作为独立的专用长流程描述。
- 协议级验证已完成；具体外部 Agent 仍需各自完成一次真实“提交—查询—取报告”验收。
- 新局进入首战的真实复验已验证开局推进、地图进入和失败留证；进入战斗仍受外部控制接口版本不匹配阻塞，不能记为完整通过。

---

## 主线整合状态（2026-05-26）

已完成两个远端分支的合并入 origin/main：

| 分支 | 内容 | 冲突 | 状态 |
|------|------|------|------|
| codex/b25-natural-language-test-pipeline | B25 NL 流水线 + B19 集成测试 + Epic10 技术债 + B1 崩溃恢复 + B7 AgentAdapter | 0 冲突 | 已合并 |
| codex/epic5-runtime-control | B2 弹窗处置 + B3 无人值守 + B4 队列 + B5 进度 + B18 异步打包 + B14 能力发现 | 1 文件(agent.py) | 已合并 |

## P3 批量实现（2026-05-31 ~ 2026-06-03）

通过 feat/b11-cicd-pipeline 分支及多个 worktree 实现：

| 任务 | 内容 | 文件新增/修改 |
|------|------|--------------|
| B10 | 修复建议 | `core/repair_advisor.py`, `common/evidence.py` 新增 FailureInfo/RepairSuggestion/RepairReport, `evidence/packager.py` 集成, 单元测试 41 个 + 集成测试 4 个 |
| B11 | CI/CD 流水线 | `.github/workflows/` 4 个工作流文件, `scripts/setup-mac-runner.sh` 自托管 Runner 脚本 |
| B13 | 桌面通知 | `core/notifier.py` 三平台实现, `config/schema.py` NotificationsConfig, `common/types.py` DesktopNotifier Protocol, 单元测试 10 个 |
| B17 | Health Check (补记) | `cli/health_server.py` 已实现，B11 流水线依赖此端点 |

合并后主干通过 1221 个单元测试（含 B10/B13 新增），mypy strict 零错误。

---

## MVP 阶段完成概况

MVP 通过 BMad (ACP) 工作流完成 **4 个 Epic、26 个 Story**（Story 4.8 安全沙箱推迟到 Beta），Beta 通过分支合并和 P3 任务新增了 167 个测试，累计 **1221 个单元测试**，全程 mypy strict 零错误、lint-imports 层级隔离零违反。

| Epic | 内容 | Story | 测试增量 |
|------|------|-------|---------|
| Epic 1 | 基础与游戏控制 | 5/5 | 62 - 177 |
| Epic 2 | 测试编写与执行 | 8/8 | 177 - 406 |
| Epic 3 | 证据与可观测性 | 4/4 | 406 - 470 |
| Epic 4 | 健壮性与运维安全 | 7/8 | 470 - 804 |
| Epic 5 | 运行时控制 | 5/5 | 804 - 854 |
| Epic 6-9 | 扩展能力(部分) | - | 854 - 935 |
| B25 + Epic10 + B19 | 附加 | - | 935 - 1054 |
| B10/B11/B13 | P3 CI/CD + 修复建议 + 桌面通知 | - | 1054 - 1221 |

### MVP 遗留技术债（已清）

| 项 | 状态 | 说明 |
|----|------|------|
| CliModAdapter 真实 CLI 集成测试 | B19 已合入 | 分层 CLI-only + requires_game |
| setup() 场景构造无自动验证 | 待处理 | |
| on_error handler 类型签名松散 | Epic10 已修复 | |
| Story 1.2 _coerce_types type: ignore | Epic10 已修复 | |

---

## BETA 阶段功能清单

### 一、核心健壮性增强

| ID | 功能 | 来源 | 状态 | 说明 |
|----|------|------|------|------|
| B1 | 崩溃自动恢复 + 三级恢复策略 | PRD-P2, Epic 4.2 | 已合入 | 重启游戏-重启 Steam+游戏-停止重试，断点续跑 |
| B2 | 弹窗自动处置 | PRD-P2 | 已合入 | popup_disposal.py，分类处置 |
| B3 | 无人值守运行时 >= 4h | NFR17-Beta | 已合入 | Epic5 运行时，待真实环境 4h 长跑验收 |
| B4 | 本地测试队列 | PRD-P2 | 已合入 | 暂停/继续、队列管理 (session_queue.py) |
| B5 | 实时进度 + 暂停/继续 | PRD-P2 | 已合入 | progress.py，进度展示与流程控制 |
| B6 | 覆盖率报告 | PRD-P2 | 待实现 | 按游戏场景维度展示覆盖情况 |

### 二、扩展能力

| ID | 功能 | 状态 | 说明 |
|----|------|------|------|
| B7 | AgentAdapter | 已合入 | 接入 STS2-Agent，HTTP + MCP 双传输 |
| B8 | Visual QA Engine | 已实现（稳定版） | HTML 报告截图 OCR 辅助分析；独立 visual-qa 结果；OpenCV 低方差/过暗/过亮/不可读检查；不影响测试结果；VLM 语义审查后续扩展 |
| B9 | 多人冒烟测试 | 待实现 | 双 Runner 编排 |
| B10 | Level 2 修复建议 | 已实现 | RepairAdvisor（三层规则引擎：L1 分类匹配 + L2 堆栈解析 + L3 异常分析），集成到 EvidencePackager |
| B11 | CI/CD 流水线 | 已实现 | 4 个 GitHub Actions 工作流（PR/push-to-main/game-integration/nightly）+ 自托管 Mac Runner 设置脚本 |
| B12 | --ci 模式 + JUnit XML | 已合入 | 在 B25 分支中实现 |
| B13 | 桌面通知 | 已实现 | 零依赖平台通知器（Windows: ctypes Shell_NotifyIconW + macOS: osascript）|
| B14 | 适配器能力发现 | 已合入 | Capabilities dataclass + capabilities 属性 |

### 三、MVP 遗留 Story

| ID | 功能 | 来源 | 状态 | 说明 |
|----|------|------|------|------|
| B15 | 安全沙箱（Story 4.8） | Epic 4.8 | 待实现 | Windows Job Objects + ACL |
| B16 | 完整 SessionQueue 异步排队 | Epic 4.6 | 已实现 | session_queue.py 完整异步排队 |
| B17 | Health check HTTP 端点 | Epic 4.7 | 已实现 | health_server.py：纯 stdlib asyncio，/health /health/live /health/ready |
| B18 | Artifact ZIP 异步打包 | Epic 4.7 | 已合入 | packager.py，异步 job 等待 |

### 四、技术债修复

| ID | 项 | 状态 | 说明 |
|----|-----|------|------|
| B19 | CliModAdapter 真实 CLI 集成测试 | 已合入 | 分层 CLI-only + requires_game |
| B20 | hooks 多 session 泄漏修复 | Epic10 | 模块级可变 dict 风险 |
| B21 | assert_that loop 未关闭修复 | Epic10 | 用户未传入 loop 时的退化场景 |
| B22 | CliModAdapter 缓存竞态修复 | Epic10 | 非线程安全问题 |
| B23 | start_session 补齐检查点 | Epic10 | Steam PID/Game PID/窗口检查 |
| B24 | doctor_cmd 补齐 Steam 登录态检查 | Epic10 | 适配器版本检查 |

### 五、新增方向

| ID | 功能 | 来源 | 状态 | 说明 |
|----|------|------|------|------|
| B25 | 自然语言测试规格流水线 | docs/2026-05-16 | 已合入 | Markdown 规格-审查-编译-生成 pytest 代码-CLI 统一执行 |

---

## 剩余任务优先级

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P1 | 真实环境验收补跑（B25 端到端 + Epic5 4h 长跑） | 待执行 |
| P2 | B15 安全沙箱 | 排队 |
| P3 | 无（B10/B11/B13/B17 已完成） | 已实现 |
| P4 | B9 多人冒烟 | 排队 |
| P5 | B6 覆盖率报告、Retrospectives、最终验收记录 | 排队 |

---

## BETA 阶段对比 MVP

| 维度 | MVP | BETA |
|------|-----|------|
| 定调 | 核心功能跑通（验证环闭合） | 健壮性 + 自动化程度提升 |
| 架构 | CLI 适配器（同步） | 双适配器（CLI + Agent HTTP/MCP） |
| 崩溃处理 | 终止后续用例 | 三级恢复 + 断点续跑 |
| 视觉 | 语法级截图校验（纯色检测） | OCR + OpenCV 截图辅助审查已稳定；VLM 语义审查保留为后续扩展 |
| 弹窗 | 无处理 | 95% 自动处置（已实现） |
| 编成 | 单人单机 | 双 Runner 多人冒烟（P4） |
| CI/CD | 本地 CLI | GitHub Actions + PR 注释 + HTTP 端点 + 4 个工作流 |
| 测试 | 935 单元测试（全 mock） | 1221 单元测试 + 真实 CLI 集成测试 |
| 无人值守 | >= 2h | >= 4h（已实现，待真实验收） |

## Epic 5 验证记录（2026-05-25）

- Mock 无人值守验证命令：python -X utf8 -m pytest tests/integration/test_epic5_unattended.py -q --tb=short --basetemp=.pytest-tmp-epic5-5-3
- 结果：1 passed in 0.03s
- 真实四小时验收待补跑：需在 Steam 已登录、游戏和 STS2-Cli-Mod 均稳定的 Windows 环境执行：

  autotest run --all --timeout 14400 --no-resume
