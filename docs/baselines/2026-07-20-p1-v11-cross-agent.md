# P1 跨 Agent 验收基线

日期：2026-07-20
基线版本：`10f26e5`
版本状态：`main` 与 `origin/main` 一致

## 1. 基线范围

本基线只覆盖 P1 的跨 Agent 任务生命周期：能力查询、任务提交、任务查询、任务取消、任务恢复和结果报告。它证明不同 Agent 可以通过公开入口完成同一套任务闭环。

它不等同于整个项目的全部功能回归，也不替代 P0 目标场景、视觉表现或业务 Mod 的独立验收记录。

## 2. 正式验收结果

| 验收轮次 | 结果 | 原任务 | 恢复任务 | 第二次新任务 |
| --- | --- | --- | --- | --- |
| V11i | PASSED | `run-20260719-231648-5ffe6829` | `run-20260719-231806-8f123c65` | `run-20260719-231848-a41cf0c0` |
| V11j | PASSED | `run-20260720-034053-dcbc1294` | `run-20260720-034210-4a728cc1` | `run-20260720-034257-7568a673` |

V11j 的关键判定全部通过：

- 重复提交保持同一任务编号；
- 原任务和第二次新任务均在执行中取消并进入 `CANCELLED`；
- 恢复任务创建新编号，并正确关联原任务，最终为 `PASSED`；
- 三次任务的结果、截图、日志和压缩包均可读取；
- 任务结束后回到干净主菜单，没有残留任务；
- 防睡眠措施运行期间存在，结束后无残留；
- 没有环境阻塞或假通过。

## 3. 配套检查

- 单元检查：1693 项通过，0 项失败；
- 集成检查：30 项通过，5 项因外部环境条件跳过；
- 编译、依赖层级和版本差异检查：通过；
- P1 全部工作已纳入 `10f26e5` 并推送到远端。

## 4. 证据索引

正式交接说明：

- `docs/handoff/2026-07-19-p1-v11-fix-handoff.md`
- `reports/v11/v11i-driver.log`
- `reports/v11/v11j-driver.log`
- `reports/v11/unit-full-p11j.txt`
- `reports/v11/integration.txt`

V11i 原始结果：

- `tests/output/cross-agent-p1/p1-platform-fix-20260719-v11i/`
- `tests/output/cross-agent-p1/p1-platform-fix-20260719-v11i/result.json`

V11j 原始结果：

- `tests/output/cross-agent-p1/p1-platform-fix-20260720-v11j/`
- `tests/output/cross-agent-p1/p1-platform-fix-20260720-v11j/result.json`

V11j 三个正式运行包：

- `tests/output/artifacts/run-20260720-034053-dcbc1294_cancelled.zip`
- `tests/output/artifacts/run-20260720-034210-4a728cc1_passed.zip`
- `tests/output/artifacts/run-20260720-034257-7568a673_cancelled.zip`

关键文件校验值：

```text
806da2ae1e151036ce4ad352f71deab10080e3605f93ba938549c243fcf19a86  v11j/result.json
67cb157937ecd600494c81b83b40bcb7e291435eb8146ad35d75fc85eb1f9c16  v11i/result.json
a81eff796650fb7ebe036dbd853f497af313d73b75a96cadecd15a3a30e3e6a3  orig_cancelled.zip
bedaa0611f63ba09a8ce1a940dd729ef0af78db0d7fe98252f23d94af3550bab  resume_passed.zip
47e2642ba768d9badd49ecf4af8e82ac606c18258e06c53ced3b8701c3f30e30  second_cancelled.zip
```

## 5. 历史数据治理

### 必须保留

- V11i、V11j 原始结果目录和三项正式运行包；
- V11 失败轮次的总结及每个独立失败原因的代表证据；
- P0 目标场景的正式报告和被报告明确引用的运行包；
- 本文件和 P1 交接文档。

### 本轮可安全清理

- 已确认为空、没有结果文件的 `tests/output/mcp-run-*` 目录；
- 同一问题的重复过程文件，但只能在正式证据和失败原因总结已经保留后处理；本轮已完成运行包可读性核对。

### 暂不删除

- `tests/output/logs/`、`tests/output/screenshots/`、`tests/output/artifacts/` 中尚未逐项归类的大批历史数据；
- `.env`、`.serena/`、`.workbuddy/`、`reports/` 下的未提交文件；
- 任何被现有交接文档直接引用、但尚未确认有替代证据的路径。

原因：这些内容仍可能承担失败复盘、版本比较或其他 Agent 交接作用。后续清理必须先把独立经验写入项目文档，再删除原始重复材料。

## 6. 本轮清理登记

所有 `tests/output/artifacts/*.zip` 均已通过完整性检查。

| 批次 | 路径 | 数量/规模 | 用途判断 | 风险控制 | 结果 |
| --- | --- | ---: | --- | --- | --- |
| C01 | `tests/output/mcp-run-*`（下列 6 个具体目录） | 6 个空目录 | 没有结果文件的中断占位 | 无证据可丢失 | 已删除 |
| C02 | `tests/output/screenshots/*` 中已存在于某个运行包的同名文件 | 549 个，约 219.6 MB | 运行包已有完整副本的展开截图 | 运行包已逐个校验可读取 | 已删除 |
| C03 | `tests/output/logs/*` 中已存在于某个运行包的同名文件 | 131 个，约 388.0 MB | 运行包已有完整副本的展开日志 | 运行包已逐个校验可读取 | 已删除 |

以下未封存文件没有删除：`tests/output/screenshots/` 中剩余 234 个文件，以及 `tests/output/logs/` 中剩余 6 个文件。它们没有运行包副本，继续保留，待下一轮按用途逐项判断。

C01 的具体路径：

- `tests/output/mcp-run-20260719-075229-29ec34`
- `tests/output/mcp-run-20260719-080136-35f9ec`
- `tests/output/mcp-run-20260719-125422-eefe56`
- `tests/output/mcp-run-20260719-232836-eb33d9`
- `tests/output/mcp-run-20260720-022444-688abc`
- `tests/output/mcp-run-20260720-035648-3a2b68`

## 7. 累积日志复盘摘要

`tests/output/logs/game-process.log` 当前约 133 MB，未纳入 C03，因为没有运行包副本。对其内容做了汇总：

- 没有发现 `fatal`、`crash`、`exception`、`conflict` 或 `blocked`；
- 发现 42 次超时相关记录，集中在等待下一帧、事件页面、奖励领取和开局过渡；
- 发现 96,573 次重复的本地化格式提示，以及 2 次静态字符串提示；
- 这些信息与既有 P1 复盘中的“页面过渡等待过长”和“本地化噪声”结论一致，没有新增 P1 阻断原因。

因此该累积日志暂时保留，作为未封存历史线索；后续若确认已有代表性日志和文档足以覆盖上述结论，再单独处理。
