# 质量门槛治理（Quality Gate Governance）

> 对应 issue：#21（防止质量门槛被基线、规则或依赖变更绕过）
> 互补：#23（强制 main 合并前通过最终自动验收）
> 本文件为**检查政策文件**，变更必须走独立政策变更 PR 并经 CODEOWNERS 审批。

## 1. 背景与威胁模型

#23 已强制 `main` 合并前必须通过 `PR Check Summary`，但质量门槛本身仍可被绕过。已识别的 5 条绕过路径：

| # | 绕过路径 | 防线 |
|---|---|---|
| 1 | 引入新失败时，同时扩大允许失败清单 | 技术层：基线从 base SHA 读取（`--baseline-json`） |
| 2 | 降低 Ruff 规则 | 流程层：CODEOWNERS 保护 Ruff 配置路径 |
| 3 | 降低 mypy 规则或缩小范围 | 流程层：CODEOWNERS 保护 `mypy-policy.ini` |
| 4 | 修改依赖条件，污染基线比较结果 | 技术层：固定工具版本 + 基线独立 venv |
| 5 | 检查政策变更与普通功能变更混合审批 | 流程层：CODEOWNERS + ruleset code owner review |

## 2. 政策文件清单

以下文件为检查政策文件，**普通 PR 不得修改**；变更必须走独立政策变更 PR（使用 `.github/PULL_REQUEST_TEMPLATE/policy_change.md`）并满足 CODEOWNERS 审批：

- `.github/workflows/ci-pr.yml`
- `.github/workflows/ci-main.yml`
- `.github/scripts/check_ruff_baseline.py`
- `.github/scripts/check_mypy_baseline.py`
- `.github/scripts/check_pytest_baseline.py`
- `.github/pytest-baseline.json`
- `.github/requirements-lint.txt`
- `.github/mypy-policy.ini`
- `pyproject.toml`
- `**/ruff.toml`、`**/.ruff.toml`
- `uv.lock`
- `docs/process/quality-gate-governance.md`（本文件）

保护机制：`.github/CODEOWNERS` 将上述路径指定给 `@crystepj-max`；ruleset `Autotest protect` 启用 `require_code_owner_review`。

## 3. 防线架构

### 3.1 技术层

1. **基线权威来源（决策 01）**：允许失败清单取自 PR base SHA（`.ci-baseline/.github/pytest-baseline.json`），`check_pytest_baseline.py --baseline-json` 必须显式传入；CI 未传参时 fail-closed（返回 2 + `::error::`）。
2. **工具版本固定（决策 04）**：`.github/requirements-lint.txt` 固定 `ruff==0.15.22`、`mypy==2.3.0`；CI 与本地均强制安装（`--force-reinstall`，不带 `--no-deps` 以保留 mypy 运行时依赖）。
3. **mypy 参数固定（决策 03）**：`.github/mypy-policy.ini` 固定 `strict` / `show_error_codes` / `no_error_summary`；`check_mypy_baseline.py --config-file` 显式引用。
4. **基线独立环境（决策 04）**：CI 为基线创建独立 `.venv-baseline`（安装基线 dev 依赖 + 固定 lint 工具），隔离 PR 依赖变化对比较结果的污染。
5. **Ruff 规则固定（决策 02）**：无显式配置文件，使用固定版本默认规则集；`ruff.toml` / `.ruff.toml` 路径受保护（新增即触发审批）。

### 3.2 流程层

1. **CODEOWNERS**：政策文件全部指定 `@crystepj-max`。
2. **ruleset**：`Autotest protect` 启用 `require_code_owner_review: true`。
3. **独立政策变更 PR 模板**：`.github/PULL_REQUEST_TEMPLATE/policy_change.md`，说明变更原因、影响、验证计划。
4. **单维护者洞察**：GitHub 规则禁止 PR 作者审批自己的 PR（含 code owner review）。单维护者仓库下，作者 `@crystepj-max` 无法自我审批政策文件变更 PR → merge 被 GitHub policy 阻止。**唯一绕过是紧急流程**（见 §5）。

## 4. 常规流程

- 日常功能 PR：不受政策文件保护影响，照常走 `PR Check Summary` 验收。
- 政策变更 PR：必须使用 `policy_change.md` 模板 + CODEOWNERS 审批；禁止与普通功能变更混合（探针 E 已验证混合会被阻止）。

## 5. 紧急绕过流程（沿用 #23）

1. 临时调整 ruleset（如关闭 `require_code_owner_review`）。
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
| `tests/unit/test_policy_files.py` | CODEOWNERS 覆盖政策文件清单（防保护被悄然移除） |

### 真实探针 PR（一次性建立，之后每季度或政策变更后复跑）

| 探针 | 模拟绕过 | 结果 | 证据 |
|---|---|---|---|
| A | 新增失败 + 扩大 `pytest-baseline.json` | 见探针 A 记录 | `.agent-runs/issue-21-bypass-probes/probe-a/` |
| B | 新增 `.ruff.toml` 降规则 | ✅ merge 被拒 | `.agent-runs/issue-21-bypass-probes/probe-b/` |
| C | 修改 `mypy-policy.ini` 去 strict | （机制同 B，可选） | — |
| D | 修改 `pyproject.toml` 升级依赖 | （机制同 B，可选） | — |
| E | 功能变更 + 修改 `ci-pr.yml` 混合 | ✅ merge 被拒 | `.agent-runs/issue-21-bypass-probes/probe-e/` |

## 7. 运行频率

- 本地单元测试：每次 PR CI 自动运行。
- 真实探针 PR：一次性建立基线后，**每次检查政策变更后或每季度**复跑。
- 跨平台验证：每季度或政策变更后，Linux/Windows/macOS 各跑一次，结论需一致。

## 8. 审计日志（紧急绕过）

| 时间 | 操作 | 原因 | 授权 | 补验 |
|---|---|---|---|---|
| — | — | — | — | — |
