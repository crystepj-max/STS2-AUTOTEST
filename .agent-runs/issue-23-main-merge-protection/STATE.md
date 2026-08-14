# STATE — issue-23-main-merge-protection

- 更新时间：2026-08-14（复审修复轮已合入 + 门禁脚本 macOS 兼容性修复 PR #33 待复审）
- 阶段：复审修复 PR #32 已合并入 main（7d36d25）；门禁脚本 macOS 兼容性修复 PR #33
  已推送并确认修复有效（bash 3.2 + C.UTF-8 缺陷实测复现），PR Check Summary **success**，
  已解除 Draft；Bot 审查 4 条线程意见处理中（复验回应 + 超时/bash 跳过已修复，待回复解决）
- 状态机位置：`DEV_REVIEW` → `DEV_ASSIGNED`（S4 REQUEST_CHANGES 回退）→ 修复完成（PR #32 合入）
  → 门禁脚本 macOS 兼容性修复（PR #33）→ 待复审

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/23 ，标签 `enhancement` + `sized-s` + `ready`。
- **仓库已转公开**（2026-08-14，用户双重授权 + 敏感扫描通过）——不可逆操作，见 `evidence/t0-visibility-change.md`。
- **main 分支保护已生效**（回读证据 `evidence/t1-protection-readback.json` + 复审回读）：
  - 必填检查 `PR Check Summary`，strict=true，enforce_admins=true，无审批要求，禁 force push/删除。
- **ruleset「Autotest protect」复审修复后终态**（回读证据 `evidence/t6-ruleset-readback.json`）：
  - 审批数 0；必填检查 `PR Check Summary`（strict）；**review 线程解决要求=true（T6 恢复，撤销 T5 越界降级）**；
    **无绕过者（bypass_actors=[]、current_user_can_bypass=never）**。
- **紧急绕过流程已真实可执行**（`evidence/t7-emergency-bypass-drill.md` + 7 个原始回读 JSON）：
  - 机制 = 临时改规则（ruleset 授予 Admin 绕过 + branch protection 解除 enforce_admins）→ 操作（admin 合并）→ 立即恢复 → 回读 → 审计；
  - 2026-08-14 演练：合并被阻断的探针 PR #31（`750ba976`）后两层保护均复原（回读无残留）。
- **缺失检查样例已补证**（`evidence/t8-missing-check-probe.md`）：
  - 探针 PR #31 仅含 markdown（0 个检查运行），合并 HTTP 405 `Required status check "PR Check Summary" is expected.`；
  - 验收字段已拆分为 failure（T3b）/ missing（T8）两案例（`t5-final-evidence.json` 终版）。
- **.env 防护已落地**：`.gitignore` 忽略 `.env`/`.env.*`（仅放行 `.env.example`），门禁脚本
  `scripts/check-env-gitignore.sh`（红→绿已验证），AGENTS.md 开发命令已登记。
- 治理文档 `docs/process/main-merge-protection.md` 已同步上述全部变化。
- 双向验证回顾：T3a 直接 push 拒绝（GH013）；T3b 失败 CI PR（#26）合并 405 + BLOCKED；
  T4 成功样例 PR #27 合并（a0673525）。
- **复审修复 PR #32 已合并入 main（`7d36d25`）**：T6–T8 与 .env 门禁主体随 PR #32 合入，CI PASS。
- **门禁脚本 macOS 兼容性修复 PR #33（复验确认修复真实有效）**：测试节点
  （round-001/测试/attempt-002）报告门禁脚本 macOS 兼容性缺陷并提交 `1153dce`
  （`$f`→`${f}`）。开发节点复验（bash 3.2.57 实测）**确认修复有效**：
  bash 3.2 在 `LC_CTYPE=C.UTF-8`（macOS 常见默认，pytest 子进程继承）下把 `$f` 与
  全角括号 UTF-8 首字节合并解析为变量名 `f\xef`，`set -u` 报 `unbound variable`，
  旧版脚本必然失败；`${f}` 修复版通过（`LC_CTYPE=C` 下两者均正常——早期"无语义
  差异"误判源于普通 shell 会话默认 C locale）。PR #33 保留 = 脚本修复 +
  回归测试 `tests/unit/test_check_env_gitignore.py`（强制 C.UTF-8 捕获缺陷 +
  超时 + bash 可用性跳过）。

## 本轮（复审修复）变更清单

| 工单 | 内容 | 证据 |
|---|---|---|
| T6 | ruleset `required_review_thread_resolution` false→true（撤销越界降级） | `evidence/t6-ruleset-readback.json`、`t6-ruleset-thread-restored.md` |
| T7 | 紧急绕过流程真实演练（临时改规则→操作→立即恢复→回读→审计） | `evidence/t7-emergency-bypass-drill.md` + 7 个 JSON |
| T8 | 缺失检查探针 PR #31 阻断样例（0 check-runs + 405） | `evidence/t8-missing-check-probe.md` |
| — | `.gitignore` 忽略 `.env` + `scripts/check-env-gitignore.sh` 门禁 | `.gitignore`、`scripts/check-env-gitignore.sh` |
| — | 治理文档 / AGENTS.md / STATE / handoff 同步 | `docs/process/main-merge-protection.md` 等 |
| R5 | 门禁脚本 macOS 兼容性修复（`$f`→`${f}`，bash 3.2 + C.UTF-8 多字节解析缺陷）+ 回归测试（PR #33） | `scripts/check-env-gitignore.sh`、`tests/unit/test_check_env_gitignore.py` |

## 风险与注意

- **转公开不可逆**：全历史公开；复审若发现泄露风险须立即报告。
- **线程解决要求已恢复**：后续所有 PR（含并行 issue-24 PR #30）合并前须处理并标记解决 bot/人工意见线程，
  solo 维护者可操作（t6 已记录处理约定）。
- **md-only PR 无法合并**：paths-ignore 忽略 docs/**.md 与 **.md，纯文档 PR 无 PR Check Summary；
  文档更新须附带非忽略文件（本次修复 PR 附带 .gitignore/脚本/JSON 证据触发 CI）。
- 紧急绕过不保留静态权限：任何绕过动作必须走 T7 演练的完整闭环并留审计记录。
- issue-13（PR #22）未合并，main push 检查仍失败，与本任务正交，不阻塞本任务门禁。
- 本任务未改动 `src/` 与 `.github/workflows/`；PR #33 新增 `tests/unit/test_check_env_gitignore.py`（门禁脚本回归测试）并微调脚本插值写法。

## 下一步

1. 处理 PR #33 的 4 条 Bot 审查线程（P1#1 以复现证据回应修复有效；P1#2/P2#3 已加固测试；P2#4 已修正文档表述），标记解决。
2. Review 节点复审：核对 S4 四项阻塞问题（紧急绕过可执行、.env 忽略、缺失样例证据、线程规则恢复）的修复证据 + PR #33 macOS 兼容性修复。
3. 复审通过后合并 PR #33 并关闭 Issue #23。
