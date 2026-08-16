# STATE — task-008（issue #18 为自动验收外部任务建立时间边界）

- 更新时间：2026-08-16（S2 复审修复轮完成，全部审核项已修，提交 75168b1 已推送）
- 阶段：**S2 修复完成** → `S4 复审`
- 状态机位置：`S1 需求/调度` → 人工门禁 → `S2 开发` → `S3 测试` → `S4 评审/验收`

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/18 （OPEN，
  labels: bug + sized-m + ready）。
- 分支：`bugfix/issue-18-timeout-boundaries`，HEAD `75168b1`（已推送 origin；
  含 S3 遗留的 ruff I001 修复）。
- Draft PR：https://github.com/crystepj-max/STS2-AUTOTEST/pull/39（新 push 已触发 CI run）。
- 人工门禁「需求确认」三项决策（S1 遗留，S2 按分诊建议落定）：
  1. 时间上限：Ruff 600s / mypy 900s / pytest 1800s（环境变量可覆盖，用于受控验证）。
  2. 受控超时验证：CI 中新增固定「受控超时验证」步骤（2s 上限杀 300s sleep，
     psutil 断言无残留）+ 单元测试超时场景 + 脚本级环境变量注入实测。
  3. 工具模块：`.github/scripts/runner_utils.py`（与三个 baseline 脚本同目录）。

## 需求三要素（S1 判定，未变化）

| 要素 | 结论 | 来源 |
|------|------|------|
| 任务目标 | 明确 | issue「目标行为」：每个外部检查都有独立时间边界；超时失败、终止残留、保存已有输出 |
| 涉及范围 | 明确 | issue「范围」+ 分诊评论：3 个 baseline 脚本 + runner_utils.py + 测试 + ci-pr.yml；明确不做 3 项 |
| 验收标准 | 明确 | issue「完成标准」6 条，均可操作、可验证 |

## 验收标准对照（S2 修复轮后）

| # | 验收标准 | 状态 | 证据 |
|---|----------|------|------|
| 1 | 三类调用独立时间上限，超时终止 | ✅ | 三脚本接入 run_timed；实测 exit=124 |
| 2 | 超时后无残留子进程 | ✅ | 单元测试 psutil 断言 + CI 受控验证步骤；KeyboardInterrupt 分支补 wait 回收（测试抓到并修复僵尸残留） |
| 3 | CI 输出明确显示 Timeout | ✅ | `TIMEOUT: ...` stderr + summary 表 Timeout (124)；job 兜底提升至 120min 覆盖多超时最坏情形 |
| 4 | 已产生输出保留 | ✅ | 上传步骤移至 mypy 之后（HIGH 修复），mypy-check.log 现可随 artifact 保留 |
| 5 | 正常路径与 Run #47 一致 | ✅ | 判定逻辑与退出码语义未变；全量 1840 passed；增量基线 New=0 |
| 6 | 受控超时验证可恢复 | ✅ | CI 受控验证步骤 + 脚本级注入实测 |

## 集成测试判定

- **need_integration_test: true**（S1 判定不变）
- 集成验证载体：PR #39 新一轮 CI run（含受控超时验证步骤 + 三个 baseline 正常路径 +
  summary/enforce 呈现 + check-logs artifact 顺序验证）。

## 任务拆分进度（to-tickets）

| Ticket | 内容 | 状态 |
|--------|------|------|
| T1-runner-utils | runner_utils.py + 单元测试 | ✅ 完成（修复轮补 KeyboardInterrupt 测试） |
| T2-ruff-timeout | ruff baseline 接入超时 | ✅ 完成 |
| T3-mypy-timeout | mypy baseline 接入超时 | ✅ 完成 |
| T4-pytest-timeout | pytest baseline 接入超时 + 测试重写 | ✅ 完成 |
| T5-ci-presentation | ci-pr.yml 呈现 + 受控超时验证 | ✅ 完成（修复轮改上传顺序 + job 兜底） |

## 已落盘产物

- `.agent-runs/task-008/developer-handoff.md`（含修复轮章节）
- `.agent-runs/task-008/stage-handoff-s2-fix.md`（修复轮交接 → S4 复审）
- 开发报告：Gold Band round-001/开发/attempt-002/attachments/dev-report.md
- 审核报告：Gold Band round-001/审核/attempt-001/attachments/review-report.md

## 下一步

1. S4 复审：核对 PR #39 新一轮 CI run（受控超时验证、三检查正常路径、summary 呈现、
   check-logs artifact 含 mypy-check.log），按验收标准 6 条复核。
2. 复审通过后合并（或转人工验收）。
