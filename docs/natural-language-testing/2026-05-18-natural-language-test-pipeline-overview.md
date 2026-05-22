# 自然语言测试流水线总体方案

**日期：** 2026-05-18
**状态：** 方案收敛完成，首批闭环已打通到 `spec -> compile -> pytest collect`

## 文档入口

- [文档索引](./index.md)
- [实现计划](../superpowers/plans/2026-05-18-natural-language-test-pipeline-master-plan.md)

## 1. 背景与问题

当前仓库已经具备以下框架主链路能力：

- `pytest fixture`
- `TestOrchestrator`
- `ActionDescriptor`
- `Fluent DSL`
- `autotest` CLI

但在“首场战斗”这类端到端场景上，原先仍有两个明显断层：

1. `tests/e2e_first_battle.py` 是绕过框架的独立脚本，直接调 adapter、自行轮询状态。
2. 测试用例仍然偏“先写 Python，再跑测试”，缺少“自然语言规格 -> 审查 -> 修订 -> 编译 -> 运行”的闭环。

本方案的目标，是把自然语言测试规格纳入框架主链路，并保留分层命令与一键总调度两种使用方式。

## 2. 目标

### 2.1 用户侧目标

- 用户先写 `Markdown` 测试规格，而不是先写 Python 测试脚本。
- 规格采用两层模型：
  - 最小完整测试用例 `case`
  - 测试用例组合 `suite`
- 用户可以单独运行一个最小用例，也可以把多个最小用例串成完整大场景。

### 2.2 框架侧目标

- 对 `Markdown` 规格执行自动审查。
- 输出两份审查产物：
  - `review report`
  - `revised draft`
- 将通过审查的规格编译为内部模型：
  - `TestSpec`
  - `SuiteSpec`
- 再生成 `pytest + fixture + Fluent DSL` 测试文件。
- 由 `autotest review` / `autotest compile` / `autotest run` 分层执行。
- 由 `autotest run --all` 统一调度全流程。

## 3. 核心设计原则

### 3.1 规格分层

第一层是最小颗粒度的完整测试用例，必须满足：

- 可独立启动
- 不依赖其他用例先执行
- 自己具备完整 `Given / When / Then`
- 自己声明 `Start State` 与 `End State`

第二层是用例组合，负责：

- 编排执行顺序
- 声明执行模式
- 承载组合级目标与断言

### 3.2 命令分层

保留独立命令：

- `autotest review`
- `autotest compile`
- `autotest run`

再由：

- `autotest run --all`

负责总调度：

1. 发现规格
2. 执行审查
3. 执行编译
4. 调 pytest 运行
5. 汇总结果

### 3.3 主链路约束

生成出来的测试代码必须走这条链路：

`pytest fixture -> TestOrchestrator -> ActionDescriptor -> Fluent DSL -> adapter`

禁止重新生成直接 new adapter、自行轮询状态的旁路脚本。

## 4. 规格格式

### 4.1 Case 规格

目录：

- `specs/cases/*.md`

固定结构：

- `Metadata`
- `Start State`
- `End State`
- `Given`
- `When`
- `Then`

### 4.2 Suite 规格

目录：

- `specs/suites/*.md`

固定结构：

- `Metadata`
- `Goal`
- `Mode`
- `Includes`
- `Then`

## 5. 审查模型

审查器输出两份产物：

### 5.1 Review Report

问题分为四类：

- `ambiguity`
- `missing`
- `unimplementable`
- `capability_gap`

### 5.2 Revised Draft

`revised draft` 的含义是：

- 不直接生成代码
- 在尽量不改变原意的前提下
- 生成一版更具体、当前框架更可实现的候选 `Markdown`

## 6. 内部模型

### 6.1 TestSpec

用于承接最小用例，核心字段包括：

- `id`
- `title`
- `tags`
- `priority`
- `start_state`
- `end_state`
- `givens`
- `steps`
- `assertions`
- `fallback_policies`
- `capability_requirements`

### 6.2 SuiteSpec

用于承接组合规格，核心字段包括：

- `id`
- `title`
- `tags`
- `priority`
- `goal`
- `execution_mode`
- `includes`
- `suite_assertions`

## 7. 从自然语言到 Fluent DSL

规格编译分两步：

1. `Markdown -> TestSpec / SuiteSpec`
2. `TestSpec / SuiteSpec -> pytest + Fluent DSL`

首批已经接回的 DSL 原语包括：

- `return_to_menu()`
- `choose_game_mode("standard")`
- `start_new_run()`
- `select_character("IRONCLAD")`
- `embark()`
- `choose_event(0)`
- `advance_dialogue()`
- `choose_map_node(col, row)`
- `skip_card_reward()`
- `end_turn()`

这意味着生成器已经不再只能退回到：

- `ActionDescriptor(action_type="原句")`

## 8. CLI 方案

### 8.1 独立命令

- `autotest review`
  - 发现规格
  - 审查规格
  - 输出报告

- `autotest compile`
  - 读取规格
  - 生成 `tests/generated/*.py`

- `autotest run`
  - 执行既有 pytest 目标或历史运行模型

### 8.2 总调度命令

- `autotest run --all`

当前已经切到：

- 默认发现 `specs/cases` 与 `specs/suites`
- 自动先 `review`
- 再 `compile`
- 最后调 pytest 执行生成测试

## 9. 首批闭环范围

首批目标不是“做完所有能力”，而是用代表性场景打通闭环。

当前首批范围为：

- `TC-PREPARE-NEW-RUN`
- `TC-RESOLVE-NEOW`
- `TC-FINISH-FIRST-BATTLE`
- `SUITE-FIRST-BATTLE-SMOKE`

## 10. 当前进度

截至 2026-05-18，已经完成：

- `specs/cases` 与 `specs/suites` 默认目录与首批样板规格
- `MarkdownParser`
- `SpecReviewer`
- `CodeGenerator`
- `autotest review`
- `autotest compile`
- `autotest run --all` 的分层调度接线
- 首批 DSL 原语映射
- 生成测试的 pytest 收集验证

当前已经打通：

`specs -> parser -> reviewer -> code generator -> tests/generated -> pytest collect`

当前还未打通的关键段是：

- 更完整的战斗策略 DSL
- 真实游戏环境下的首战执行通过
- `review report` / `revised draft` 的持久化输出规范
- 旧旁路脚本的正式退场策略

## 11. 目录建议

- `docs/natural-language-testing/`
- `docs/superpowers/plans/`
- `specs/cases/`
- `specs/suites/`
- `tests/generated/`

## 12. 结论

这套方案已经确认的关键边界是：

- 规格分层：`case + suite`
- 命令分层：`review + compile + run`
- `run --all` 只做总调度
- `Markdown` 是权威输入
- 审查先于编译，编译先于运行
- 生成结果必须走框架主链路

后续实现只需要继续沿这条主线补足剩余执行能力，而不需要再回到旁路脚本模型。
