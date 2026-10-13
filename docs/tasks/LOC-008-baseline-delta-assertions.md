> **轨道归属（2026-10-12 追加，未改动下文任何原文）**：本条是 Gawain 侧上报缺口 **⑤（断言 DSL 缺「增量基线」语义）** 的**权威定义轨道**。
> 已在 `docs/tasks/registry.json` 登记（状态「定义中」）。GitHub 本批只开了 #103–#106（对应上报清单的第 3/4/9 条与一条新缺陷），
> **不含本条**——为避免与本稿重复建项，本条**不在 GitHub 另开 issue**，推进与拍板都在本稿进行。
> 校正一处上游前提：`hand_size_changed_by` / `player_block_increased_by` 等**绝对增量原语已存在**且有生成器映射，
> 真缺的是「较步骤 N 基准」的**多参照系**；下文「实测证据」第 2–5 点已按此口径记录，引用时以本稿为准。

> 本任务属本地轨道，GitHub 恢复后需补建 issue。
> 上游输入：`STS2-GAWAIN/automation/docs/spec-gate-blocked-attribution-20261009.md` §4 REQ-01（二手证据）。本文件的「实测证据」全部由本次会话在 AUTOTEST 仓 `.venv` 内重新独立复算，未直接沿用上游结论；与上游不符处已在文中显式标注。
> 基线确认依据：用户在任务指令中授权「落成可开工的需求条目（含验收判据），不写实现代码」。

# LOC-008 基线捕获与相对（增量）断言语义

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-008 |
| 需求来源 | `STS2-GAWAIN` 门禁阻断归因（上游 REQ-01） |
| 任务类型 | enhancement（DSL 语义扩展） |
| 优先级 | P0（开工顺序排在 LOC-009 之后，理由见「优先级」节） |
| 当前状态 | 定义中（未登记 `registry.json`，见「不在范围」末条） |
| 需求基线 | V1 |
| 前置依赖 | LOC-009（门禁 fail-closed）——否则本条的验收判据无法自证 |
| 施工环境组 / 角色 | 独立 |
| 无人值守许可 | 不允许（含公共 API 签名决策，见「涉及范围」的接缝选择） |
| GitHub 同步 | pending |

## 背景

Gawain 用例大量用「较步骤 N 基准 增加/下降/不变 X」这类**相对断言**描述卡牌效果的期望值。门禁把这类期望判为能力缺口，消息形如：

```
当前没有断言 DSL 可稳定验证该期望: 敌人 HP 较步骤 4 基准下降 4
```

这类缺口在门禁读数里是最大的一族（A 类 34 条中的 DELTA 桶）。它不是"某个指标读不到"，而是**整个 DSL 缺一层时间参照系**：断言只能对"最终状态"说话，说不出"和中间某一步的自己比"。

## 实测证据

复现方式（零副作用，全程不写仓内文件）：用 `D:/STS2-WORKSPACE/STS2-AUTOTEST/.venv/Scripts/python.exe` 直接 import `sts2_autotest`，并只读调用 `STS2-GAWAIN/automation/autotest/lib/spec_gate.py` 的纯函数 `inspect_specs()`（不调 `main()`，因 `:121` 会回写报告文件）。临时产物见文末「复现物位置」。

1. **门禁读数已自验，与上游一致**：`case_count=60 / suite_count=35 / blocked_case_count=14 / problems=107 / status=BLOCKED`；problem 一级分类实测 A=34、D=34、B=23、C=13、F=2、E=1，合计 107。
2. **断言拿不到第二个参照点**。`AssertionFn = Callable[[GameState], tuple[bool, str]]`（`src/sts2_autotest/dsl/assertions.py:16`）——单入参。运行侧同样只喂一个 state：`src/sts2_autotest/dsl/fluent.py:254-255` `for assertion in assertions: ok, msg = assertion(final_state)`。签名里没有位置表达"和步骤 N 比"。
3. **"before"值的地基确实存在，但形态被钉死**。`dsl/fluent.py:36-42` 的 `_TRACKED_PREVIOUS_FIELDS` 是**封闭的 5 项**：`previous_hp / previous_energy / previous_block / previous_hand_count / previous_enemy_hp`；由 `dsl/fluent.py:251` `final_state = _merge_previous_snapshot(before_state, final_state)` 注入到最终状态上（合并函数 `dsl/fluent.py:56-74`）。而 `before_state` 只有**一个抓取点**——execute 阶段第一个动作之前：`dsl/fluent.py:222-223`（`execute_action_sequence(self._execute_actions, on_first_pre_state=_capture_before)`，捕获器 `dsl/fluent.py:181-183`）。
   ⇒ 结论：参照系**不是没有，是只有一个、匿名、位置固定、指标封闭**。用例要的"步骤 4 基准""步骤 10 后基准""步骤 8-9-10 逐段净增量"三者都无法用现有 5 个槽位拼出。
4. **比较符是 `>=`，表达不了精确值与不变式**。`dsl/assertions.py:104` `ok = actual >= amount  # >= because RNG can cause extra damage`；缺基准时直接失败返回 `dsl/assertions.py:102`。因此「较步骤 4 基准**不变**」（用例 4）和「格挡**等于**基准 B」（用例 8）在当前原语下没有等价写法。
5. **敌人 HP 是聚合口径**。`dsl/assertions.py:44-49` 的 `_resolve_enemy_field` docstring 明确："For `hp` we use the total HP of all alive enemies"。用例 3 要的是「所有敌人 HP **各**较基准下降 12」——聚合总量口径会假通过（3 敌各掉 4 与 1 敌掉 12 在总量上不可区分）。
6. **储能不在上述 5 项里**（`dsl/fluent.py:36-42` 无 storage 类字段）。状态负载侧是否暴露储能，本条**未自验**，见「未核实」。
7. **生成侧与白名单侧都没有相对语义条目**：`core/code_generator.py:104-171` `_assertion_to_call` 的全部 13 个分支都是即时绝对值，落空即 `:171` `# TODO: implement assertion for '{assertion}'`；`core/spec_reviewer.py:52-69` 的 13 条断言正则同样无「较/基准/不变」条目。**这直接证实上游"全仓生成路径无 baseline/record 概念"的说法**：在 `src/sts2_autotest/{dsl,core,common}` 内 grep `baseline|record` 只命中注释与无关的 combat policy 命名，无原语。
8. **C 类派生标记会随本条自动消失**：`spec_gate.py:67` 只检 `TODO: implement assertion` / `# no assertions defined`（后者由 `core/code_generator.py:382` 写出），前者由 `:171` 写出。A 类一旦可映射，同一用例的 C 类同时归零——实测 14 个 BLOCKED 用例中，用例 1-4、12 的 reasons 恰为「1 条 A + 1 条 C」。

## 定义（任务目标）

在 NL 规格流水线里补一层**步骤级具名基线**，并让断言能相对基线做**精确**比较。规格作者的写法语义（保持现有中文措辞，不要求 Gawain 改文案）：

| 规格写法 | 语义 |
|:---|:---|
| `较步骤 N 基准 增加 X` | 在第 N 步边界读一次值 B；末值 `cur`；判 `cur - B == X` |
| `较步骤 N 基准 下降 X` | 判 `B - cur == X` |
| `较步骤 N 基准 不变` | 判 `cur == B`（增量 0） |
| `等于基准 <名称>` | 判 `cur == B`，用于"未被引擎清零"这类保留性断言 |
| `步骤 8-9-10 中净增量分别为 +1、+1、0` | 同一指标的**链式多点**基线，逐段各判一次 |

必须同时具备：

- **多槽**：一个用例内可存多个基线（键为步骤号或名称），生命周期 = 单用例。
- **具名/可寻址**：断言能指名它比的是哪一个基线。
- **per-entity 维度**：至少敌人 HP 支持「逐个敌人各判」与「全体总量」两种口径，缺口径时**默认更严的那个**，不得默认聚合。
- **指标面**：玩家 HP、玩家格挡、手牌数、（每个）敌人 HP。储能**不在本条**，见「不在范围」。

### 接缝选择（需负责人拍板，本条不代定）

三种可选落地形态，代价不同，按 AGENTS.md「需要改 public API 名称/签名/导出类型 → 必须暂停请求决策」登记：

- **(i) 扩展 `AssertionFn` 签名**，让断言多拿一个基线容器。最干净，但改公共类型，波及 `dsl/assertions.py` 全部 13 个原语与既有单测。
- **(ii) 沿用现有 `previous_*` 注入风格**，把基线以 `previous_<名称>_<指标>` 之类的动态键 `model_copy(update=...)` 挂到 `GameState`（`common/state.py` 的 `extra="allow"` 允许）。不改公共签名、最贴近现状；代价是字段名拼接、非类型化，且必须避免与既有 5 个固定键撞名。
- **(iii) 生成期把基线值烘成字面量**。经实测**不可行**：基线值只有真正跑完第 N 步才知道，而生成发生在执行之前（`spec_gate.py:66` 生成 → `:70` `ast.parse` 校验）。登记于此仅供后人不要再走这条路。

## 涉及范围

`core/spec_models.py`（步骤边界与基线声明的承载模型）、`core/markdown_parser.py`（解析「较步骤 N 基准」措辞）、`core/spec_reviewer.py:52-69`（断言白名单补相对语义条目）、`core/code_generator.py:104-171`（映射为真实断言调用，**并移除该族的 TODO 兜底**）、`dsl/fluent.py:36-42 / :56-74 / :222-223`（多槽基线捕获与注入）、`dsl/assertions.py`（新增精确/不变/等值/per-entity 断言）、`tests/unit/`（与上述 1:1 对应）、`docs/natural-language-testing/`（语义说明）。

外部依赖：无游戏环境要求；本条可完全离线用单测证明（AGENTS.md 测试优先级 1）。

## 能解锁哪几个用例

| 用例 | 现阻断 reason 组成 | 本条单独能否转 READY |
|:---|:---|:---|
| `TC-GAWAIN-COMMAND-BARRAGE-ORDER-1M` | A(敌人HP较步骤4下降4) + C | ✅ 转 READY |
| `TC-GAWAIN-COMMAND-BARRAGE-ORDER-2M` | A(较步骤7下降8) + C | ✅ 转 READY |
| `TC-GAWAIN-COMMAND-BARRAGE-ORDER-3M-AOE` | A(各较步骤11下降12) + C | ✅ **仅当含 per-entity 维度**，否则仍假通过 |
| `TC-GAWAIN-COMMAND-BARRAGE-ORDER-NO-OFFENSE` | A(较步骤4不变) + C | ✅ 仅当含「不变」等值语义 |
| `TC-GAWAIN-COMMAND-LOGISTICS-1M` | A(储能) + A(手牌数) + C | ❌ 卡在储能通道 |
| `TC-GAWAIN-COMMAND-LOGISTICS-3M-DRAW` | A(储能) + A(手牌数) + C | ❌ 同上 |
| `TC-GAWAIN-POWER-RELAY-EQUIPMENT-BUFF-USES` | A(步骤8-9-10逐段储能净增量) + C | ❌ 储能 + 需链式多点基线 |
| `TC-GAWAIN-COMMAND-MEDICAL-PROTOCOL-2M-NO-DEBUFF` | B(set_hp 措辞) + A(玩家HP较步骤7) + A(无报错无崩溃) + C | ❌ 还需上游 REQ-06 的两条 allowlist 修正 |
| `TC-GAWAIN-COMMAND-STEEL-FORTRESS-3M-RETAIN` | B(wait_until_actionable) + A(较步骤10增加15) + A(等于基准B) + C | ❌ 还需 WAIT 步骤原语 |

⇒ **本条单独：4 个用例（BARRAGE 四件套）。叠加储能读数后：+3 个 = 7 个，即上游所称「14 个 BLOCKED 中的 7 个」的可达成路径。** 该结论已由上表逐条 reasons 复核成立。

## 优先级

P0。但**开工顺序必须排在 LOC-009（门禁 fail-closed）之后**：

- 本条的验收判据全部形如"用例 X 从 BLOCKED 转 READY"，而"READY"当前由一个 fail-open 的门禁给出（见 LOC-009）。门禁不可信时，"转 READY"并不蕴含"步骤真的映射上了"——本条会失去自证能力。
- LOC-009 落地后，本条若漏配某条生成映射，会被门禁立刻硬报错拦住；否则漏配会静默变成假 READY。
- 业务价值上本条最高（独占 4 个用例解锁、覆盖 A 类 34 条中的 9 条），故仍列 P0，仅次序让行。

## 验收标准

1. `BARRAGE-ORDER-1M / 2M / 3M-AOE / NO-OFFENSE` 四个用例在 `spec_gate.py review` 输出中 `status` 由 `BLOCKED` 变 `READY_TO_COMPILE`，且各自 `reasons` 为**空数组**（A 与派生 C 同时消失）。
2. 运行级读数可复算：`blocked_case_count` 由 14 降至 ≤10；`problems` 由 107 降至 ≤99（本条消 4 条 A + 4 条 C = 8 条）。
3. 四个用例的生成码 `.assert_that(...)` 内**不再出现 `# TODO:`**，而是真实断言调用；该结论由 AST 断言，不得用文本 grep 代替。
4. **per-entity 反证用例**：构造三个敌人 HP 为 `40/40/40` 与 `40/40/16` 两组状态，对「各下降 12」的断言必须分别判 pass / fail。若实现走聚合总量口径，此条必然失败——用它锁死用例 3 不假通过。
5. **不变式反证用例**：对「较步骤 4 基准不变」的断言，注入"敌人 HP 下降 1"的状态变化，必须判 fail（证明未被 `dsl/assertions.py:104` 的 `>=` 语义恒真放行）。
6. **等值基准反证用例**：对「格挡等于基准 B」，构造"回合开始格挡被引擎清零"的状态，必须判 fail。
7. **多槽不串味**：同一用例内声明两个不同步骤的基线，断言指名其一；改动另一个步骤的读数不得影响该断言结果。
8. **反面要求（硬门）**：数据缺失时**禁止 `return True` 静默跳过**。已知反面范例 `STS2-GAWAIN/automation/docs/closing-dsl-capability-gaps.md:184-200` 的 `storage_changed_by` 草案写了 `if previous is None or current is None: return True, ""  # skip - data not available`——本条不得沿用该模式；缺基线必须判 fail 或保持 BLOCKED。依据：`spec_gate.py:1` 模块意图「拒绝不完整的自动测试」。
9. 默认验证三件套通过：`python -m pytest tests/unit/ -q`、`mypy src/sts2_autotest --strict`、`lint-imports`。

## 不在范围

- **魔网储能读数通道**（上游 REQ-02）：储能是自定义 MOD 资源，读取通道独立立项。本条不实现、也不声明对用例 5/6/12 的解锁。
- 截图步骤与证据包断言（上游 REQ-03/04）、弹窗选择原语（REQ-05）、套件级 Then 生成（REQ-07，见 LOC-010）、其余步骤原语（REQ-08）、NOT_RUN 元语义（REQ-09）。
- 门禁 fail-closed 本体（LOC-009）。
- ** Gawain 侧规格债**：卡 id 暂定命名未回填（上游 §5 G9）——门禁结构性不校验，属本仓外，本条不代改文案。
- 本文件**未**写入 `docs/tasks/registry.json`、**未**重生成 `docs/tasks/BOARD.md`（该文件首行声明自动生成）。登记与看板刷新请由任务定义入口或负责人执行，避免与并行会话争同一文件。

## 未核实（不外推）

- 储能字段是否已进入 `/state` 状态负载：本条只自验了它不在 `dsl/fluent.py:36-42` 的 5 个固定键内。
- 上游对 A 类 34 条的 (a)/(b) 分档：上游明确只把 23 条**步骤**原文喂过生成器，**A 类断言未逐条实调**（其 §7 自述）。本条同样未逐条实调，因此用例 7、8 是否还藏着其他假缺口，未核实。
- 三条候选接缝中 (i) 与 (ii) 的改动面大小仅按现有代码结构推断，未做原型验证。

## 复现物位置（临时，不入库）

`C:/Users/22487/AppData/Local/Temp/reqb-repro/`（`repro.py`、`repro2.py`、`repro3.py`、`repro4.py` 与 `specs-case-only/`）。全部只读调用仓内代码，未修改 `STS2-AUTOTEST` 或 `STS2-GAWAIN` 任何文件。
