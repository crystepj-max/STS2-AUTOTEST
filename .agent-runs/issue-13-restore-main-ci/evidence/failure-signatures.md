# 失败签名归档（T1）

- 归档时间：2026-08-13
- 任务：issue-13-restore-main-ci
- 来源 Issue：https://github.com/crystepj-max/STS2-AUTOTEST/issues/13

## 运行记录（修复前基线）

| 运行 | 日期 | 提交 | 结果 | 日志归档 |
|------|------|------|------|----------|
| [31672864997](https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31672864997) | 2026-08-13 | f68d6f2 | failure | `run-31672864997-20260813-main-full.log` |
| [31561378957](https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31561378957) | 2026-08-12 | — | failure | `run-31561378957-20260812-main-full.log` |

## F1（持久性失败，两天均现）：setup-python hostedtoolcache 无写权限

失败点：Quick Checks 各 job 的 `actions/setup-python@v5` 步骤（代码验证尚未开始）。

```
Check if Python hostedtoolcache folder exist...
Creating Python hostedtoolcache folder...
##[error]mkdir: /Users/runner: Permission denied
```

- 根因：runner 以 launchd 服务运行（用户 `chris`，`/Users/chris/actions-runner`），但 setup-python 的
  工具缓存路径解析为 GitHub 托管机约定路径 `/Users/runner/...`；本机 `/Users` 归 root 所有，
  `chris` 无权限创建 `/Users/runner` → 失败。
- 证据行：8-12 日志 3 处、8-13 日志 1 处（见下文）。

## F2（间歇性失败，8-13 出现）：GitHub 域名 TLS 断连

```
Client network socket disconnected before secure TLS connection was established
```

- 触发点：`actions/setup-python` 下载 python 发行包（github.com）、action 下载（codeload.github.com）。
- 证据行：8-13 unit-test job 2 处、lint job 2 处。
- 定性：**间歇性代理抖动，GitHub Actions 重试机制可自愈**（8-13 重试后下载成功，实际致命失败是 F1）。
- 修复结论（2026-08-13 实测，补充证据 `network-probe-20260813.md`）：
  - 走 ClashX 代理：3/3 成功，1.9–3.3s —— 稳定，**必须走代理**。
  - 直连 codeload.github.com：2/2 超时（12s 只收 150–280KB）—— 本机直连 GitHub 受网络环境影响不可用。
  - 因此**不添加 GitHub 域名 NO_PROXY 直连**；TLS 抖动靠重试自愈兜底。

## F3（验证期间发现，静默存在）：main push 检查与 PR 检查机制不一致

- 现象：本地复现 CI 环境（python3.11 + `.[dev]`）下 ruff 0.16.2 报 **363 项**存量错误；
  ci-main.yml 的 lint/mypy 为全量直跑（无 baseline），必然失败。
- 根因：pyproject 依赖为 `ruff>=0.1.0`（未锁版本），ruff 0.16 默认规则集扩增；
  PR #10「新代码不增债」的 baseline 机制只落地到 ci-pr.yml，ci-main.yml 未同步。
  历史上 main CI 从未跑到代码检查（F1 在更早阶段失败），此不一致未被暴露。
- 修复：ci-main.yml 对齐 ci-pr.yml 的 baseline 机制（见本 PR），存量债务由基线容忍、只拦新增。

## 连锁结果

- CLI Integration Tests：跳过（Quick Checks 失败连锁）
- Deploy Gawain Mod：跳过（同上）
- Push Summary：failure

## 结论

非代码回归（`src/` 无缺陷改动需求）。恢复主分支验证 = F1 runner 环境修复 + F3 main 检查机制对齐。
