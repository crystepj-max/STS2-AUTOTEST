# PR #12 三个 Run 的日志签名（T1 基线）

来源：`gh run list` / `gh run view`，2026-08-14 取证。
PR：https://github.com/crystepj-max/STS2-AUTOTEST/pull/12（quality/ruff-debt-batch-2）

## Run #39 — databaseId 31673830034（cancelled）

- 触发：2026-08-13T06:26:22Z，event=pull_request
- CLI Integration Tests：startedAt 06:33:04Z → completedAt 06:54:06Z，conclusion=cancelled，
  steps=[]（**从未开始执行步骤，全程排队约 21 分钟**）
- 同一 run 内 GitHub 托管矩阵 job（ruff/mypy/import/unit）全部 success，
  说明排队发生在自托管 runner 侧，不是代码问题
- 并发组 `pr-${{ github.event.pull_request.number }}` cancel-in-progress 生效，
  #40 触发后 #39 的排队 job 被取消 —— 排队 + 取消的组合即 Issue 症状 1/2

## Run #40 — databaseId 31675595593（failure）

- 触发：2026-08-13T06:54:14Z
- CLI Integration Tests：startedAt **10:10:25Z** → completedAt 10:12:43Z（success）
  —— **排队 3 小时 16 分钟**后才被 runner 领取，Issue「长期等待」的直接证据
- PR Check Summary：startedAt 10:12:44Z → 10:12:47Z，conclusion=failure
  （run 最终失败在汇总强制步骤，而非集成测试本身）
- 同一台 runner 在 06:33–10:10 期间未领取该 job → 间歇性不可用（F2/F3 候选窗口）

## Run #47 — databaseId 31754859148（success）

- 触发：2026-08-13T23:43:43Z
- 唯一 job PR Check Summary：23:43:48Z → 23:52:57Z，**9m9s 完整通过**（success）
- 产物 junit-integration / junit-unit-macos-self-hosted 正常上传
- 证明：runner 注册与任务匹配链路本身可正常工作（与 Issue「已有证据」一致）

## 签名提炼（供 T5 手册/签名→处置表使用）

| 签名 | 含义 | 处置 |
|---|---|---|
| job queued > 15min 且 runner online | runner 领取异常（F2/F3 候选） | 按手册查 runner 日志 + 探针数据归因 |
| job cancelled after long queue | 并发组取消 | 重试即可，非永久故障 |
| BrokerServer SocketException 89 + Back off | 长轮询中断，自愈中 | 观察，勿重启（重启会打断自愈） |
| AAD token slow >5s | 代理/上游波动 | 记录，纳入 T7 归因 |

## 原始命令

```bash
gh run list --limit 80 --json number,status,conclusion,event,headBranch,createdAt
gh run view <databaseId> --json jobs
```
