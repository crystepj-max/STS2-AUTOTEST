# Stage Handoff — S1 需求 → 开发

- task_id: issue-23-main-merge-protection
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/23 （labels: enhancement, sized-s）
- 交接时间：2026-08-14
- 下一阶段：开发（codex + GPT-5.6）——**第一件事读本文件与 task.yaml**

## 必读（按序）

1. `.agent-runs/issue-23-main-merge-protection/task.yaml`（含 size、分诊取证、T0–T5 依赖、scope）
2. `.agent-runs/issue-23-main-merge-protection/STATE.md`
3. `../sts2-dev-infra/agent-protocol/AGENT_CONTRACT.md` → `ROLE_DEVELOPER.md` → `QUALITY_GATES.md`

## 交接要点

- **这不是代码任务，是仓库治理任务。** 禁止改动 `src/` 与 `.github/workflows/ci-pr.yml` 的 job 逻辑
  （Issue 明确不做项）。唯一仓库内改动是新增治理文档 `docs/process/main-merge-protection.md`。
- **硬约束已取证**：私有库免费账号无原生分支保护（gh api 403 实测）。用户已拍板路径：
  **先转公开，再用原生分支保护**。
- **T0 是闸门**：转公开不可逆，执行前必须（a）敏感信息扫描（.env 已 gitignore，抽检历史与现文件），
  （b）再次取得用户明确授权后才可执行可见性变更。
- 分支保护配置要点（T1）：
  - required status check context 字符串必须是 `PR Check Summary`（与 job 显示名完全一致）；
  - `strict: true`（要求分支最新 = Issue 的「等价的最终提交验证规则」）；
  - `enforce_admins` / `required_pull_request_reviews` 按 task.yaml `decisions` 节的门禁确认值设置；
  - 配置后回读 `gh api .../branches/main/protection` 验证并归档证据。
- 验证要求（T3/T4）：失败样例（直接 push main 被拒 + check 失败的 PR 不可合并）与
  成功样例（T2 文档 PR 通过 PR Check Summary 后合并）缺一不可；证据归档
  `.agent-runs/issue-23-main-merge-protection/evidence/`。
- 治理文档必须包含：规则生效状态、紧急绕过流程（授权人 / 原因记录 / 24h 内补验证据）、验证证据链接。
- 禁止：以截图口述代替回读证据；顺手修改 CI 检查逻辑；把管理员绕过当常规流程。

## 门禁

- 本阶段（S1）已确认：强制路径 = 先转公开再用原生分支保护（用户 2026-08-14 确认）；size = S。
- 待人工门禁「需求确认」拍板的默认建议（见 task.yaml `decisions`）：
  enforce_admins=true；紧急绕过 = 文档约定；PR approvals=0。
- 待办：人工门禁「需求确认」通过后，给 Issue 打 `ready` 标签再开工。
