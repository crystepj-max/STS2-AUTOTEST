# Developer Handoff: issue-24-mac-runner-maintainability

- 开发阶段：T2 状态修复 / T3 采集探针 / T4 健康检查 / T5 手册（本交接）
- T1 基线取证已在提交 a271d70 归档（evidence/ 4 份证据）
- T6 演练 / T7 收口：待授权与 7 天数据，见「未完成项」

## 实现摘要

Issue #24（自有 Mac 自动验收的连接与服务可维护性）是**运行保障任务，非代码 bug**。
分诊确认三个独立失败源（task.yaml `triage`）：

- **F1 持久**：`setup-mac-runner.sh` 描述一套不存在的安装（`~/actions-runner-autotest`
  + `com.sts2.autotest-runner.plist` + `run.sh`），真实安装是 `~/actions-runner`
  + svc.sh 管理的 launchd 服务。按旧脚本/文档操作打不到真实服务。
- **F2 间歇**：BrokerServer 长轮询被 cancel（SocketException 89）后退避自愈；
  AAD token 偶发慢。属采集归因对象，不是修复对象。
- **F3 间歇**：代理 TLS 抖动；直连不可用（issue-13 实证），维持 ClashX 代理。

本阶段交付（均 TDD：先写失败测试再实现）：

1. **T2 状态一致性修复**：重写 `setup-mac-runner.sh`（以真实安装为准、幂等）；
   统一 `runner-ctl.sh` status/stop/start 入口（前次会话已建，本次补测试验证）。
2. **T3 连续采集探针**：`runner-probe.sh`，单次输出一行 JSONL，记录服务状态 /
   GitHub 侧状态 / 直连与代理可达性 / 出口 IP，支持四类归因；PROBE_OUTPUT 落盘追加。
3. **T4 健康检查**：`check-runner-health.sh`（不依赖游戏环境，0=可用 1=不可用），
   已接入 `ci-pr.yml` 业务验收 workflow 前置 step（Runner health precheck）。
4. **T5 运维手册**：`docs/runner-runbook.md`，以非原排障人员可独立执行为标准。
5. **verify.sh**：本地全量验证入口（shell 测试 + unit + lint-imports；增量基线门禁）。

## 修改文件

- `scripts/setup-mac-runner.sh`（重写，F1 漂移修复）
- `scripts/runner-ctl.sh`（T2 统一入口，前次会话已建）
- `scripts/runner-probe.sh`（T3 新增）
- `scripts/check-runner-health.sh`（T4 新增）
- `scripts/verify.sh`（新增验证入口）
- `scripts/tests/run-all.sh`、`scripts/tests/lib/helpers.sh`（前次会话已建）
- `scripts/tests/test_runner_ctl.sh`（前次会话已建）
- `scripts/tests/test_setup_mac_runner.sh`（新增）
- `scripts/tests/test_runner_probe.sh`（新增）
- `scripts/tests/test_check_runner_health.sh`（新增）
- `.github/workflows/ci-pr.yml`（T4 健康检查前置 step）
- `docs/runner-runbook.md`（T5 手册）
- `.agent-runs/issue-24-mac-runner-maintainability/evidence/`（T1 基线，已在 a271d70）

## 使用到的 BaseLib / STS2 API

本任务为自托管 runner 运维，**未使用任何 BaseLib / STS2 API**，未改动 `src/`
（task.yaml scope 明确排除）。引用的事实依据均来自本机取证（T1 evidence）：
svc.sh 语义、launchd label 构造、GitHub Actions Runner 行为。

## Localization 变更

无（不涉及 Card/Relic/Power/UI/Tooltip/Event/Option Text）。

## 自测命令

```bash
# 1. shell 脚本测试（本任务核心交付物）
bash scripts/tests/run-all.sh

# 2. 项目回归
.venv/bin/python -m pytest tests/unit/ -q
.venv/bin/lint-imports

# 3. 统一入口
./scripts/verify.sh
```

## 自测结果

- shell 脚本测试：**PASSED**（4 套：runner-ctl 9 用例 / setup 6 用例 / probe 5 用例 / health 5 用例；突变验证确认测试能捕获缺陷）
- 单元测试 `tests/unit/`：**见 verify.sh 输出**
- lint-imports：**PASSED**（Contracts: 1 kept, 0 broken）
- Build / Smoke：NOT_RUN（无构建步骤；运行保障任务不涉及游戏内行为，无烟测项）

## 未完成项（需授权 / 需时间）

| 项 | 说明 | 阻塞原因 |
|---|---|---|
| T2 实证 | 真实机器 status/stop/start 验证「stop 后不可接收任务、start 后进程更新」 | 需逐项授权（工作目录外服务操作） |
| T3 部署 | 定时探针部署（cron/launchd，≥7 天连续采集） | 需逐项授权；最快 2026-08-21 满 7 天 |
| T6 演练 | 人工恢复演练 + 演练记录落盘 evidence/ | 依赖 T5 手册 + 需授权（服务重启） |
| T7 收口 | 7 天数据四类归因 + 代理决策记录 + 回填 Issue | 依赖 T3 满 7 天数据 |

## 已知风险

- `check-runner-health.sh` 的网络探测（curl 5s 超时）在 runner 作业内执行；
  gh 缺失/未认证不影响判定（能领取 job 已证明在线）。
- workflow 前置 step 失败会使 PR job 直接红——这是预期行为（快速失败，
  避免业务验收在故障环境下排队/执行）。
- 探针 gh 查询带 8s 超时；探针整体应可安全高频调用，但建议 5–15 分钟间隔。

## 建议 Reviewer 重点检查

1. `setup-mac-runner.sh`：幂等分支（已配置安装跳过注册）与 F1 漂移引用清除；
   注意新安装分支的 `--name Chris-Mac-mini-STS2-AUTOTEST` 为真实机器名（刻意为之）。
2. `runner-probe.sh`：JSONL 字段完整性（四类归因所需字段是否齐全）；
   `run_with_timeout` 的进程清理。
3. `ci-pr.yml` 前置 step 位置与失败语义（fail-fast 是否符合运维预期）。
4. `scripts/tests/` 测试的 fake 环境是否充分隔离（不触碰真实 runner 目录）。
5. 文档/脚本中的真实机器路径（`~/actions-runner` 等）是否与本机一致。
