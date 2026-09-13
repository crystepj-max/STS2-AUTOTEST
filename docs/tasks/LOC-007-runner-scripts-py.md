> 本任务属本地轨道，GitHub 恢复后需补建 issue；决策依据：用户确认「3 个决策项按推荐走」（LOC-007 采方案 B：不迁移）。

# LOC-007 runner 三件套迁移

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-007 |
| 需求来源 | 会话录入（CONTEXT.md 债务：runner 三件套在包外） |
| 任务类型 | enhancement（评估类） |
| 优先级 | sized-m |
| 当前状态 | 已否决（决策 B：不迁移） |
| 需求基线 | V1 |
| 前置依赖 | 无 |
| 决策时间 | 2026-09-10 |

## 决策记录（ADR 式，供未来架构走查不再重提）

**决定：不把 runner 三件套（runner-ctl.sh / runner-probe.sh / check-runner-health.sh）
迁入框架 Python 包。**

理由：
1. 产品边界——三件套管理 GitHub Actions runner 服务（launchctl/svc.sh、
   Runner.Listener、代理四类归因），与「STS2 Mod 测试编排框架」的领域无交集；
2. 现状健康——拥有独立 bash 测试套件并接入 CI（scripts/tests/run-all.sh），
   近期迭代集中在 nightly 环境域（已由候选 5 收敛进 Python）；
3. 迁移成本——需重写千行平台绑定 bash 并移植测试套件，收益（mypy/单测覆盖）
   不抵成本。

**重新评估触发条件**：runner 域再次进入活跃迭代、或归因状态机需要跨域复用时。
