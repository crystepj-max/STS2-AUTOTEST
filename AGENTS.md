# STS2-AUTOTEST Agent Rules

本仓库是 STS2 Mod 通用自动化测试平台，承担角色化多 Agent 流程中的 Test Agent 底座。

## 仓库职责

- 管理通用测试计划。
- 执行 build、静态检查、localization check。
- 部署 Mod 到 STS2 mods 目录。
- 启动游戏并等待自动化接口可用。
- 执行 smoke test / regression test。
- 收集日志、截图、状态 JSON 和测试报告。

## 多 Agent 协议

本仓库遵守：

- `../sts2-dev-infra/agent-protocol/AGENT_CONTRACT.md`
- `../sts2-dev-infra/agent-protocol/ROLE_TESTER.md`
- `../sts2-dev-infra/agent-protocol/QUALITY_GATES.md`
- `../sts2-dev-infra/agent-protocol/ARTIFACT_TEST_REPORT.md`

## Test Agent 原则

- 优先脚本化和可复现，不依赖模型“感觉”。
- 没有日志、截图或状态 JSON 证据的测试项不得标记为 PASSED。
- 游戏无法启动、自动化接口不可用、环境缺失时标记 BLOCKED。
- 发现裸 Key、missing localization、崩溃、关键交互失败时标记 FAILED。

## 仓库边界

- `test-plans/`：结构化测试计划。
- `scripts/`：测试执行脚本。
- `reports/`：测试报告输出目录。
- `fixtures/`：测试数据与样例输入。

不要在本仓库保存 Gawain 业务实现代码；业务仓库只提供测试定义和目标分支，本仓库负责执行通用测试能力。
