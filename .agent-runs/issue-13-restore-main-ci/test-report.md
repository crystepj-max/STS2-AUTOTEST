# Test Report: issue-13-restore-main-ci

## 测试结论

BLOCKED

代码级检查已通过；Issue #13 要求的自托管主分支集成链路与 Gawain 部署无法在当前机器完成，原因是缺少 Steam、Godot 和 STS2 游戏目录。

## 环境

- Repo: `/Users/chris/STS2-WORKSPACE/STS2-AUTOTEST`
- Branch: `fix/issue-13-restore-main-ci`
- Commit under test: `f68d6f21175cc4135ee1161831fae59ec51c6d2f`
- OS: macOS 26.5.2，Apple Silicon
- Python: 3.13.12
- pytest: 9.0.3
- Test runner: 本地 `.venv`
- STS2-Agent health: 可响应，服务版本 `0.7.2`，游戏版本 `v0.107.1`

## 执行命令

```bash
.venv/bin/pytest tests/unit/ -q --junitxml=/tmp/issue13-unit-after.xml --durations=20
.venv/bin/pytest tests/integration/ -q -m "not requires_game" --junitxml=/tmp/issue13-integration.xml
.venv/bin/mypy src/sts2_autotest --strict
.venv/bin/lint-imports
.venv/bin/ruff check src/ tests/
.venv/bin/python .github/scripts/check_ruff_baseline.py --baseline-dir <HEAD^ archive> --current-dir .
.venv/bin/python .github/scripts/check_mypy_baseline.py --baseline-dir <HEAD^ archive> --current-dir .
.venv/bin/autotest doctor --ci --json
bash -n scripts/setup-mac-runner.sh
```

## 测试结果

| 测试项 | 结果 | 证据 |
|---|---|---|
| 单元测试 | PASSED | `1757 passed, 2 warnings in 166.05s`；附件 `issue13-unit-after.junit.xml` |
| CLI-only 集成测试 | PASSED | `24 passed, 5 skipped, 6 deselected in 2.18s`；附件 `issue13-integration.junit.xml` |
| Ruff 基线门禁 | PASSED | 历史 11 项，当前 3 项，新增 0 项 |
| mypy 基线门禁 | PASSED | 历史 17 项，当前 15 项，新增 0 项 |
| 导入边界检查 | PASSED | `1 kept, 0 broken` |
| 工作流 YAML / runner 脚本语法 | PASSED | 两个 YAML `YAML OK`；`bash -n` 通过 |
| 全量 Ruff | FAILED（既有债务） | 3 项既有问题，未新增：2 个 F401、1 个 F811 |
| 全量 mypy | FAILED（既有环境债务） | cv2 缺失及 Quartz 缺少类型信息；基线门禁确认未新增 |
| 游戏启动 / Mod 加载 | BLOCKED | 本机 `GODOT_MISSING`、`STS2_MISSING` |
| Gawain 部署 | BLOCKED | 缺少真实游戏与 Mod 目录，未执行假部署 |
| 游戏内冒烟 | BLOCKED | 当前改动不涉及游戏内显示；且无可运行游戏环境 |
| 合并冲突 | PASSED | `git ls-files -u` 无输出；无未解决冲突 |

## 缺陷诊断与修复

修复前，全量单测在 299 个通过用例后长时间停滞；单文件 `tests/unit/test_cli.py` 在预检门禁测试处稳定复现。原因是该测试没有隔离本机真实游戏目录，先进入旧游戏状态清理等待，无法验证它声称的“预检失败立即返回”。

先保留红色复现，再在该测试中模拟“环境不由本框架管理”，随后针对性测试通过，之后全量单测通过。

同时修正了后台系统组件导入的类型检查忽略类别，使基线门禁不再把本提交误判为新增类型债务。

本阶段修改文件：

- `tests/unit/test_cli.py`
- `src/sts2_autotest/core/test_agent_runner.py`

## 阻塞详情

Issue #13 的完整完成标准仍要求：

1. 主分支四项快速验收在自托管 runner 上全部通过；
2. CLI 集成验证实际执行并通过；
3. Gawain 部署实际执行并给出证据；
4. 同一主分支提交下完成最终远程验证。

当前本地证据不能替代这些远程门禁。需要在已安装 Steam/Godot/STS2 的自托管 runner 上应用本轮改动，并重跑 workflow；若仍有 TLS 断连，保留独立失败证据后再处理 runner 网络配置。

## 附件

- 单测 JUnit：`/Users/chris/.gold-band/projects/Users-chris-STS2-WORKSPACE-STS2-AUTOTEST/tasks/task-001/runs/run-001/rounds/round-001/nodes/测试/attempt-001/attachments/issue13-unit-after.junit.xml`
- 集成测试 JUnit：`/Users/chris/.gold-band/projects/Users-chris-STS2-WORKSPACE-STS2-AUTOTEST/tasks/task-001/runs/run-001/rounds/round-001/nodes/测试/attempt-001/attachments/issue13-integration.junit.xml`
- 修复前失败运行日志：`.agent-runs/issue-13-restore-main-ci/evidence/run-31672864997-20260813-main-full.log`

## 建议

将本阶段交给 S3/人工验收：在目标自托管 runner 上重跑同一提交的主分支 workflow，确认工具缓存路径已生效，并实际完成 CLI 集成与 Gawain 部署。未拿到该远程证据前，Issue #13 不应关闭。
