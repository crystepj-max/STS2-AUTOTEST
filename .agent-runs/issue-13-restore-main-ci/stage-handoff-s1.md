# Stage Handoff — S1 需求 → 开发

- task_id: issue-13-restore-main-ci
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/13 （labels: bug, sized-m）
- 交接时间：2026-08-13
- 下一阶段：开发（codex + GPT-5.6）——**第一件事读本文件与 task.yaml**

## 必读（按序）

1. `.agent-runs/issue-13-restore-main-ci/task.yaml`（含 size、分诊结论、T1–T7 依赖、scope）
2. `.agent-runs/issue-13-restore-main-ci/STATE.md`
3. `../sts2-dev-infra/agent-protocol/AGENT_CONTRACT.md` → `ROLE_DEVELOPER.md` → `QUALITY_GATES.md`

## 交接要点

- **这不是代码 bug。** 两次主分支失败（run 31672864997、31561378957）均未跑到代码验证；禁止改动 `src/`。
- 两个独立失败源（签名见 task.yaml `triage` 节）：
  - F1 持久：`mkdir: /Users/runner: Permission denied`——setup-python 的 hostedtoolcache 路径沿用 GitHub 托管机约定。修复方向：`RUNNER_TOOL_CACHE` 指向可写目录（如 `/Users/chris/actions-runner/_work/_tool`），写入 runner `.env` 或 launchd plist 后重启 runner 服务。
  - F2 间歇：ClashX 代理致 GitHub 域名 TLS 断连。修复方向：runner 级 `no_proxy`/代理绕过，或 ClashX 规则放行 codeload.github.com 与 github.com。
- **授权边界**：用户已确认"修本机 runner 环境"路线；但对工作目录外文件的具体改动授权未明确——实施前必须再次向用户确认，且所有改动先备份、留命令记录。
- **验收对齐 Issue 完成标准**：四项快速验收全绿 → CLI Integration 实际执行通过 → Gawain Deploy 实际执行（独立失败须附证据）→ 同一主分支提交下结论 → 回填 Issue。
- 禁止：以"跳过任务"冒充恢复；以本地单次通过替代远程主分支验证；顺手清理历史质量债务。
- 证据目录：`.agent-runs/issue-13-restore-main-ci/evidence/`（T1 负责归档两次失败运行完整日志）。

## 门禁

- 本阶段（S1）已通过的分诊/澄清见 task.yaml `decisions` 节。
- 待办：人工门禁「需求确认」通过后，给 Issue 打 `ready` 标签再开工。
