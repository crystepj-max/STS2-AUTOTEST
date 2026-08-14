# Stage Handoff — S2 开发 → 审查

- task_id: issue-24-mac-runner-maintainability
- 来源 Issue: https://github.com/crystepj-max/STS2-AUTOTEST/issues/24
- 交接时间：2026-08-14（开发阶段）
- 下一阶段：审查（Reviewer）——先读本文件、developer-handoff.md 与 task.yaml

## 交接要点

- **开发完成范围**：T2（状态修复）/ T3（探针脚本）/ T4（健康检查 + workflow 前置）/
  T5（手册）已实现并自测；T1 基线在 a271d70 归档。
- **TDD 证据**：4 套 shell 测试（runner-ctl 9 / setup 6 / probe 5 / health 5 用例），
  全部 RED→GREEN 验证过（setup/probe/health 先看失败后看通过；probe/health
  做了突变验证确认测试能捕获缺陷）。
- **verify.sh 全绿**：shell 测试 + unit（1757 passed）+ lint-imports +
  ruff/mypy 增量（New: 0，基线 origin/main）。
- **未完成（需授权/时间，不在本 PR 范围内）**：
  - T2 真实机器实证（stop 后不可接收任务 / start 后进程更新）——需逐项授权；
  - T3 定时部署（cron/launchd，≥7 天连续采集）——需授权，最快 2026-08-21 满 7 天；
  - T6 恢复演练——需授权（服务重启）；
  - T7 代理决策收口——依赖 T3 满 7 天数据。
- **审查重点**（详见 developer-handoff.md 末节）：setup 脚本幂等性、探针 JSONL
  字段完整性、workflow 前置 step 失败语义、测试 fake 环境隔离性。
- **风险提示**：verify.sh 的增量基线步骤要求 BASELINE_DIR 为 origin/main
  的最新 checkout（用本地过时 main 会产生 mypy 行号伪报）。

## 门禁

- Gate1（知识）✓ / Gate2（API 来源）不适用（未用 BaseLib/STS2 API）/
  Gate3（本地化）不适用 / Gate4（构建）→ 等价验证（unit+mypy+lint）已绿。
- 待 Reviewer：`reviewer_approved`。
- 待人工：T2 实证 / T3 部署 / T6 演练授权；T7 在 2026-08-21 后收口并回填 Issue。
