# 质量门槛治理（Quality Gate Governance）

> 对应 issue：#21（防止质量门槛被基线、规则或依赖变更绕过）
> 互补：#23（强制 main 合并前通过最终自动验收）；#80（解除政策 PR 的 code owner 自批死锁）；#81（混合检查 / Canonical / L0 直通道）
> 本文件为**检查政策文件**，变更必须走独立政策变更 PR，由维护者点 Merge（Agent 不得代合）。CODEOWNERS 仍标识负责人；ruleset 不再要求 code owner review（#80）。政策与功能不得混在同一 PR；混合阻断由 `PR Check Summary` 内混合检查承接（#81）。

## 1. 背景与威胁模型

#23 已强制 `main` 合并前必须通过 `PR Check Summary`，但质量门槛本身仍可被绕过。已识别的 5 条绕过路径：

| # | 绕过路径 | 防线 |
|---|---|---|
| 1 | 引入新失败时，同时扩大允许失败清单 | 技术层：基线从 base SHA 读取（`--baseline-json`） |
| 2 | 降低 Ruff 规则 | `PR Check Summary` 内混合检查（对照 **base SHA** 的 CODEOWNERS；独立政策 PR 可由维护者点 Merge） |
| 3 | 降低 mypy 规则或缩小范围 | 同上混合检查（#81） |
| 4 | 修改依赖条件，污染基线比较结果 | 技术层：固定工具版本 + 基线独立 venv |
| 5 | 检查政策变更与普通功能变更混合审批 | `PR Check Summary` 内混合检查（政策与功能同 PR 则失败；不新增必填 check 名；#81） |

政策清单与 L0 白名单是**两份名单**：CODEOWNERS 决定「什么算政策」；`.github/l0-allowlist.txt` 决定「什么可以走轻量直通道」。治理 Markdown（本文件）在 CODEOWNERS 上，因此**不是 L0**。普通 `README.md` / 非政策 `docs/**` 可以是 L0。

## 2. 政策文件清单

以下文件为检查政策文件，**普通 PR 不得修改**；变更必须走独立政策变更 PR（使用 `.github/PULL_REQUEST_TEMPLATE/policy_change.md`），由维护者点 Merge。权威覆盖以 `.github/CODEOWNERS` 为准（`.github/workflows/` 一条覆盖 `ci-pr.yml` / `ci-main.yml` / `ci-nightly.yml` / `ci-game.yml` 等）：

- `.github/CODEOWNERS`（自指）
- `.github/l0-allowlist.txt`
- `.github/workflows/`（含 `ci-pr.yml`、`ci-main.yml`、`ci-nightly.yml`、`ci-game.yml`）
- `.github/scripts/check_*_baseline.py`
- `.github/scripts/check_workflow_artifact_order.py`
- `.github/scripts/check_policy_isolation.py`
- `.github/scripts/check_canonical_pr.py`
- `.github/scripts/classify_l0.py`
- `.github/scripts/pr_path_matcher.py`
- `.github/workflow-artifact-manifest.yaml`（issue #51 / #61）
- `.github/pytest-baseline.json`
- `.github/requirements-lint.txt`
- `.github/mypy-policy.ini`
- `pyproject.toml`
- `**/ruff.toml`、`**/.ruff.toml`
- `uv.lock`
- `docs/process/quality-gate-governance.md`（本文件）

保护机制：`.github/CODEOWNERS` 将上述路径指定给 `@crystepj-max`（标识负责人，不构成合并审批票）。ruleset `Autotest protect` 的 `require_code_owner_review` 为 `false`（#80）。政策/功能混合的合并阻断见混合检查（#81）。

## 3. 防线架构

### 3.1 技术层

1. **基线权威来源（决策 01）**：允许失败清单取自 PR base SHA（`.ci-baseline/.github/pytest-baseline.json`），`check_pytest_baseline.py --baseline-json` 必须显式传入；CI 未传参时 fail-closed（返回 2 + `::error::`）。
2. **工具版本固定（决策 04）**：`.github/requirements-lint.txt` 固定 `ruff==0.15.22`、`mypy==2.3.0`；CI 与本地均强制安装（`--force-reinstall`，不带 `--no-deps` 以保留 mypy 运行时依赖）。
3. **mypy 参数固定（决策 03）**：`.github/mypy-policy.ini` 固定 `strict` / `show_error_codes` / `no_error_summary`；`check_mypy_baseline.py --config-file` 显式引用。
4. **基线独立环境（决策 04）**：CI 为基线创建独立 `.venv-baseline`（安装基线 dev 依赖 + 固定 lint 工具），隔离 PR 依赖变化对比较结果的污染。
5. **Ruff 规则固定（决策 02）**：无显式配置文件，使用固定版本默认规则集；`ruff.toml` / `.ruff.toml` 路径受保护。
6. **Workflow artifact 步骤顺序（issue #51 / #61）**：`check_workflow_artifact_order.py` + `workflow-artifact-manifest.yaml` 静态校验 upload-artifact 必须晚于 producer；`ci-pr.yml` 门禁强制，本地 `scripts/verify.sh` 硬门槛同步执行。
7. **混合检查（#81）**：`check_policy_isolation.py` 对照 **base SHA** CODEOWNERS 分类变更文件。允许附带 `docs/**`、`*.md`、`tests/unit/test_policy*.py`、`tests/unit/test_ci_*_baseline.py`。命中政策 glob 又出现非附带文件 → `PR Check Summary` 失败。读不到清单 / diff / 分类不确定 → 失败（不降级）。只改 CODEOWNERS 且去掉自指 → 失败。
8. **Canonical 检查（#81）**：PR 未链 Issue 则跳过；链了且 Canonical PR 空或就是本 PR → 通过；已填且不是本 PR → 失败。
9. **L0 直通道（#81）**：删除 `ci-pr.yml` 的 `paths-ignore`；始终同一个 job 名 `PR Check Summary`。全部变更命中 **base** `.github/l0-allowlist.txt` 且未命中 **base** CODEOWNERS → 轻量路径（不跑 health / 单测 / ruff/mypy / CLI / runner 脚本测试 / 超时实验）。白名单自身不在名单里；HEAD 改白名单无效。读不到白名单时**升级全套**，不把 Summary 仅因 L0 分类失败而判红。注释-only /「不影响行为」不算 L0。`.gitignore` 是 L0。

### 3.2 流程层

1. **CODEOWNERS**：政策文件全部指定 `@crystepj-max`（谁负责，不是合并票）；文件自指。
2. **ruleset**：`Autotest protect` 的 `require_code_owner_review: false`（#80）。`bypass_actors` 保持空；审批数保持 0。不把紧急绕过当常规解锁。
3. **独立政策变更 PR 模板**：`.github/PULL_REQUEST_TEMPLATE/policy_change.md`，说明变更原因、影响、验证计划。
4. **单维护者洞察**：GitHub 仍禁止作者给自己打审批勾。关闭 code owner review 后，政策 PR 不再被「作者即唯一 code owner」挡住；维护者可在 CI 与线程解决满足后点 Merge。Agent 仍不得 `gh pr merge` 政策变更。紧急流程（§5）只用于真正紧急情况，**不再是政策变更的常规绕过**。

## 4. 常规流程

- 日常功能 PR：不受政策文件保护影响，照常走 `PR Check Summary` 全套验收。
- 纯文档 / 白名单路径（未命中 CODEOWNERS）：走 L0 轻量 `PR Check Summary`，不再因 `paths-ignore` 缺失检查而合不进去。
- 政策变更 PR：必须使用 `policy_change.md` 模板，且为独立政策变更（不含 `src/` 功能代码）；由维护者点 Merge。Agent 可开 PR / 推代码 / 跑 CI，不得代合。禁止与普通功能变更混合（混合检查 fail-closed）。政策 + 文档 / 政策附带测试文件可以通过混合检查，但仍走全套而非 L0。
- 紧急流程（§5）不是政策变更的常规路径。

## 5. 紧急绕过流程（沿用 #23）

> 2026-08-26 #80 将 `require_code_owner_review` 关闭属于**常规解锁**，不是紧急绕过：未填 `bypass_actors`，未改审批数，未改 branch protection 的 `enforce_admins`。#81 删除 `paths-ignore` 同理，是常规政策变更。

1. 临时调整 ruleset（例如临时授予 `bypass_actors`，见 `main-merge-protection.md`）。
2. 完成操作后**立即恢复** ruleset 配置。
3. 记录原因、授权人、变更内容（写回本文件审计日志）。
4. 24h 内完成等价验收补验（通过 `PR Check Summary`）。
5. 审计日志：时间、操作、原因、补验 run URL。

## 6. 反向验证样例

### 本地单元测试（随 CI 每次运行）

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_ci_pytest_baseline.py` | `--baseline-json` 从 base 读取；fail-closed；#42 已清偿强制失效 |
| `tests/unit/test_ci_ruff_baseline.py` | `--ruff-bin` / `--baseline-ruff-bin` 显式路径；相对路径转绝对 |
| `tests/unit/test_ci_mypy_baseline.py` | `--config-file` / `--mypy-bin` 参数透传 |
| `tests/unit/test_ci_workflow_artifact_order.py` | upload 晚于 producer；ci-pr/main/nightly/game 真实 YAML 通过 |
| `tests/unit/test_policy_files.py` | CODEOWNERS 覆盖政策文件清单（防保护被悄然移除） |
| `tests/unit/test_policy_pr_gates.py` | 混合 / Canonical / L0 假 diff 与不变量 |

### 真实探针 PR（一次性建立，之后每季度或政策变更后复跑）

| 探针 | 模拟绕过 | 结果 | 证据 |
|---|---|---|---|
| A | 新增失败 + 扩大 `pytest-baseline.json` | ✅ merge 被拒（CI 从 base SHA 识别新增失败） | `.agent-runs/issue-21-bypass-probes/probe-a/` |
| B | 新增 `.ruff.toml` 降规则（孤立政策 PR） | 混合检查通过，走全套；维护者可点 Merge（不再靠 code owner 自批挡） | `.agent-runs/issue-21-bypass-probes/probe-b/` |
| C | 修改 `mypy-policy.ini` 去 strict（孤立政策 PR） | 同上，孤立可合 | `.agent-runs/issue-21-bypass-probes/probe-c/` |
| D | 修改 `pyproject.toml` 升级依赖 | ✅ merge 被拒 + CI 仍用固定版本（PR #48） | `.agent-runs/issue-21-bypass-probes/probe-d/` |
| E | 功能变更 + 修改 `ci-pr.yml` 混合 | `PR Check Summary` 失败（混合检查） | `.agent-runs/issue-21-bypass-probes/probe-e/` |

> 探针 A/D 的技术层结论仍以 2026-08-17 为准。B/C/E 的**合并门禁**在 #81 后改为上表：孤立政策可合，混合必须 Summary 失败。

## 7. 运行频率

- 本地单元测试：每次 PR CI 自动运行。
- 真实探针 PR：一次性建立基线后，**每次检查政策变更后或每季度**复跑（尤其 B/C/E）。
- 跨平台验证：每季度或政策变更后，Linux/Windows/macOS 各跑一次，结论需一致。

## 8. 审计日志（紧急绕过）

| 时间 | 操作 | 原因 | 授权 | 补验 |
|---|---|---|---|---|
| 2026-08-17 | 临时关闭 code owner review + 清空 status check → 合并 PR #46（治理文档）→ 立即恢复双层保护 | 单维护者无法自我审批政策文件；文档变更命中 paths-ignore 无 status check | crystepj-max（项目经理，方案 A） | 24h 内由后续 PR 通过 PR Check Summary 自然验证（详见 `.agent-runs/issue-21-bypass-probes/emergency-doc-merge/`） |
