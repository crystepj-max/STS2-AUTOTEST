# STS2-AUTOTEST BETA 阶段路线图

> 生成日期：2026-05-17
> 基于 MVP 阶段（Epic 1-4）完成情况与 PRD Beta 功能集编制

---

## MVP 阶段完成概况

MVP 通过 BMad (ACP) 工作流完成 **4 个 Epic、26 个 Story**（Story 4.8 安全沙箱推迟到 Beta），产出 **935 个单元测试**，全程 mypy strict 零错误、lint-imports 层级隔离零违反。

| Epic | 内容 | Story | 测试增量 |
|------|------|-------|---------|
| Epic 1 | 基础与游戏控制 | 5/5 | 62 → 177 |
| Epic 2 | 测试编写与执行 | 8/8 | 177 → 406 |
| Epic 3 | 证据与可观测性 | 4/4 | 406 → 470 |
| Epic 4 | 健壮性与运维安全 | 7/8 | 470 → 804 |

### MVP 遗留技术债

| 优先级 | 项 | 说明 |
|--------|----|------|
| **最高** | CliModAdapter 真实 CLI 集成测试 | 全部测试基于 mock，零集成测试 |
| 中 | setup() 场景构造无自动验证 | 构造后未自动验证状态符合预期 |
| 低 | on_error handler 类型签名松散 | 类型安全不足 |
| 低 | Story 1.2 `_coerce_types` type: ignore | mypy 逃逸 |

---

## BETA 阶段功能清单

### 一、核心健壮性增强

| ID | 功能 | 来源 | 说明 |
|----|------|------|------|
| B1 | 崩溃自动恢复 + 三级恢复策略 | PRD-P2, Epic 4.2 | MVP 崩溃终止后续用例；Beta 升级为 重启游戏→重启 Steam+游戏→停止重试，断点续跑 |
| B2 | 弹窗自动处置 | PRD-P2 | 覆盖 95% 已识别弹窗类型，崩溃弹窗保留现场 |
| B3 | 无人值守运行时 ≥ 4h | NFR17-Beta | 从 MVP 的 2h 提升到 4h |
| B4 | 本地测试队列 | PRD-P2 | 暂停/继续、队列管理 |
| B5 | 实时进度 + 暂停/继续 | PRD-P2 | 进度展示与流程控制 |
| B6 | 覆盖率报告 | PRD-P2 | 按游戏场景维度展示覆盖情况 |

### 二、扩展能力

| ID | 功能 | 说明 |
|----|------|------|
| **B7** | **AgentAdapter** | **接入 STS2-Agent（HTTP API + MCP Server），MCP-native 控制** |
| B8 | Visual QA Engine | OCR + OpenCV + VLM 视觉审查 |
| B9 | 多人冒烟测试 | 双 Runner 编排 |
| B10 | Level 2 修复建议 | crash pack → patch.diff |
| B11 | CI/CD 流水线 | GitHub Actions / Azure 自托管 Runner，PR 注释 |
| B12 | `--ci` 模式 + JUnit XML 逐用例输出 | 结构化日志、关闭进度条 |
| B13 | 桌面通知 | 运行完成后通知 |
| B14 | `get_capabilities()` 适配器能力发现 | 运行时动态能力发现 |

### 三、MVP 遗留 Story

| ID | 功能 | 来源 | 说明 |
|----|------|------|------|
| B15 | 安全沙箱（Story 4.8） | Epic 4.8 | Windows Job Objects + ACL 完整实现 |
| B16 | 完整 SessionQueue 异步排队 | Epic 4.6 简化 | MVP 仅锁检查，Beta 完整异步排队 |
| B17 | Health check HTTP 端点 | Epic 4.7 简化 | 扩展 CLI doctor 为 HTTP 端点 |
| B18 | Artifact ZIP 异步打包 | Epic 4.7 优化 | MVP 在 on_session_end 中同步执行 |

### 四、技术债修复

| ID | 项 | 说明 |
|----|-----|------|
| B19 | CliModAdapter 真实 CLI 集成测试 | 分层为 CLI-only 与 requires_game 两组；CLI-only 可在仅安装 sts2.exe 的环境运行，requires_game 用于验证真实游戏/Mod 链路 |
| B20 | hooks 多 session 泄漏修复 | 模块级可变 dict 风险 |
| B21 | assert_that loop 未关闭修复 | 用户未传入 loop 时的退化场景 |
| B22 | CliModAdapter 缓存竞态修复 | 非线程安全问题 |
| B23 | start_session 补齐检查点 | 增加 Steam PID/Game PID/窗口检查 |
| B24 | doctor_cmd 补齐 Steam 登录态检查 | 增加适配器版本检查 |

### 五、新增方向（已讨论确认）

| ID | 功能 | 来源 | 说明 |
|----|------|------|------|
| B25 | 自然语言测试规格流水线 | docs/2026-05-16 | Markdown 规格→审查→编译→生成 pytest 代码→CLI 统一执行 |

---

## BETA 阶段对比 MVP

| 维度 | MVP | BETA |
|------|-----|------|
| 定调 | 核心功能跑通（验证环闭合） | 健壮性 + 自动化程度提升 |
| 架构 | CLI 适配器（同步） | 双适配器（CLI + Agent HTTP） |
| 崩溃处理 | 终止后续用例 | 三级恢复 + 断点续跑 |
| 视觉 | 语法级截图校验（纯色检测） | OCR + OpenCV + VLM 语义审查 |
| 弹窗 | 无处理 | 95% 自动处置 |
| 编成 | 单人单机 | 双 Runner 多人冒烟 |
| CI/CD | 本地 CLI | GitHub Actions + PR 注释 + HTTP 端点 |
| 测试 | 935 单元测试（全 mock） | 需补齐真实 CLI 集成测试 |
| 无人值守 | ≥ 2h | ≥ 4h |
## Epic 5 验证记录（2026-05-25）

- Mock 无人值守验证命令：`python -X utf8 -m pytest tests/integration/test_epic5_unattended.py -q --tb=short --basetemp=.pytest-tmp-epic5-5-3`
- 结果：`1 passed in 0.03s`
- 覆盖范围：20 个 mock case 连续运行，验证框架层未产生 crash，作为四小时真实长跑前的快速回归哨兵。
- 真实四小时验证：本次未启动真实 Steam/Slay the Spire 2 长跑；需在 Steam 已登录、游戏和 STS2-Cli-Mod 均稳定可用的 Windows 本机环境单独运行 `autotest run --all --timeout 14400 --no-resume` 或 `python -m pytest tests/integration/ -m requires_game --durations=20 -q --tb=short` 并记录 evidence pack。
