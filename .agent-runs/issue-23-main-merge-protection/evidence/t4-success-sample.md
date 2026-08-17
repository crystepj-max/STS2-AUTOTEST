# T4 成功样例证据：满足规则的 PR 正常合并（2026-08-14）

## 样例 PR

- PR: https://github.com/crystepj-max/STS2-AUTOTEST/pull/27（治理文档 + 证据归档）
- 内容：`docs/process/main-merge-protection.md` + `.agent-runs/issue-23-main-merge-protection/`（task.yaml、STATE、stage-handoff、developer-handoff、evidence/）
- 附带非忽略文件（证据 JSON/txt）→ 正常触发 ci-pr.yml

## CI 结果

- PR Check Summary: **success**
- 成功 run: https://github.com/crystepj-max/STS2-AUTOTEST/actions/runs/31768914035
- 说明：首次运行因自托管 runner 网络中断（ECONNRESET，artifact 上传与 visual 依赖安装失败）失败，与 PR 内容无关；重跑后全绿。

## 合并记录

- 合并时间：2026-08-14T04:31:43Z
- 合并提交：`a0673525aa32fe3845efdbea47c3b023dd442856`
- 合并方式：PR 形态 + `PR Check Summary` 成功（strict=true，分支与 main 最新）

## 结论

满足规则（PR 形态 + 最终提交通过 PR Check Summary）的 PR 可以正常合并，
符合 Issue 完成标准第四条。
