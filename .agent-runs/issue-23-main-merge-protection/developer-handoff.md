# Developer Handoff: issue-23-main-merge-protection（复审修复轮）

- 开发阶段：2026-08-14（第一轮：codex + GPT-5.6；复审修复轮：开发节点 attempt-002；
  macOS 兼容性补充修复轮：开发节点 attempt-003，PR #33；
  S4 复审第二轮修复轮：开发节点 attempt-004，PR #33 R2–R5，2026-08-14~15）
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/23
- 状态：S4 复审 REQUEST_CHANGES 的两轮阻塞问题已全部修复（T6–T8 + .env 门禁 +
  补验闭环 + 脚本自备限时 + 证据统一 + 自动对账门禁），复审修复 PR #32 已合并入 main
  （`7d36d25`）；PR #33 修复完成（PR Check Summary success，run 31856341611，
  head 0199d96，bot 审查线程全部解决）待复审

## 实现摘要

### 第一轮（T0–T5，已合入 main）

- T0 敏感扫描 + 转公开（双重授权）；T1 分支保护 + ruleset 悬空修正；T2 治理文档；
  T3 失败样例（T3a 直接 push 拒绝 / T3b 失败 CI 合并被禁）；T4 成功样例（PR #27 合并 a0673525）；
  T5 收尾（PR #28/#29 合并，Issue 回填）。
- 详见 `stage-handoff-s2.md` 与上一版 handoff（git 历史可查）。

### 复审修复轮（T6–T8 + 门禁，本 PR 合入）

Review（S4）判定四项阻塞问题，本轮逐一闭环：

| 复审阻塞问题 | 本轮修复 | 证据 |
|---|---|---|
| ① 紧急绕过流程不可执行（`bypass_actors=[]`、`current_user_can_bypass=never`，文档所称机制不存在） | 流程改为「临时改规则→操作→立即恢复→回读→审计」并**真实演练**：临时授予 ruleset Admin 绕过（pull_request 模式）+ 临时解除 branch protection enforce_admins → `gh pr merge --admin` 合并被阻断的探针 PR #31（`750ba976`）→ 立即恢复 → 回读无残留 | `evidence/t7-emergency-bypass-drill.md` + 7 个原始 JSON |
| ② `.env` 未被忽略（AGENTS.md/task.yaml 声称已忽略，实际 `.gitignore` 无条目；本地 `?? .env`） | `.gitignore` 新增 `.env`/`.env.*`（仅放行 `.env.example`）；新增门禁脚本 `scripts/check-env-gitignore.sh`（红→绿验证：修复前 FAIL→修复后 PASS）；AGENTS.md 开发命令登记 | `.gitignore`、`scripts/check-env-gitignore.sh` |
| ③ 「失败或缺失检查均阻断」只验证了失败场景 | 新增纯 markdown 探针 PR #31（命中 paths-ignore，0 个检查运行）→ 合并 HTTP 405 `Required status check "PR Check Summary" is expected.`；验收字段拆分为 failure/missing 两案例 | `evidence/t8-missing-check-probe.md`、`t5-final-evidence.json` 终版 |
| ④ 未经授权关闭「审查意见线程必须解决」（ruleset `required_review_thread_resolution=false`） | 恢复为 `true`（PUT 仅改此字段，回读确认）；线程处理约定：bot/人工线程逐条处理并标记解决，solo 维护者可操作 | `evidence/t6-ruleset-thread-restored.md`、`t6-ruleset-readback.json` |

### 门禁脚本 macOS 兼容性修复轮（PR #33，attempt-003）——复验确认修复真实有效

测试节点（round-001/测试/attempt-002）报告门禁脚本 `scripts/check-env-gitignore.sh`
存在 macOS 兼容性缺陷并提交 `1153dce`（`$f` → `${f}`）。开发节点复验（bash 3.2.57 实测）**确认修复真实有效**：

> **根因（可复现）**：macOS 自带 bash 3.2 在 `LC_CTYPE=C.UTF-8`（macOS 常见默认，
> pytest 子进程亦继承）下存在多字节解析缺陷——`$f` 后紧跟全角括号 `（`（UTF-8 首字节
> 0xEF）时，bash 3.2 把 `f\xef` 合并解析为变量名，`set -u` 下报
> `unbound variable`，门禁脚本第 3 项检查循环直接失败。
> 实测：`LC_CTYPE=C.UTF-8 bash scripts/check-env-gitignore.sh` → 旧版
> `line 42: f�: unbound variable`；`${f}` 修复版全部通过。
> （`LC_CTYPE=C` 下两者均正常——这解释了早期"无语义差异"的误判来源：
> 普通 shell 会话默认 `LC_CTYPE=C`，而 pytest 子进程继承 `C.UTF-8`。）

PR #33 内容：

| 变更 | 内容 | 证据 |
|---|---|---|
| 脚本兼容性修复 | `scripts/check-env-gitignore.sh` 两处 echo `$f` → `${f}`（界定变量名边界，消除 bash 3.2 + C.UTF-8 多字节解析歧义） | `scripts/check-env-gitignore.sh` |
| 门禁脚本回归测试 | 新增 `tests/unit/test_check_env_gitignore.py`：subprocess 调用脚本，**强制 `LC_CTYPE=C.UTF-8`**（确保旧实现 `$f` 真实失败、新实现通过——突变验证：旧版在该环境下报 unbound variable）；断言退出码 0 + 模板文件报告；**加固：subprocess 60s 超时 + bash 不可用时 `skipif` 跳过**（Windows 无 Git Bash 不报错） | `tests/unit/test_check_env_gitignore.py` |

### S4 复审第二轮修复轮（PR #33 R2–R5，attempt-004）——三类阻塞问题闭环

Review 复审（S4 第二轮，REQUEST_CHANGES）判定三类交付阻塞，本轮逐一闭环：

| 阻塞问题 | 本轮修复 | 证据 |
|---|---|---|
| 紧急绕过缺少事后成功补验，恢复时限写成「最迟 24 小时」 | 恢复改为单次操作后立即执行（无宽限，24h 仅用于补验）；绕过台账补齐授权/原因链接、被绕过 SHA、操作完成时间、恢复与补验全字段；补验记录（已核实：run 31774866515 success、head `64ed09fc` 祖先链包含被绕过合并 `750ba976`、合并后约 2 小时完成） | `evidence/t7-post-verification.md`、`docs/process/main-merge-protection.md`「紧急绕过流程/绕过记录」 |
| 门禁脚本本体无独立限时 | `scripts/check-env-gitignore.sh` 每个外部调用自备限时（python3+psutil，超时终止整棵进程树返回 142）；git 卡死负例（限定时间内失败退出、无遗留进程）与 git 128 错误负例（不再误判「未跟踪」PASS） | `scripts/check-env-gitignore.sh`、`tests/unit/test_check_env_gitignore.py` |
| 正式证据与真实状态不一致（`/env`、失效链接、Issue 正文、旧 head/run） | t5 JSON `env_guard.gitignore` 修正为真实 `.env` 条目；治理文档失效相对链接修复；Issue #23 正文同步（T8 缺失样例、`required_review_thread_resolution=true`、T7 补验记录）；STATE/handoff 统一当前事实 | `t5-final-evidence.json`、`docs/process/main-merge-protection.md`、Issue #23 |
| 自动对账门禁（S4 必修项） | 新增 `scripts/check-issue23-evidence.sh`：`.gitignore`↔JSON、PR head↔PR Check Summary、Issue 正文↔实时规则（ruleset enforcement/覆盖/必填检查/线程/绕过者 + branch protection enforce_admins/strict/必填检查）、治理文档相对链接、绕过台账逐条核验（授权/原因/SHA/恢复证据/24h 固定窗口/补验 run 门禁实质/祖先覆盖）；全部外部调用自备限时 | `scripts/check-issue23-evidence.sh`、`tests/unit/test_check_issue23_evidence.py`（15 个用例，假 gh 注入） |

对账门禁经 bot 复审 4 轮加固（R2–R5）：双层必填检查核验、enforcement=active、
ruleset 覆盖默认分支、补验 run 含成功 PR Check Summary job、git 128 等执行错误显式失败、
Windows venv（`.venv/Scripts/python.exe`）解析、24h 窗口固定 0~24h、测试超时清进程树。

## 修改文件（本修复轮 PR：chore/issue-23-review-fixes）

- `docs/process/main-merge-protection.md`：ruleset 表格（线程解决=是）、新增「本地配置防护」、
  紧急绕过流程重写（可执行机制 + 演练记录 + 绕过记录首行）、验证证据增补缺失样例、完成标准对照拆分 failure/missing。
- `.gitignore`：新增 `.env`/`.env.*`/`!.env.example`。
- `scripts/check-env-gitignore.sh`：新增门禁脚本（退出码 0/1）。
- `AGENTS.md`：开发命令新增 `bash scripts/check-env-gitignore.sh`。
- `.agent-runs/issue-23-main-merge-protection/STATE.md`：复审修复状态。
- `.agent-runs/issue-23-main-merge-protection/stage-handoff-s2.md`：复审交接。
- `.agent-runs/issue-23-main-merge-protection/developer-handoff.md`：本文档。
- `.agent-runs/issue-23-main-merge-protection/evidence/`：新增 `t6-ruleset-thread-restored.md`、
  `t6-ruleset-readback.json`、`t7-emergency-bypass-drill.md`、`t7-ruleset-before/during/after.json`、
  `t7-branch-protection-before/during/after.json`、`t8-missing-check-probe.md`；
  更新 `t5-ruleset-thread-fix.md`（顶部「已撤销」标注）、`t5-final-evidence.json`（终版：acceptance 拆分 + 新增 env_guard/emergency_bypass 字段）。

## 修改文件（补充修复轮 PR #33：macOS 兼容性修复 + 回归测试 + S4 复审第二轮修复）

- `scripts/check-env-gitignore.sh`：`$f` → `${f}`（bash 3.2 + C.UTF-8 多字节解析缺陷）；
  每个外部调用自备限时（python3+psutil，超时终止整棵进程树返回 142）；git 执行错误
  （128 等）显式失败，不再误判「未跟踪」PASS。
- `scripts/check-issue23-evidence.sh`（新增）：治理证据自动对账门禁（S4 复审要求）。
- `tests/unit/test_check_env_gitignore.py`：门禁脚本回归测试（C.UTF-8 捕获缺陷）+ git 卡死负例
  + git 128 错误负例。
- `tests/unit/test_check_issue23_evidence.py`（新增）：对账门禁 15 个用例（假 gh 注入 PATH）。
- `docs/process/main-merge-protection.md`：紧急绕过立即恢复 + 绕过台账 + 证据对账门禁章节 + 失效链接修复。
- `.agent-runs/issue-23-main-merge-protection/evidence/`：`t7-post-verification.md`（新增）、
  `t7-emergency-bypass-drill.md`（补验指针）、`t5-final-evidence.json`（.env 修正 + 台账）。
- `AGENTS.md`：登记对账门禁命令。
- `.agent-runs/issue-23-main-merge-protection/STATE.md` / `developer-handoff.md` / `stage-handoff-s2.md`：状态同步。
- Issue #23 正文已同步（远端，gh issue edit）。

仓库外变更（不可逆或即时生效，需 Reviewer 关注）：

- ruleset「Autotest protect」：`required_review_thread_resolution` false→true（已回读）。
- 演练期间的临时变更已全部恢复（ruleset `bypass_actors=[]`、branch protection `enforce_admins=true`，回读无残留）。
- 探针 PR #31 已按演练计划合并入 main（`750ba976`，内容为探针记录本身，无业务代码）。

## 使用到的 BaseLib / STS2 API

不适用（本任务为仓库治理任务，无游戏 API 调用；未改动 `src/` 与 CI job 逻辑）。

## Localization 变更

无（未涉及 Card/Relic/Power/UI 等对象）。

## 自测命令与结果

```bash
# ① 环境文件门禁（含自备限时；git 卡死/128 负例在测试套件中）
cd <worktree> && bash scripts/check-env-gitignore.sh     # 3 项 PASS，exit 0

# ② 治理证据对账门禁（交接/复审前必跑；依赖 gh + 带 psutil 的 Python）
bash scripts/check-issue23-evidence.sh                   # 全部 PASS，exit 0

# ③ 保护配置回读（远程实时状态）
gh api repos/crystepj-max/STS2-AUTOTEST/branches/main/protection --jq '{enforce_admins: .enforce_admins.enabled, strict: .required_status_checks.strict, contexts: .required_status_checks.contexts}'
gh api repos/crystepj-max/STS2-AUTOTEST/rulesets/19962718 --jq '{enforcement, target, conditions, bypass: .bypass_actors, can_bypass: .current_user_can_bypass, thread: .rules[] | select(.type=="pull_request") | .parameters.required_review_thread_resolution}'
# 结果：enforce_admins=true / strict=true / contexts=["PR Check Summary"]
#       enforcement=active / target=branch / bypass=[] / can_bypass=never / thread=true

# ④ 缺失检查样例（已归档，勿复跑；复验方式见 t8 证据）
#   纯 md PR → 0 check-runs → 合并 405「Required status check "PR Check Summary" is expected.」

# ⑤ 本地全量验证（issue-23 worktree：门禁 + 对账 + 全量单测 + lint-imports）
bash scripts/check-env-gitignore.sh                      # 3 项 PASS，exit 0
bash scripts/check-issue23-evidence.sh                   # 全部 PASS，exit 0
PYTHONPATH=src python3 -m pytest tests/unit/ -q          # 1770 passed（attempt-004 实测，含门禁/对账测试）
<venv>/bin/lint-imports                                  # 架构边界通过
```

## 已知风险

1. **转公开**不可逆：全历史公开；已做敏感扫描（无凭据），仍建议 Reviewer 抽检。
2. **双保护层漂移**：ruleset 与 branch protection 已对齐；对账门禁会实时核验两层
   （必填检查/线程/绕过者/enforce_admins），未来漂移会被门禁拦下。
3. **线程解决要求恢复后**：PR 合并前须处理并标记解决 bot/人工意见线程（处理约定见 t6）；
   对账门禁核验 ruleset 实时 thread=true。
4. **md-only PR 无法合并**：纯文档 PR 无 PR Check Summary 会被 Expected 阻塞；文档更新须附带非忽略文件触发 CI。
5. **紧急绕过无静态权限**：任何绕过必须走 T7 演练闭环（临时改两层规则→操作→立即恢复→回读→审计），
   演练实证「仅 ruleset 绕过不够，须同时解除 enforce_admins」；24h 只用于补验，恢复无宽限。
6. **issue-13 未合并**（PR #22 open）：main push 检查仍失败，与本任务正交。
7. **对账门禁依赖 gh CLI 与带 psutil 的 Python**（项目 venv）；解析顺序为显式
   `CHECK_*_PYTHON` → `.venv/Scripts/python.exe`（Windows）→ `.venv/bin/python3` → 裸 `python3`。

## 建议 Reviewer 重点检查

1. S4 第二轮三类阻塞问题的修复证据链：补验闭环（t7-post-verification.md + 台账）、
   脚本自备限时（卡死/128 负例）、证据统一（t5 JSON、链接、Issue 正文、STATE/handoff）。
2. 对账门禁 `scripts/check-issue23-evidence.sh` 的 5 组核验是否完整可信（含 bot 复审 4 轮加固点）。
3. 演练后回读无残留：`bypass_actors=[]`、`current_user_can_bypass=never`、`enforce_admins=true`、
   `required_review_thread_resolution=true`、enforcement=active、必填 PR Check Summary 双层。
4. 探针 PR #31 的合并是否符合演练记录（`750ba976`），且未引入非探针内容。
5. 验收字段拆分（failure T3b / missing T8）是否各自绑定独立证据。
6. 治理文档完成标准对照表是否仍有夸大。
7. 确认未改动 `src/` 与 `.github/workflows/`（Issue 明确不做项）；PR #33 新增测试
   （门禁回归 + 卡死/128 负例 + 对账门禁套件）属本修复轮的测试资产。
