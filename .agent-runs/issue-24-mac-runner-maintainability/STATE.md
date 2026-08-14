# STATE — issue-24-mac-runner-maintainability

- 更新时间：2026-08-14（需求阶段 S1 完成）
- 阶段：需求登记与规格完成（分诊 + 取证 + 拆票 T1–T7 + task.yaml 落盘）；
  待人工门禁「需求确认」
- 状态机位置：`TASK_CREATED` →（S1 产物齐全，待人工确认打 `ready`）→ `DEV_ASSIGNED`

## 当前事实（均已落盘，不依赖会话记忆）

- Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/24 ，
  标签 `bug` + `sized-m`（需求阶段已打）；`ready` 待人工门禁通过后补打。
- 规模：M（5 切片 / 单一子系统：自托管 runner 运维 / 活跃工作量 ≤3 天；
  7 天为被动采集等待）。非 L，无需 wayfinder/OpenSpec。
- 本机 runner 现状（2026-08-14 取证）：
  - 安装目录 `~/actions-runner`，版本 2.336.0，agentId 21，
    名称 `Chris-Mac-mini-STS2-AUTOTEST`；
    GitHub 侧唯一注册，status=online，busy=false。
  - 服务形态：svc.sh 管理的 launchd 服务，
    label `actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST`，
    进程 PID 40231（runsvc.sh）/ 40235（Runner.Listener）。
  - `svc.sh status` 当前能正确显示已启动；Issue 症状中的「Stopped」指向
    旧脚本/旧 label（见 F1）。
- 分诊三个独立失败源（详见 task.yaml `triage` 节）：
  - F1 持久：`scripts/setup-mac-runner.sh` 描述的安装
    （`~/actions-runner-autotest` + `com.sts2.autotest-runner.plist`）在本机
    **不存在**，与真实安装漂移——按旧文档操作状态/停止/启动打不到真实服务。
  - F2 间歇：BrokerServer 长轮询被 cancel（SocketException 89）后退避重试
    自行恢复；AAD token 偶发 6–15s
    （`~/actions-runner/_diag/Runner_20260813-151834-utc.log`，
    2026-08-14 00:18–00:19 UTC）。
  - F3 间歇：代理路径 TLS 抖动；issue-13 已实证直连不可用、必须走 ClashX
    （`.agent-runs/issue-13-restore-main-ci/evidence/network-probe-20260813.md`）。
- 授权边界（用户 2026-08-14 确认）：**逐项二次确认**——工作目录外改动
  （`~/actions-runner`、`~/Library/LaunchAgents`、服务重启）实施前逐项请示，
  先备份、留命令记录。
- 任务边界：单 task.yaml 覆盖 Issue 全部 5 项；T7（代理决策）依赖 T3 满 7 天，
  任务保持 open 至收口。

## 交付物（S1）

- `.agent-runs/issue-24-mac-runner-maintainability/task.yaml`（含 size、分诊、T1–T7 依赖、scope、授权）
- `.agent-runs/issue-24-mac-runner-maintainability/STATE.md`（本文件）
- `.agent-runs/issue-24-mac-runner-maintainability/stage-handoff-s1.md`

## 下一步

1. **人工门禁「需求确认」**：确认 task.yaml 的 scope/tickets/decisions。
2. 确认通过后：给 Issue #24 打 `ready` 标签 → 进入开发阶段
   （codex + GPT-5.6，先读 stage-handoff-s1.md 与 task.yaml）。
3. 开发顺序建议：T1 取证 → T2 状态修复与 T3 采集部署并行 → T4/T5/T6 →
   T3 满 7 天后 T7 收口。
