> **轨道归属（2026-10-12 追加，未改动下文任何原文）**：本条是 Gawain 侧上报缺口 **②（门禁 fail-open：取不到就跳过不报错）** 的**权威定义轨道**。
> 已在 `docs/tasks/registry.json` 登记（状态「定义中」）。GitHub 本批只开了 #103–#106（对应上报清单的第 3/4/9 条与一条新缺陷），
> **不含本条**——为避免与本稿重复建项，本条**不在 GitHub 另开 issue**，推进与拍板都在本稿进行。
> 2026-10-12 独立复算补一条边界：机制确实成立（合成用例可产出「语法合法 + 无 TODO + 裸 `play_card` 未被 import」的生成码），
> 但**当前 Gawain 语料 0 触发**——318 步中 20 步落兜底、2 步裸名，全部先被 `spec_reviewer` 白名单拦下（放行 ∧ 兜底 = 0 条）；
> 两表之间无任何一致性测试。⇒ 属**潜伏缺陷**，估工时别按「今天已在假 READY」算。

> 本任务属本地轨道，GitHub 恢复后需补建 issue。
> 上游输入：`STS2-GAWAIN/automation/docs/spec-gate-blocked-attribution-20261009.md` §4 REQ-06b（二手证据）。本条**已按要求自行复现**，复现结果与上游有一处实质性不符，已在「与上游归因的差异」节如实登记。
> 基线确认依据：用户在任务指令中要求「(i) 自己复现一次（最小可复现输入 + 垃圾 action_type 真实产物作为证据）；(ii) 给出 fail-closed 应该长什么样的判据」。

# LOC-009 规格门禁 fail-closed：未映射步骤不得静默通过

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-009 |
| 需求来源 | `STS2-GAWAIN` 门禁阻断归因（上游 REQ-06b） |
| 任务类型 | bugfix（门禁完整性缺陷） |
| 优先级 | **P0，本清单第一条开工项** |
| 当前状态 | 定义中（未登记 `registry.json`，见「不在范围」末条） |
| 需求基线 | V1 |
| 前置依赖 | 无 |
| 施工环境组 / 角色 | 独立 |
| 无人值守许可 | 允许（判据明确、无公共 API 变更） |
| GitHub 同步 | pending |

## 背景

`spec_gate.py` 的作用是"拒绝不完整的自动测试"（模块 docstring `STS2-GAWAIN/automation/autotest/lib/spec_gate.py:1`），并在出现阻断项时**不向调用者提供可执行产物**（`:31`）。它的用例级判据只有三条：

- `:67` 生成码含 `TODO: implement assertion` 或 `# no assertions defined` ⇒ 阻断；
- `:70` `ast.parse(code)` 语法错误 ⇒ 阻断；
- `:61` 断言/步骤未命中 `spec_reviewer` 白名单 ⇒ 阻断。

问题在于**步骤侧的生成落空不留任何标记**。生成器对认不出的步骤有两类兜底产物，两者都语法合法：

- `core/code_generator.py:291` `return f"ActionDescriptor(action_type={step!r})"`——**把整句中文原文当 action_type**；
- `core/code_generator.py:286-289` 命中 `_STEP_TO_ACTION` 关键字后 `return action`，而该表 `core/code_generator.py:90-93` 实测只有 2 项：`结束回合 → "end_turn()"`、`使用 → "play_card"`。**第二项连括号都没有**，产出的是裸函数名。

于是：一条被白名单放行的落空步骤 ⇒ `ast.parse` 通过 ⇒ `:67` 不命中（那是断言侧标记）⇒ 用例判 `READY_TO_COMPILE`。

## 实测证据

全部为本次会话实测。解释器只用 `D:/STS2-WORKSPACE/STS2-AUTOTEST/.venv/Scripts/python.exe`，未向系统 Python 装包；只读调用纯函数 `inspect_specs()`，未调 `main()`（`:121` 会回写报告文件）；未跑 `dotnet build/publish`，未碰游戏 `mods/`。

### 1. 兜底产物实测（`_step_to_action_call()` 真实返回）

| 步骤原文 | 生成产物 | `ast.parse` |
|:---|:---|:---|
| `使用 控供能中继器装备增益到支持系仆从` | `play_card`（裸，无括号无参） | OK |
| `使用 紧急征召 触发满员挤出` | `play_card`（裸） | OK |
| `等待新回合开始（wait_until_actionable）` | `ActionDescriptor(action_type='等待新回合开始（wait_until_actionable）')` | OK |
| `弹出选择窗口` | `ActionDescriptor(action_type='弹出选择窗口')` | OK |
| `截取包含仆从队列与详情浮窗的画面。` | `ActionDescriptor(action_type='截取包含仆从队列与详情浮窗的画面。')` | OK |

（对照，行为正确者：`离开营火返回地图` → `ActionDescriptor(action_type="proceed")`，`core/code_generator.py:223-224`；`设置玩家生命 20` → `set_hp(20)`，`:275-277`。）

裸 `play_card` 的成因：`:267-269` 取卡 id 的正则是 `使用\s+([A-Za-z0-9_:-]+)`，**只吃 ASCII id**；中文动作描述不匹配，遂落到 `:286-289` 的关键字表拿到无括号的 `"play_card"`。

### 2. 端到端最小可复现（fail-open 确实发生）

输入 `TC-REQB-FAILOPEN.md`（临时目录，不入库）：

```markdown
## When
1. 进入首场战斗
2. 使用 控供能中继器装备增益到支持系仆从
3. 基础策略战斗

## Then
- 敌人受到 4 点伤害
```

实测输出：

- `spec_reviewer` 报的问题数 = **0** —— 步骤 2 命中白名单 `core/spec_reviewer.py:48` 的 `结束回合|使用\s+.+`（`使用` 后有空格即通过），断言命中 `:61`；
- `spec_gate.py:67` 的空断言标记命中 = **False**；
- `ast.parse(code)` = **OK**；
- 生成码（真实产物）：

```python
    result = (
        define("TC-REQB-FAILOPEN", autotest, _session_loop)
        .require_start_state("- 位于战斗", requirements={...})
        .setup(
            enter_combat(),
            play_card,          # <<< 裸函数名，不是 ActionDescriptor
        )
        .execute(
            combat_basic_policy(),
        )
        .assert_that(
            enemy_hp_decreased_by(4),
        )
    )
```

⇒ 门禁判定 **READY_TO_COMPILE**，而这一步根本不会执行。**fail-open 复现成功，上游归因不是误读。**

关键触发条件：`使用` 与中文描述之间**有一个空格**即被白名单放行。Gawain 现网用例里 `使用控供能中继器…`（无空格）当前是诚实 BLOCKED——这正是上游 §5 G6 警告"补空格会刷假 READY"的机理，本次已实测坐实。

### 3. 运行期失败模式（用真实 `TestOrchestrator.execute_action_sequence` 实测，桩适配器、短超时、不连游戏）

| 产物 | 运行期表现 | 出处 |
|:---|:---|:---|
| 裸 `play_card`（函数对象） | `AttributeError: 'function' object has no attribute 'action_type'`，瞬时，未归类到 6 类 `ErrorCategory` | `core/orchestrator.py:1443-1447` 循环内首次取 `action.action_type`；`dsl/fluent.py:244` 同样会取 |
| `action_type='弹出选择窗口'` | `STS2Error category=timeout_error`，**消息为 `Game not actionable before action 0: 弹出选择窗口`**，且要等满 `ActionDescriptor` 默认 `timeout=30.0`（`core/action_model.py:22`）才失败 | 轮询 `core/orchestrator.py:1516` → `:1039-1071`，判失败 `:1523` |
| `action_type='end_turn'`（对照） | 正常进入 `adapter.act()` | `core/orchestrator.py:1202` |

⇒ 两类垃圾在**运行期**都会炸，但形态都不指向真因：前者是未分类的 `AttributeError`，后者把"规格步骤没映射上"**误报成"游戏不可操作"**（环境/游戏侧口径）。

### 4. 当前爆炸半径实测为零（重要）

对 46 个 READY 用例的生成码做 AST 统计（按关键字实参取 `action_type`）：

- `ActionDescriptor` 的 `action_type` 取值共 12 处，全部为合法 ASCII 名：`choose_map_node_by_type`(4)、`win_combat`(2)、`collect_rewards_and_proceed`(2)、`enable_travel`(2)、`choose_neow_blessing`(1)、`give_block`(1)；
- **非 ASCII 的 `action_type` = 0**；
- `setup()/execute()` 直接收到裸函数名的用例数 = **0**。

⚠️ 计量陷阱（供后人）：按"行文本等于 `play_card,`"去统计会得到 **38**，那是 `_build_import_block` 生成的 import 列表行（`core/code_generator.py:73-77`，每行 `    play_card,`），**不是**裸步骤产物。必须用 AST 判定。

⇒ 结论修正：缺陷**当前是潜伏态**，Gawain 现网语料里还没有一个用例因此变成假 READY。风险敞口是一次文案改写或一次善意补白名单（见上游 REQ-06）。

### 5. 门禁读数自验（与上游一致）

`case=60 / suite=35 / blocked=14 / problems=107`；分类 A=34 / D=34 / B=23 / C=13 / F=2 / E=1 = 107；D 类涉及套件数 = 34（35−1，唯一幸免者为 `SUITE-GAWAIN-M1-MAGICWEB-RESONANCE`，它没有套件级 Then）。上游这些数字**可复算，未发现不符**。

## 与上游归因的差异（如实登记）

上游 REQ-06b 表述：「23 条被拒步骤中的 21 条，**实际已在生成**"看着能跑、实为 no-op"的测试」。实测两处需修正：

1. **"已在生成"不成立为运行事实**：那 21 条都挂在**已经 BLOCKED** 的用例上，而 `spec_gate.py:106` 在有阻断项时返回空 output、`:125-126` 直接退出码 2，产物根本不会落盘执行。准确表述应为"已存在于生成码中，但被 allowlist 挡在提交前"——即缺陷为**潜伏**，非现行。
2. **"实为 no-op"不准确**：两类产物在运行期**都会失败**（`AttributeError` / `timeout_error`），不是静默通过。真缺陷层次更窄也更关键——**是门禁（compile-readiness）层 fail-open，不是运行层静默**。它污染的是"READY 计数"与由此导出的估工，而非造成实机假通过。

上游对成因（`:67` 只检断言侧、`:291`/`:92` 产垃圾）的判断**完全正确**，仅严重度口径需要按上述两点收窄。

## 定义（任务目标）

让"步骤没有被真实映射"成为**门禁可识别的硬失败**，且不再依赖 allowlist 恰好也拒绝它。核心判据：**任何进入 `setup()`/`execute()` 的参数，必须是生成器已知语义的产物**；未知即报错，不得产出语法合法的占位物。

### fail-closed 应该长什么样（判据）

必须直接报错、不得静默生成的步骤/产物，逐条可测：

1. **`ActionDescriptor(action_type=<非 ASCII 字面量>)`** ⇒ 门禁阻断。合法 `action_type` 是有限的已知集合（ASCII，含 `end_turn`/`play_card`/`choose_*`/`proceed`/`win_combat` 等），把整句中文当 action_type 从来不是合法值。
2. **`setup()`/`execute()` 的实参是裸名（函数对象引用）而非调用/构造表达式** ⇒ 门禁阻断。判据应为 AST 级（`ast.Name` 而非 `ast.Call`），不得用文本匹配。
3. **`_step_to_action_call()` 的两条兜底分支（`core/code_generator.py:291`、`:286-289` 命中 `_STEP_TO_ACTION` 但值为裸名）必须显式化**，而非返回合法码。可选：返回带机读标记的 TODO 注释（如 `# TODO: unmapped step: …`）由门禁检测；或 raise；或返回 None 让调用方收集成阻断项。**不得**继续返回可过 `ast.parse` 的占位码。
4. **关键字表值必须补全括号**：`core/code_generator.py:90-93` 的 `使用 → "play_card"` 至少应改成合法的 `play_card(...)` 或彻底移除该项——但移除会改变现网生成结果，必须与判据 2 同批落地并跑全量比对。
5. **门禁检测必须与 allowlist 解耦**：判据不得写在 `spec_reviewer` 白名单里（那是同一处漂移的两面）。要在 `spec_gate` 的生成码校验路径上独立成立，使得**将来给白名单加任何一条，都必须同时证明生成侧真有映射**，否则该用例仍被阻断。这条是本清单存在的全部理由。
6. **套件侧同样适用**：`core/code_generator.py:462-468` 在 `generate_suite_test` 里走同一 `_step_to_action_call`，步骤垃圾同样落进 `.setup()`。判据 1-3 必须对套件生成路径同等生效。

### 报错形态要求

阻断 reason 必须**指向真因**，不得复用运行期的 `Game not actionable`（实测证据 3）。要求 reason 文案含"步骤未映射"语义与被拒步骤原文，使"BLOCKED"可被人类直接归因到规格或平台某一方。

## 涉及范围

`STS2-GAWAIN/automation/autotest/lib/spec_gate.py:66-72`（新增步骤侧检测；该文件属 Gawain 入口脚本，**改动需跨仓协调**）、`core/code_generator.py:90-93 / :286-291`（兜底显式化）、`core/code_generator.py:462-468`（套件路径）、`core/spec_reviewer.py:34-50`（与本判据对账，不重复造轮子）、`tests/unit/`（对应新增用例）。

外部依赖：判据 1-3、5-6 可纯离线单测证明；判据 4 需一次全量生成比对（46 个 READY 用例的产物 diff）。**注意**：`spec_gate.py` 的改动落在 Gawain 仓，与本仓分属两仓，需在涉及范围里显式协调（AGENTS.md「与其他仓库关系」）。

## 能解锁哪几个用例

**0 个。** 本条不新增任何 BLOCKED→READY 解锁（实测 READY 侧垃圾产物为 0，见证据 4）。它的价值在于让其余需求条目——尤其 LOC-008——的验收判据变得可自证，以及让 `blocked_case_count / problems` 这些估工数字重新可信。

## 优先级

**P0，本清单第一条开工项。** 论证：

1. **它决定其他条目的验收是否可信**。LOC-008 的判据全部是"用例从 BLOCKED 转 READY"，而 READY 由当前这个 fail-open 门禁给出。先修 LOC-008 会在一个不可信的度量上做验收。
2. **单位成本最低**。判据 1、2、5 是门禁侧纯 Python 改动，无游戏依赖，可完全用 `tests/unit/` 证明（AGENTS.md 测试优先级 1-3 均可离线跑通）。
3. **爆炸半径已实测为零**（证据 4），不会把现有 46 个 READY 用例反向打成 BLOCKED，因此可以安全先落。
4. **风险敞口是一次编辑**。上游 REQ-06 的善意 allowlist 补录、或 Gawain 侧把 `使用控供能…` 补一个空格，都会立刻把它变成现行假 READY（证据 2 已实测该路径）。

排序：**LOC-009 → LOC-008 →（LOC-010 评估）**。

## 验收标准

1. 复现用例 `使用 控供能中继器装备增益到支持系仆从`（即证据 2 的最小规格）在改造后被门禁判 **BLOCKED**，reason 文案含被拒步骤原文与"未映射"语义，而非 `ast.parse` 通过后的沉默。
2. 兜底分支不再产出合法占位码：对 `core/code_generator.py` 的两条兜底路径各写单测，断言其产出可被 `spec_gate` 检为阻断（或 raise），**且 `ast.parse` 不是判据**。
3. 判据以 AST 实现：单测须覆盖"`play_card,` 出现在 import 块"这一合法形态**不得**被误判（防证据 4 的计量陷阱进产品代码）。
4. 套件路径同等生效：把判据 1 的垃圾步骤写进一个套件包含的用例，`generate_suite_test` 产物同样被阻断（`core/code_generator.py:462-468`）。
5. 与 allowlist 解耦的证明：临时把某条步骤原文加进 `spec_reviewer` 白名单而不加生成侧映射 ⇒ 门禁**仍**判 BLOCKED。这是判据 5 的直接测试，建议作为固定回归。
6. 零回归证明：现有 46 个 READY 用例改造后仍为 READY（`blocked_case_count` 保持 14、`case_count` 保持 60），用 AST 统计脚本给出前后 `action_type` 取值集合与裸名实参数对照。
7. 运行期报错形态改善（若本条一并处理运行侧）：未知 `action_type` 不得再报 `Game not actionable before action N`；应给出指向"动作未注册/步骤未映射"的机读 reason。若此项留待后续，须在 PR 描述中显式登记未做。
8. 默认验证三件套通过：`python -m pytest tests/unit/ -q`、`mypy src/sts2_autotest --strict`、`lint-imports`。

## 不在范围

- 不新增任何 DSL 原语、不补任何白名单条目（补白名单是上游 REQ-06，且必须在 LOC-009 之后做）。
- 不修 `_STEP_TO_ACTION` 之外的步骤语义缺失（WAIT/HOVER/PICK/USE 等属上游 REQ-08）。
- 不碰 Gawain 侧用例文案与卡 id。
- 不实现套件级 Then 生成（LOC-010 / 上游 REQ-07）。
- 本文件**未**写入 `docs/tasks/registry.json`、**未**重生成 `docs/tasks/BOARD.md`。

## 未核实（不外推）

- 合法 `action_type` 的完整集合：实测只覆盖 46 个 READY 用例出现的 6 个 ASCII 值 + 代码里读到的 `proceed / nav_to_screen / return_to_menu` 等。判据 1 若按白名单式枚举实现，集合完整性未核实。
- `core/orchestrator.py:1192-1199` 的 `Action '…' not available`（`ADAPTER_ERROR`）路径在本条实测中未被触发——桩适配器让 `_wait_until_action_available` 先超时了。两条路径的先后关系未核实。
- `dsl/fluent.py:167-175`：`all_actions` 为空时 `assert_that` 直接返回 `status="pass"`。经 `spec_gate.py:64`（缺 When 步骤即阻断）推断该分支不可从规格侧抵达，但未实测构造反证。

## 复现物位置（临时，不入库）

`C:/Users/22487/AppData/Local/Temp/reqb-repro/`

| 文件 | 作用 |
|:---|:---|
| `repro.py` | 兜底产物表、最小规格端到端 fail-open、107/34 分类复算 |
| `repro2.py` | READY 用例统计（含文本行法误报 38 的那版，保留作反例） |
| `repro3.py` | 步骤级精确定位 + 真实 orchestrator 失败模式 |
| `repro4.py` | 三类产物运行期行为与计时（`AttributeError` / `timeout_error` / 对照） |
| `specs-case-only/TC-REQB-FAILOPEN.md` | 最小可复现输入 |

未修改 `STS2-AUTOTEST` 生产代码与用例；未修改 `STS2-GAWAIN` 任何文件（含归因文档）；未建 issue、未提 PR、未 commit。
