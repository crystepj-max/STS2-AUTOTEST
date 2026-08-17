# Stage Handoff — S1 需求 → 开发

- task_id: issue-24-mac-runner-maintainability
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/24 （labels: bug, sized-m）
- 交接时间：2026-08-14
- 下一阶段：开发（codex + GPT-5.6）——**第一件事读本文件与 task.yaml**

## 必读（按序）

1. `.agent-runs/issue-24-mac-runner-maintainability/task.yaml`（含 size、分诊结论、T1–T7 依赖、scope、授权边界）
2. `.agent-runs/issue-24-mac-runner-maintainability/STATE.md`
3. `../sts2-dev-infra/agent-protocol/AGENT_CONTRACT.md` → `ROLE_DEVELOPER.md` → `QUALITY_GATES.md`

## 交接要点

- **这是运行保障任务，不是代码 bug。** 禁止改动 `src/sts2_autotest/`；
  不做 Ruff 历史债治理（归属 Issue #25）；不重新注册 runner（无损坏证据）。
- 三个独立失败源（签名见 task.yaml `triage` 节）：
  - F1 持久：`scripts/setup-mac-runner.sh` 与真实安装漂移——脚本写的是
    `~/actions-runner-autotest` + `com.sts2.autotest-runner.plist`，真实安装是
    `~/actions-runner` + svc.sh 管理的 `actions.runner.*.plist`。
    T2 修复方向：以真实安装为准重写脚本/文档，提供统一 status/stop/start 入口，
    并实证「status 真实、stop 后不可接收任务、start 后进程更新且可接收」。
  - F2 间歇：BrokerServer 长轮询 cancel + SocketException 89 + 退避自愈；
    AAD token 偶发慢。属采集与归因对象（T3/T7），不是立即修复对象。
  - F3 间歇：代理 TLS 抖动；**决策起点 = 维持 ClashX 代理**（issue-13 已实证
    直连不可用），T7 用 7 天数据正式记录决策，禁止数据不足时归罪代理软件。
- **授权边界（用户已确认）：逐项二次确认**——改动 `~/actions-runner`、
  `~/Library/LaunchAgents`、重启 runner 服务前逐项向用户请示；所有改动先备份、
  留命令记录（沿用 issue-13 备份惯例）。
- **并行结构**：T3（7 天采集）部署后被动等待，T2/T4/T5/T6 不阻塞；
  T7 是唯一必须等满 7 天的收口票。
- **验收对齐 Issue 完成标准**：状态一致实证 → ≥7 天四类归因数据 →
  代理决策记录 → 健康检查前置可用/不可用 → 手册可被非原排障人员执行 →
  至少一次演练成功 → 回填 Issue。
- 禁止：以单次手工验证替代连续数据；顺手清理 Ruff 债务；把后续跳过/等待
  误判为代码失败；重新注册 runner。
- 证据目录：`.agent-runs/issue-24-mac-runner-maintainability/evidence/`
  （T1 负责建立并归档基线）。

## 门禁

- 本阶段（S1）已通过的分诊/澄清见 task.yaml `decisions` 节。
- 待办：人工门禁「需求确认」通过后，给 Issue 打 `ready` 标签再开工。
