# 2026-07-17 P0 目标场景收口 — 最终回传

## 总体结论

- P0 总体状态：**PASSED**
- NEXT_ACT 完成条件：已按完整定义验收（新局 → 开图事件 → 第一章遍历 → Boss → Boss 奖励 → 章节变化 → 第二章入口事件观察并处理 → 第二章稳定地图 → 再次确认章节 → PASSED）
- 第二章入口事件：两次整章运行分别真实观察并处理 OROBAS（codex 轮）与 PAEL（本轮最终轮）
- 证据闭环：清单与压缩包逐项一致（最终轮 45 截图 + 1 日志，独立解压核对一致）
- 持久报告：即时读取 ✓、MCP 重启后读取 ✓、原目录被保留策略清理后从压缩包读取 ✓（card_test 运行真实演练）
- 十类目标场景：均有独立公共任务结果（见验收矩阵）；本轮另补 COMBAT 三模式、NEXT_ACT×2、card_test、死亡测试
- 最终截图：窗口绑定、1920×1080 JPEG、新鲜帧；FINAL 终态凭证与最终状态视觉一致（已修离屏陈旧帧问题）
- 最终整章：`run-20260717-040110-b34e7a86` PASSED
- 是否使用公共 MCP：是（`http://127.0.0.1:8090/mcp`，capabilities → submit_run → get_run → get_report）
- 是否包含角色或 Mod 专属规则：否（`git diff` 对本轮改动文件 grep IRONCLAD/SILENT/GAWAIN 为零；卡牌 ID 均为运行参数）

## 修改内容（本轮在 codex 交接基础上新增）

- `src/sts2_autotest/core/navigation.py`：`combat_mode="death"` 时战斗只返回 `end_turn`，禁用出牌与 `win_combat`。
- `src/sts2_autotest/core/journeys.py`：新增 `death_test`（COMBAT→只 end_turn→真实 GAME_OVER 才算 PASSED）与 `card_test`（give_card→验证入手→真实打出→状态变化；假成功/无调试能力分别判 NO_PROGRESS/DEBUG_ACTIONS_UNAVAILABLE）；observation 回调支持异步（截图稳定等待的前提）。
- `src/sts2_autotest/cli/main.py`：CLI 暴露 `card_test` journey、`death` 战斗模式、`--card-id`；截图回调改异步并在采集前等待 API 状态连续一致 + 渲染 settle；GAME_OVER 纳入关键截图；成功路径新增 `FINAL_` 终态截图（延长 settle，保证至少一张与最终状态一致的视觉凭证）。
- `src/sts2_autotest/cli/mcp_tools.py`：capabilities 增加 `death` 战斗模式、`supported_journeys`、`card_test` 声明与 `card_id` 参数；submit 校验；`card_test` 默认 agent 适配器（修复 cli 适配器误报调试能力不可用）；`read_run_report` 保留标量 `final_state`、仍剔除 dict 形态嵌入状态；顺手修复 codex 引入的 2 个 mypy 错误（trace_path 变量重名）。
- `src/sts2_autotest/evidence/capture.py`：macOS 离屏窗口陈旧帧防护——窗口不可见时先激活应用恢复渲染，仍不可见则报告截图不可用，绝不把系统缓存旧帧当最新证据。
- 测试：`test_goal_scene_executor.py`（死亡模式 5 + 卡牌专测 5 + 异步回调 1）、`test_mcp_tools.py`（契约 6）、`test_journey_foreground.py`（稳定等待 2 + FINAL 1）、`test_p0_evidence_persistence.py`（标量 final_state 1）、`test_capture.py`（离屏防护 3）。
- 文档：`docs/unified-run-contract.md`（4.2 死亡测试与卡牌专测）、`docs/user-manual.md`（CLI 示例）、`docs/platform-capability-inventory.md`（能力清单）。
- 原有工作区内容如何保留：全程只做增量 Edit/Write，未执行任何 reset/checkout/clean；用户既有修改（AGENT.md、CLAUDE.md、README.md、docs/、.env、.serena/、.workbuddy/、reports/ 等）与 codex 改动全部原样保留；未提交、未推送。

## 自动检查

- 精确问题检查：死亡模式端到端 mock 流程、give_card 假成功两类（无效果/给错牌）、离屏陈旧帧拒绝、FINAL 凭证、契约校验，全部新增并通过。
- 目标场景检查：`test_goal_scene_executor.py`、`test_journeys.py`、`test_navigation_flow.py` 通过。
- MCP 和任务检查：`test_mcp_tools.py`、`test_p0_evidence_persistence.py`、`test_mcp_server.py` 通过。
- 证据和报告检查：`test_capture.py`、`test_evidence_hooks.py`、`test_journey_foreground.py` 通过。
- 完整安全回归：`pytest tests/unit tests/integration` → **1609 passed, 1 skipped, 2 warnings**（现场服务在线时）；复核复跑 **1604 passed, 6 skipped, 0 failed**（游戏窗口与公共服务关闭后 5 项现场检查转为跳过；总数 1610 一致）。`compileall` OK；`git diff --check` OK；`lint-imports` KEPT；改动文件 `ruff` 全绿；`mypy --strict` 14 errors == HEAD 基线（另修复了 codex 引入的 2 个）。
- 跳过项：1 个截图用例在无游戏窗口时跳过（既有行为）；2 个 `TestAgentRunner` 收集警告（既有）；两个 generated 同名冲突（既有非 P0 遗留，未删文件掩盖）。
- 跳过原因：均为既有已知项，与本轮改动无关。

## 十类目标场景验收

本轮新跑（最终代码、公共 MCP、evidence=full）：

| target_scene | run_id | character_id | 最终状态 | 最终场景 | 耗时 | 失败原因 | 报告/证据包 |
|---|---|---|---|---|---|---|---|
| COMBAT(basic) | run-20260717-032254-763b7dcb | IRONCLAD | PASSED | COMBAT | 13.8s | — | tests/output/artifacts/run-20260717-032254-763b7dcb_passed.zip |
| COMBAT(traversal) | run-20260717-035211-62d39b0f | IRONCLAD | PASSED | COMBAT | 24.0s | — | tests/output/artifacts/run-20260717-035211-62d39b0f_passed.zip |
| COMBAT(death) | run-20260717-033414-121d47b3 | IRONCLAD | PASSED | GAME_OVER | 112.7s | — | tests/output/artifacts/run-20260717-033414-121d47b3_passed.zip（每回合 end_turn、血量 64→0、死亡截图） |
| NEXT_ACT | run-20260717-040110-b34e7a86 | IRONCLAD | PASSED | MAP(ch2 f0) | 317.9s | — | tests/output/artifacts/run-20260717-040110-b34e7a86_passed.zip（45 截图 1 日志，清单一致） |
| card_test | run-20260717-033255-fdc33e20 | IRONCLAD | PASSED | COMBAT | 28.7s | — | tests/output/artifacts/run-20260717-033255-fdc33e20_passed.zip（give_card 入手、play_card 格挡 0→5） |

其余 8 类场景（MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / REST / SHOP / CHEST / CARD_REWARD）由 codex 轮在同一代码基线上完成独立公共任务验收（run-20260717-001842 … run-20260717-012735，全部 PASSED，证据包保留于 tests/output/artifacts/）；本轮对这些场景的处理路径只做增量改动（战斗分支新增 death 前置判断、截图回调异步化、FINAL 截图追加），不改变 traversal/basic 行为，1609 项自动回归全绿。

额外角色短目标（codex 轮）：SILENT → CHARACTER_SELECT，run-20260717-014957-67ae856a，PASSED。

保留的失败/诊断记录（不得改写）：run-20260717-032448-c46c5e81（card_test 走 cli 适配器→DEBUG_ACTIONS_UNAVAILABLE，促使修复适配器默认）、run-20260717-032755-db26a62f（游戏进程无调试环境变量→复位失败留证），以及 codex 轮 4 条失败记录。

## 最终整章运行（run-20260717-040110-b34e7a86）

- character_id：IRONCLAD；起始章节：第一章（act_id=0）
- 第一章开图事件：NEOW（涅奥祝福）
- 地图路线：17 次节点选择（leftmost）；实际房间：COMBAT×多场、EVENT、CARD_REWARD、REST、SHOP、CHEST
- Boss：同族信徒×2 + 同族神官（traversal 模式 win_combat 通路验证×10，均验证战斗离开与后续页面）
- Boss 奖励：collect_rewards_and_proceed 完成，章节 act_id 0→1
- 章节切换：真实观察；第二章入口事件：PAEL（佩尔），3 个选项，选择第一个未锁定选项完成
- 第二章稳定地图：MAP、act_id=1、floor=0、is_traveling=false、无未处理事件/奖励
- 总耗时：317865ms（run 记录 / run-result / journey-trace / zip 内报告完全一致）
- 最终结果：PASSED；62 ops、45 截图（含 FINAL_MAP 新鲜帧终态凭证）、1 日志

## 证据一致性

- 截图清单数量 45 == 压缩包截图数量 45（独立解压核对）；日志清单 1 == 压缩包日志 1
- run-result / journey-trace / evidence-manifest / 人类报告（summary.md）/ 压缩包均在且一致
- get_report 返回真实存在的压缩包路径；MCP 重启后读取一致；原目录清理后（card_test 真实演练）从压缩包读取一致
- get_run 与 get_report 终态一致（PASSED == PASSED）；报告不再返回不存在的默认路径（原目录缺失时 report_paths 全 null 并给出压缩包 archive_member）

## 未解决问题

- 非 P0 遗留：两个既有 generated 同名收集冲突（未删文件）；`tests/generated/junit.xml` 为测试运行产物；ruff 全仓 11 个既有告警（HEAD 基线 12，本轮改动文件全绿）；mypy 14 个既有错误（== HEAD 基线）。
## 外部环境风险与服务现状

- 游戏进程曾被未知触发以无调试环境变量重启（导致一次 card_test 失败留证）；已按一次性方式带 `STS2_ENABLE_DEBUG_ACTIONS=1` 重启。Windows 截图“代码有支持、真实环境未验收”。
- 公共 MCP（8090）在验收结束后已停止，当前无监听；“服务位于 8090”不再成立。历史上“MCP 重启后仍可读取历史报告”的能力已真实验证，需要时可按本文档中的命令重启恢复。
- 是否影响 P0 结论：否。

## 交付状态

- P0 功能与真实验收：🟢 PASSED
- 证据闭环：🟢 PASSED
- 仓库交付固化：🟡 PENDING（源码、检查与本文档待精准提交并推送，见下一步）
- Windows 真实截图验证：🟡 非 P0 遗留

## 工作区

- 当前分支：main（399c1a3，与 origin/main 一致）
- 原有未提交内容：全部保留（用户文档/配置 + codex 本轮源码与测试改动）
- 本轮新增修改：上述源码 5 文件、测试 5 文件、文档 3 文件
- 是否提交代码：否
- 是否推送代码：否
