# STATE — issue-23-main-merge-protection

- 更新时间：2026-08-15（S4 复审第二轮修复完成：紧急绕过补验闭环 + 门禁脚本自备限时 +
  证据统一 + 自动对账门禁，PR #33 待复审）
- 阶段：复审修复完成，PR #33（chore/issue-23-review-fixes，head `1bd5e50`，
  PR Check Summary run 31845000064 success，bot 审查线程全部解决）待 Review 节点复审
- 状态机位置：`DEV_REVIEW` → `DEV_ASSIGNED`（S4 REQUEST_CHANGES 回退）→ 修复完成
  （PR #32 合入 + macOS 兼容性修复）→ S4 复审第二轮修复完成 → 待复审

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/23 ，标签 `enhancement` + `sized-s` + `ready`，
  正文已同步 T8/线程解决要求/补验记录。
- **仓库已转公开**（2026-08-14，用户双重授权 + 敏感扫描通过）——不可逆操作，见 `evidence/t0-visibility-change.md`。
- **main 分支保护已生效**（回读证据 `evidence/t1-protection-readback.json` + 复审回读 + 对账门禁实时核验）：
  - 必填检查 `PR Check Summary`，strict=true，enforce_admins=true，无审批要求，禁 force push/删除。
- **ruleset「Autotest protect」复审修复后终态**（回读证据 `evidence/t6-ruleset-readback.json` + 对账门禁实时核验）：
  - enforcement=active、target=branch、覆盖默认分支（~DEFAULT_BRANCH）；
  - 必填检查 `PR Check Summary`（strict 策略）、审批数 0；**review 线程解决要求=true（T6 恢复）**；
    **无绕过者（bypass_actors=[]、current_user_can_bypass=never）**。
- **紧急绕过流程已真实可执行并完成补验闭环**（`evidence/t7-emergency-bypass-drill.md` + `t7-post-verification.md`）：
  - 机制 = 临时改规则 → 操作 → **立即恢复（无 24h 宽限）** → 回读 → 审计；
  - 2026-08-14 演练：合并被阻断的探针 PR #31（`750ba976`）后两层保护均复原（回读无残留）；
  - 事后补验：run 31774866515（head `64ed09fc`，success，head 祖先链包含被绕过合并）在操作完成后
    约 2 小时完成，24h 期限内；绕过台账含授权/原因链接、被绕过 SHA、操作时间、恢复与补验全字段。
- **缺失检查样例已补证**（`evidence/t8-missing-check-probe.md`）：
  - 探针 PR #31 仅含 markdown（0 个检查运行），合并 HTTP 405 `Required status check "PR Check Summary" is expected.`；
  - 验收字段已拆分为 failure（T3b）/ missing（T8）两案例。
- **.env 防护已落地**：`.gitignore` 忽略 `.env`/`.env.*`（仅放行 `.env.example`），门禁脚本
  `scripts/check-env-gitignore.sh`（含 macOS 兼容性修复 + 自备限时 + 卡死/错误负例测试），AGENTS.md 已登记。
- **自动对账门禁已落地**：`scripts/check-issue23-evidence.sh`——交接/复审前核验
  `.gitignore`↔JSON、PR head↔check、Issue 正文↔实时规则、治理文档链接、绕过台账
  （授权/原因/SHA/恢复/24h 补验/门禁实质），全部外部调用自备限时。
- 治理文档 `docs/process/main-merge-protection.md` 已同步上述全部变化（含「证据对账门禁」章节）。
- 双向验证回顾：T3a 直接 push 拒绝（GH013）；T3b 失败 CI PR（#26）合并 405 + BLOCKED；
  T4 成功样例 PR #27 合并（a0673525）。

## 本轮（S4 复审第二轮）变更清单

| 工单 | 内容 | 证据 |
|---|---|---|
| S4-1 | 紧急绕过补验闭环：恢复改为立即（无 24h 宽限）；绕过台账补齐授权/原因/SHA/时间/恢复/补验；新增 `t7-post-verification.md`（run 31774866515，head 64ed09fc，合并后约 2h） | `evidence/t7-post-verification.md`、`docs/process/main-merge-protection.md` |
| S4-2 | 门禁脚本自备限时：`scripts/check-env-gitignore.sh` 每个外部调用限时（python3+psutil，超时终止进程树）；git 卡死负例 + git 128 错误负例 | `scripts/check-env-gitignore.sh`、`tests/unit/test_check_env_gitignore.py` |
| S4-3 | 证据统一：t5 JSON `/env`→`.env`、治理文档失效相对链接修复、Issue 正文同步（T8/线程=true/补验）、STATE/handoff 当前事实 | `t5-final-evidence.json`、`docs/process/main-merge-protection.md`、Issue #23 |
| S4-4 | 自动对账门禁：`scripts/check-issue23-evidence.sh`（5 组核验 + 绕过台账逐条 + 假 gh 测试套件） | `scripts/check-issue23-evidence.sh`、`tests/unit/test_check_issue23_evidence.py` |
| S4-5 | 对账门禁按 bot 复审加固：双层必填检查、enforcement=active、ruleset 覆盖默认分支、补验 run 门禁实质（PR Check Summary job）、Windows venv 解析、24h 窗口固定 0~24h | 同上（PR #33 R2–R5） |

## 风险与注意

- **转公开不可逆**：全历史公开；复审若发现泄露风险须立即报告。
- **线程解决要求已恢复**：PR #33 每轮 bot 审查线程须处理并标记解决后方可合并
  （solo 维护者可操作，t6 已记录处理约定）。
- **md-only PR 无法合并**：paths-ignore 忽略 docs/**.md 与 **.md，纯文档 PR 无 PR Check Summary；
  文档更新须附带非忽略文件（本次修复 PR 附带脚本/测试/JSON 证据触发 CI）。
- 紧急绕过不保留静态权限：任何绕过动作必须走 T7 演练的完整闭环并留审计记录。
- 对账门禁依赖 gh CLI 与带 psutil 的 Python（项目 venv）；psutil 缺失时明确报错。
- 本任务未改动 `src/` 与 `.github/workflows/`。

## 下一步

1. 复审 PR #33（S4 复审第二轮修复证据：补验闭环 / 脚本限时 / 证据统一 / 对账门禁）。
2. 复审通过后合并 PR #33 并关闭 Issue #23。
