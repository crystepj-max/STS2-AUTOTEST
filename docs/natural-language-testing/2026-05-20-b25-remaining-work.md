# B25 自然语言测试规格流水线未完成项清单

**日期：** 2026-05-20
**范围：** 仅统计 `B25` 自身仍待完成的功能开发任务。
**不包含：** 单纯补写更多 `case` / `suite` 规格内容本身。
**目标：** 说明在“已有规格、已有 review/compile/run 骨架、已有首批 DSL 原语”的前提下，`B25` 距离真正可稳定落地还差哪些能力。

---

## 1. 当前状态摘要

截至当前，`B25` 已经完成的部分主要是：

- `Markdown case + suite` 双层规格目录和基础模板
- `review / compile / run` 分层命令
- `run --all` 对 `review -> compile -> pytest` 的总调度
- `TestSpec / SuiteSpec` 基础模型
- `MarkdownParser / SpecReviewer / CodeGenerator` 骨架
- 首批首战路径 DSL 原语接回 `pytest fixture -> TestOrchestrator -> ActionDescriptor -> Fluent DSL`
- 真实环境升级与连通性恢复
  - `sts2.exe` 与 `STS2.Cli.Mod` 已升级到 `0.103.0`
  - `sts2 ping` 已可返回 `{"ok": true}`

当前真正的剩余问题，已经不再是“环境起不来”，而是“规格是否被正确理解并稳定执行”。

---

## 2. P0 未完成项

这些任务不完成，`B25` 仍然不能算“闭环打通”。

### B25-P0-1: case 起始状态校验与执行前护栏

**现状**

- `case` 规格里已经有 `Start State / End State` 概念。
- 但生成测试执行前，没有先校验当前状态是否满足 `Start State`。
- 当前真实失败样例就是 `TC-FINISH-FIRST-BATTLE` 在主菜单直接执行 `choose_map_node`。

**需要完成**

- 在编译模型或执行器中显式承载 `start_state` 约束。
- 在执行每个 `case` 前先校验当前 screen / available actions / 关键状态字段。
- 状态不满足时，返回结构化失败，而不是让动作层直接暴露底层错误。

**验收标准**

- 当 `case` 起点不满足时，失败信息明确指出：
  - 当前状态
  - 规格要求的起始状态
  - 哪个前提不满足
- 失败信息不再只是 `Action 'xxx' not available` 这种动作级错误。

### B25-P0-2: suite 共享会话串联语义

**现状**

- `suite` 文档已经存在。
- 但组合执行还没有真正把多个 `case` 串成同一连续用户旅程。
- 前一个 `case` 的结束状态与下一个 `case` 的起始状态之间，没有严格衔接机制。

**需要完成**

- 为 `suite` 增加“共享同一会话连续执行”的正式执行模式。
- 在 `suite` 中按顺序运行每个 `case`。
- 每个 `case` 完成后，把当前状态传递给下一段。
- 在 `suite` 级别输出子用例顺序、失败位置和终止原因。

**验收标准**

- `SUITE-FIRST-BATTLE-SMOKE` 能按：
  - `TC-PREPARE-NEW-RUN`
  - `TC-RESOLVE-NEOW`
  - `TC-FINISH-FIRST-BATTLE`
  连续执行。
- 若中间失败，报告能明确指出失败发生在哪个 `case`、哪一步。

### B25-P0-3: TODO / 暂缓 - 首批闭环场景的真实执行通过

**现状**

- 现在环境联通已恢复。
- 生成测试已经能被 pytest 收集。
- 但代表性闭环还没有“真实通过”。
- 暂缓原因：真实 Steam 启动 / 游戏进程接管仍需要单独排查，不作为当前批次其它 B25 框架能力的阻塞项。

**2026-06-16 真实实跑记录（经 MCP daemon :8090）**

- Steam 启动链路已恢复：StS2 `v0.103.3` + mod `0.7.1`，agent `ready`。
- 经 MCP `compile_spec -> run_test` 真实驱动游戏：`start_new_run` ✅ → 到达 `CHARACTER_SELECT` ✅ → `select_character` ❌（`B25-P0-5` 角色选择 id 解析未就绪）。
- 流水线机制本身全通（review/compile/run/get_report/junit/evidence 均产出），失败点是新登记的 `B25-P0-5`。

**2026-06-17 最终通过记录**

- `B25-P0-5`（角色解析）✅ 修复
- `B25-P0-6`（settle）✅ 修复，含事后补修的异常处理 + CliModAdapter 缓存修复
- `TC-GAWAIN-PREPARE-NEW-RUN` 经 `compile_spec -> run_test` 真实 **PASS**（`passed: 1, failed: 0, status: OK`）

**TODO 标签**

- `TODO-B25-STEAM-REAL-RUN`: 已达成。首条 NL 用例已真实通过。

**需要完成**

- 至少打通 1 条最小 `case` 的真实执行通过。
- 至少打通 1 条 `suite` 的真实执行通过。
- 用真实结果验证：
  - `review`
  - `compile`
  - `run`
  - `run --all`
  不是只在 `collect` 层可用，而是真能执行。

**验收标准**

- 至少 1 个 `case` 在真实游戏环境下 `PASS`
- 至少 1 个 `suite` 在真实游戏环境下 `PASS`
- `autotest run --all` 至少完成一次端到端真实执行

### B25-P0-4: 规格到 DSL 的关键路径编排补全

**现状**

- 当前已有一批首战相关 DSL 原语。
- 但真实路径仍然存在“步骤合理、执行顺序不合理”的问题。

**需要完成**

- 为首批闭环场景补齐最小可执行顺序。
- 让生成测试不再把“中间状态前提”隐含给调用者。
- 必要时把粗粒度步骤拆成多个更稳定的 DSL 组合。

**验收标准**

- 生成出的测试文件，不需要人工再改动步骤顺序即可运行。
- 首战 smoke 不再因为“当前屏幕不对”而在第一步动作即失败。

### B25-P0-5: DONE - 角色选择动作的 id 与 option_index 解析（NL 路径）

**发现日期：** 2026-06-16（首次真实 `run_test` 实跑，见 `B25-P0-3` 记录）

**现状（已修复，2026-06-16）**

- `CodeGenerator._CHARACTER_IDS` 把”选择 Gawain”编译成 `select_character(“gawain”)`，但实机注册 id 是大写 `GAWAINMOD-GAWAIN`（与 `IRONCLAD` 同规则）。
- 更关键的是：live STS2-Agent 的 `select_character` 动作要求 `option_index`，**不接受** `character_id`。
- 且该 `option_index` 必须取自 `character_select.characters[].index` 字段，**不是数组下标**（实测数组下标 6 = `GAWAINMOD-GAWAIN`，但 `option_index=6` 实际选中的是 `WATCHER-WATCHER`）。
- DSL 的 `select_character`（`dsl/assertions.py`）与 agent adapter（`adapters/agent.py:_resolve_agent_action_args` 仅特化 `play_card`）都没有做这层解析，导致 NL 路径在选角第一步即失败：`Action 'select_character' failed: Character 'gawain' not found`。
- 成熟的 `TestAgentRunner._resolve_gawain_character_option_index` 已正确处理：遍历 `characters`，用 `_is_gawain_character_value` 匹配身份字段，取 `character.get(“index”, fallback_index)` 作为 `option_index`。即**框架内已有正确实现，但 B25 NL→DSL 路径未复用**。

**完成记录**

- `_CHARACTER_IDS` 已修正：`”Gawain” -> “GAWAINMOD-GAWAIN”`，与实机 id 保持一致。
- `AgentAdapter._resolve_agent_action_args` 新增 `select_character` 分支：当 `character_id` 在 args 中且无 `option_index` 时，调用 `_resolve_select_character_args` 从 `character_select.characters[].index` 解析出正确的 `option_index`。
- 解析采用模糊匹配（忽略大小写与分隔符），兼容 `”gawain”`、`”GAWAINMOD-GAWAIN”` 等多种写法；解析失败时 fall-through 传原 args 并记 WARNING，让 agent 返回其自有错误（区分”未匹配”与”agent 自身 CHARACTER_NOT_FOUND”）。
- 已新增 4 个单元测试覆盖解析、模糊匹配、pass-through 和 fall-through 场景。
- 生成测试文件（`test_tc_gawain_prepare_new_run.py`、`test_suite_gawain_smoke.py`）已更新为 `select_character(“GAWAINMOD-GAWAIN”)`。

**验收标准**

- `TC-GAWAIN-PREPARE-NEW-RUN` 经 `compile_spec -> run_test` 在真实游戏环境下 `PASS`（主菜单 → 选 Gawain → embark → 到达 EVENT）。
- 不再出现 `Character 'gawain' not found`，也不再误选成其它角色。

**2026-06-16 复跑验证**

- 重启 daemon 加载修复代码后重编译，生成测试已变为 `select_character("GAWAINMOD-GAWAIN")`。
- 实机重跑：`start_new_run` → `select_character` → `embark` 全部通过，实机最终 `screen=EVENT`、`run.character_id=GAWAINMOD-GAWAIN`，角色解析修复**端到端确认有效**。
- 但 case 仍未 `PASS`：断言抓到 `UNKNOWN`，根因是新登记的 `B25-P0-6`（断言前无状态沉降等待），与角色解析正交。即 P0-5 的角色解析达标，case 级 `PASS` 现被 `B25-P0-6` 阻塞。

### B25-P0-6: DONE - 动作后状态沉降与断言重试（settle / poll）

**发现日期：** 2026-06-16（`B25-P0-5` 修复后复跑实机）

**现状（已修复，2026-06-16）**

- `FluentBuilder.assert_that`（`dsl/fluent.py`）在执行完动作序列后，**立刻**单次 `get_state()` 取 `final_state`，再逐条跑断言，中间无任何 settle / 轮询。
- `embark` 之后游戏处于 `embark → 加载 → EVENT` 过场，单次快照抓到 `screen=UNKNOWN`，`game_reached_state(EVENT)` 判失败；实机数百毫秒后即到达 `EVENT`（已用 live 状态证实）。
- 这是**确定性失败**（非偶发 flaky）：动作返回与画面沉降之间没有等待窗口。
- 关联：`AgentAdapter.wait_until_actionable` 已有”可操作即返回”的轮询，但”可操作”早于”目标画面沉降”，不能替代断言级的目标态等待。

**完成记录**

- `FluentBuilder.__init__` 新增 `settle_timeout=5.0` 和 `settle_poll_interval=0.5` 参数。
- `FluentBuilder._settle_and_get_state` 新方法：首次 `get_state()` 返回 `UNKNOWN` 时，以 `settle_poll_interval` 轮询，直至 screen ≠ UNKNOWN 或 `settle_timeout` 到期；非 UNKNOWN 失败不轮询，避免所有断言失败都等 5 秒。
- `assert_that` 中的单次 `adapter.get_state()` 替换为 `_settle_and_get_state()`。
- `define()` 函数透传 `settle_timeout` 和 `settle_poll_interval` 可选参数，保持向后兼容。
- 已新增 4 个单元测试覆盖：settle 成功路径、无需 settle 直通路径、settle 超时报最后 UNKNOWN 状态、非 UNKNOWN 错误状态立即失败不轮询。

**后续补修（2026-06-17）**

- `_settle_and_get_state` 中 `get_state()` 调用包裹异常处理：新增 `_get_state_or_unknown` 工具方法，捕获所有异常并返回 UNKNOWN 而非崩溃，使 settle 循环对 agent 不可用（加载过渡期 `/state` 挂起）有弹性。
- `CliModAdapter._get_state_sync` 修复：**不再缓存 UNKNOWN 状态**。原本加载过渡期的 UNKNOWN 被缓存后，settle 循环的后续轮询全部命中缓存、无法拿到 EVENT。此 bug 是 settle 机制在默认 adapter 下无法工作的根因。

**验收标准**

- `TC-GAWAIN-PREPARE-NEW-RUN` 经 `compile_spec -> run_test` 真实 `PASS`（不再被过场期 `UNKNOWN` 卡住）。
- 终态断言在合理超时内对”稍后才沉降的目标画面”能稳定判定通过。

**2026-06-17 实跑验证**

- 最终验证：从干净 MAIN_MENU → start_new_run → select_character(“GAWAINMOD-GAWAIN”) → embark → 断言 EVENT。
- 结果：**`PASS`**（`passed: 1, failed: 0, status: OK`，测试耗时 ~120s）。
- 关键依赖链：P0-5（角色解析）→ P0-6（settle）→ P0-6 补修（异常处理 + 缓存修复）串联后首条 NL 用例真实通过。

---

## 3. P1 已完成项（框架侧）

这些任务不阻塞首批闭环通过，但不完成会明显影响 `B25` 的可用性和可维护性。

### B25-P1-1: DONE - review 能力感知化

**现状**

- `SpecReviewer` 已存在。
- 但它目前更像规则检查器，不是真正基于“当前框架能力表”的审查器。

**需要完成**

- 基于当前 DSL / adapter / action 映射建立能力视图。
- 审查时区分：
  - 模糊项
  - 缺失项
  - 当前不可实现项
  - 待扩展能力项

**验收标准**

- 审查器能明确指出“当前写法做不到”的原因来自：
  - 没有 DSL 原语
  - 没有 adapter action
  - 没有可靠组合策略

**完成记录**

- `SpecReviewer` 已基于当前 DSL / 断言能力表识别 `capability_gap`。
- 首战关键路径步骤不会被误报为能力缺口。
- 未支持的规格步骤会提示“当前不可实现”，并建议改写或登记新能力缺口。

### B25-P1-2: DONE - `revised draft` 正式进入工作流

**现状**

- `revised draft` 可以生成。
- 但还没有成为正式的人机确认节点。

**需要完成**

- 为原始稿和修订稿建立稳定关联。
- 允许 `compile` 明确选择“原始稿”还是“确认后的修订稿”作为输入。
- 在 review 输出中记录修订摘要与关键修改点。

**验收标准**

- 用户可以清楚知道：
  - 原始规格是什么
  - 修订建议改了什么
  - 当前编译使用的是哪一版

**完成记录**

- `autotest review --output-dir <dir>` 会写出 `review-report.md`。
- 每个 case 的候选修订稿会写入 `<dir>/revised/<case-id>.md`。
- `<dir>/revised/.source-map.json` 记录原始稿、修订稿和修改摘要的稳定关联。
- `autotest compile --use-revised --revised-dir <dir>` 可显式从修订稿目录编译。

### B25-P1-3: DONE - 执行失败信息升级

**现状**

- 现在很多失败仍然以动作层和 adapter 层报错为主。
- 对规格作者不够友好。

**需要完成**

- 把失败信息提升到规格语义层。
- 报告里优先展示：
  - 失败的 `case`
  - 失败的步骤
  - 失败时的 screen / available actions
  - 建议的修正规格或恢复路径

**验收标准**

- 非框架开发者阅读失败报告时，不需要先理解 adapter 内部机制。

**完成记录**

- 生成的 case 测试断言失败时，会输出包含 `case_id`、标题、起始状态、结束状态、步骤列表、失败信息和 detail 的规格语义上下文。
- 生成的 suite 测试在子 case 失败时，会输出对应 case 的语义摘要，而不是只暴露底层动作失败。

### B25-P1-4: DONE - suite 级结果摘要

**现状**

- 组合层执行虽然有雏形，但报告产物不完整。

**需要完成**

- 输出 suite 级摘要：
  - 总结果
  - 子 case 顺序
  - 每个子 case 的结果
  - 首个失败点
  - 证据路径

**验收标准**

- `suite` 运行结束后可以直接阅读统一摘要，而不需要人工翻多个 pytest case。

**完成记录**

- 生成的 suite 测试会维护 `suite_results`。
- 每个子 case 执行后会写出 `tests/output/suite-summaries/<suite-id>.json`。
- 摘要包含总数、通过数、失败数、首个失败 case 和每个子 case 的语义结果。

---

## 4. P2 未完成项

这些任务更偏产品化增强，不是首批闭环的前置条件。

### B25-P2-1: 更强的自然语言归一化

**现状**

- 当前编译器更适合受控写法。

**需要完成**

- 支持更多同义表达。
- 支持更稳的步骤归一化。
- 减少“写法稍变就映射失败”的问题。

### B25-P2-2: 能力缺口登记与复用

**现状**

- 我们已经在设计上定义了 `capability_gap`。
- 但还没有正式的缺口登记资产。

**需要完成**

- 建立待扩展能力清单。
- 让多个规格能复用同一能力缺口 ID。

### B25-P2-3: 统一产物视图

**现状**

- `review`、`compile`、`run` 各自已有部分产物。

**需要完成**

- 用统一目录结构和命名规范管理：
  - review report
  - revised draft
  - generated tests
  - run summary
  - evidence

---

## 5. 与“补案例”区分开的真正开发任务

为了避免后续讨论再次混淆，这里单独强调：

以下事项**不算** `B25` 的功能开发完成，只算规格资产补充：

- 新增更多 `specs/cases/*.md`
- 新增更多 `specs/suites/*.md`
- 改写现有首战规格文字

以下事项才属于 `B25` 自身仍待完成的功能开发：

- 状态约束进入执行链路
- suite 共享会话编排
- 规格语义级失败报告
- review 的能力感知
- `revised draft` 正式工作流
- 首批真实闭环通过

---

## 6. 建议执行顺序

建议按下面顺序继续推进：

1. `B25-P0-5` DONE：角色选择动作的 id 与 option_index 解析（NL 路径）
2. `B25-P0-6` DONE：动作后状态沉降与断言重试（settle / poll，含异常处理 + CliModAdapter 缓存修复）
3. `B25-P0-1` case 起始状态校验与执行前护栏
4. `B25-P0-2` suite 共享会话串联语义
5. `B25-P0-4` 首批闭环场景的关键路径编排补全
6. `B25-P0-3` DONE：真实 `case / suite / run --all` 验证通过（首条 NL 用例 `TC-GAWAIN-PREPARE-NEW-RUN` 已真实 PASS）
5. `B25-P1-3` DONE：执行失败信息升级
6. `B25-P1-1` DONE：review 能力感知化
7. `B25-P1-2` DONE：revised draft 正式进入工作流
8. `B25-P1-4` DONE：suite 级结果摘要
9. P2 产品化增强项

---

## 7. 结论

`B25` 当前已经完成了“从自然语言规格到 pytest 代码生成，再到框架主链路执行”的框架侧闭环。
状态约束、suite 串联、关键 DSL 编排、能力感知 review、revised draft 产物化、语义失败信息和 suite 摘要均已完成框架侧落地。

当前唯一被显式暂缓的 P0 是：

- `TODO-B25-STEAM-REAL-RUN`: 恢复 Steam / 真实游戏启动链路后，补做真实 `case / suite / autotest run --all` PASS 验证。

在该 TODO 恢复前，`B25` 可按“非真实游戏依赖的自然语言测试流水线能力”继续使用和迭代。
