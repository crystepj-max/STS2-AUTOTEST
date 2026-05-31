# B11 CI/CD 流水线 — Session Handoff

> 类型：设计前 handoff（brainstorming 尚未完成）
> 日期：2026-05-31
> 来源：beta-roadmap.md P3
> 下一个 session：从 brainstorming 第一步"探索项目上下文 + 提出 2-3 方案"继续

---

## 任务概述

为 STS2-AUTOTEST 搭建 GitHub Actions CI/CD 流水线，包含 PR 注释和 JUnit XML 报告。Beta 阶段对标 "从本地 CLI → CI 自动化"。

## 已有资产（可直接复用）

| 资产 | 位置 | 说明 |
|------|------|------|
| `--ci` 模式 | `cli/main.py:82` | `--ci` flag，输出紧凑 JSON，已实现 |
| JUnit XML 生成 | `evidence/packager.py:519-544` | `_generate_junit_xml()` 函数，从 SummaryJson 生成，已实现 |
| Health HTTP 端点 | `cli/health_server.py` | 纯 stdlib asyncio，`/health` `/health/live` `/health/ready` 三个端点，B17 已实现 |
| 单元测试 | `tests/unit/` | 1067 个测试，纯 mock，无外部依赖 |
| 集成测试 (CLI-only) | `tests/integration/` | 需 CLI 但不需要游戏 |
| 集成测试 (requires_game) | `tests/integration/` | 需要 Steam + 游戏运行，用 `@pytest.mark.requires_game` 标记 |
| mypy strict | 全项目 | `mypy src/sts2_autotest --strict`，当前零错误 |
| lint-imports | 全项目 | import-linter 层级隔离检查，当前零违反 |
| 项目依赖 | `pyproject.toml` | hatchling 构建，`pip install -e ".[dev]"` |

## 关键技术决策点（下一个 session 需要讨论）

### 1. Runner 类型

- **选项 A：GitHub-hosted Runner（ubuntu-latest）**
  - 优点：零维护，免费额度通常够用
  - 缺点：无法运行 `requires_game` 测试（无 Windows + Steam），只能跑单元测试 + CLI-only 集成测试
  - 适用：仅检查代码质量 + 单元/CLI-only 测试
- **选项 B：自托管 Runner（Windows 11 + Steam）**
  - 优点：可以跑全量测试包括 requires_game
  - 缺点：需要维护一台 Windows 机器，安全配置（Runner 有 shell 注入风险）
  - 适用：完整 CI 流水线
- **选项 C：混合模式（GitHub-hosted 跑 lint/test，自托管跑 game 测试）**
  - 优点：分层检查，PR 快速反馈 + 合并前全量验证
  - 缺点：管理两套 Runner

### 2. PR 注释方式

- **选项 A：GitHub Checks API + Job Summary**
  - 通过 `$GITHUB_STEP_SUMMARY` 输出 Markdown，自动渲染在 PR 下方
  - 优点：原生支持，零额外工具
- **选项 B：PR Comment Bot**
  - 用 `gh pr comment` 或 GitHub API 直接发评论
  - 优点：更灵活，可定制格式
  - 缺点：需要 `issues: write` 权限
- **选项 C：Checks API + Annotations**
  - 对每个失败/警告创建 annotation，直接显示在 PR Files Changed 对应行
  - 优点：最精细的反馈
  - 缺点：Annotations 限制 10 个/请求

### 3. 流水线触发策略

- `push` to `main` — 全量检查 + 构建
- `pull_request` to `main` — lint + 单元测试 + CLI-only 集成测试（快速反馈）
- `workflow_dispatch` — 手动触发 requires_game 测试
- `schedule` (cron) — 每夜全量回归

### 4. 测试分层执行

```
PR 触发：lint → mypy → lint-imports → 单元测试 → CLI-only 集成测试
            ↑_____ 并行失败快速反馈 _____↑
合并到 main：以上全部 + requires_game 集成测试（如有自托管 Runner）
每夜构建：全量 + 证据打包 + 长跑（4h unattended）
```

## 需要注意的约束

1. **Windows-only 组件**：B15 安全沙箱（Job Objects）、游戏控制（Steam 子进程）仅 Windows 可用。GitHub-hosted Windows Runner 费用是 Linux 的 2 倍。
2. **Steam 登录态**：`requires_game` 测试需要 Steam 已登录 + 游戏已安装。自托管 Runner 需要配置 Steam 自动登录（安全风险）。
3. **测试时长**：单元测试 ~3 秒，CLI-only 集成测试 ~30 秒，requires_game 测试 ~数分钟。
4. **macOS 开发**：当前项目在 macOS 上可以做 lint/mypy/单元测试，但不能跑游戏测试。CI 不应该假定 macOS Runner。

## 建议的 MVP 范围

1. GitHub-hosted Runner (`ubuntu-latest`) + 可选的 Windows 自托管 Runner
2. PR 触发：lint + mypy + lint-imports + 单元测试
3. push to main：上述 + CLI-only 集成测试
4. PR 注释：Job Summary Markdown + JUnit XML artifact 上传
5. 每夜构建 (cron)：全量测试（需要自托管 Runner）

## 相关文件

- `src/sts2_autotest/cli/main.py` — `--ci` flag (line 82)
- `src/sts2_autotest/evidence/packager.py` — JUnit XML 生成 (line 519-544)，artifact ZIP 导出 (line 380-443)
- `src/sts2_autotest/cli/health_server.py` — HTTP 健康检查端点（B17，已实现）
- `pyproject.toml` — 项目元数据和依赖
- `.importlinter` — 导入层级规则
- `docs/beta-roadmap.md` — B11 在 P3 (line 106)

## 下一个 session 的启动建议

```
/brainstorming B11 CI/CD 流水线 — 从 handoff 继续
```

Session 应该先审阅此 handoff，然后按照 brainstorming checklist：确认范围 → 细化方案 → 呈现设计 → 生成 spec → 进入 writing-plans。
