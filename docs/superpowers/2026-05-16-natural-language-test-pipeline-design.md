# 自然语言测试规格与执行流水线设计

**日期：** 2026-05-16（第二版，确认 MOD 项目边界）
**状态：** 已确认，待进入实现计划

## 背景

当前仓库已经具备以下框架基础能力：

- `TestOrchestrator` 负责测试会话生命周期与动作调度
- `ActionDescriptor` 负责动作契约
- `Fluent DSL` 用于以游戏语义组织测试步骤
- `pytest fixture` 负责向测试函数注入框架能力
- `autotest` CLI 已具备基础入口

但真实端到端场景仍存在两个断层：

1. `tests/e2e_first_battle.py` 仍是旁路脚本，直接操作 adapter，没有走 `pytest fixture -> TestOrchestrator -> ActionDescriptor -> Fluent DSL` 主链路。
2. 测试用例仍偏"先写代码再运行"，还没有形成"自然语言规格 -> 审查 -> 修订 -> 生成框架测试 -> CLI 统一运行"的闭环。

本设计文档用于沉淀一套分层方案，把自然语言测试用例纳入框架主链路，同时保留独立命令与一键执行入口。

## 框架与 MOD 项目的职责边界

本框架（STS2-AUTOTEST）不绑定具体 MOD 项目。框架提供 **能力**，MOD 项目提供 **内容**：

| 归属 | 内容 | 说明 |
|------|------|------|
| **MOD 项目** | 测试用例 Markdown 规格 | 放在 MOD 项目 `tests/` 下，遵循框架提供的模板 |
| **MOD 项目** | 生成的 pytest 测试代码 | 框架编译生成到 MOD 项目的 `tests/` 下 |
| **本框架** | `sts2-autotest.yaml` 配置 | 集中声明多个 MOD 项目的规格目录、输出目录等参数 |
| **本框架** | 审查器（reviewer） | 解析并审查 Markdown 规格 |
| **本框架** | 编译器（compiler） | Markdown → TestSpec/SuiteSpec |
| **本框架** | 代码生成器（generator） | TestSpec → pytest + Fluent DSL |
| **本框架** | CLI 调度 | review, compile, run, run --all |
| **本框架** | 标准模板与指引 | Markdown 模板、生成代码模板、集成说明 |

## 目标

- 用户（MOD 开发者）先编写 Markdown 自然语言测试规格，而不是先写 Python 测试脚本。
- 框架先审查规格的合理性、清晰度与可实现性。
- 框架输出两份审查产物：
  - `review report`：诊断报告
  - `revised draft`：更具体、当前可实现的候选 Markdown 用例稿
- 审查通过后，规格被编译为内部 `TestSpec` / `SuiteSpec` 模型。
- 在此基础上生成 `pytest + fixture + Fluent DSL` 测试文件到 MOD 项目目录。
- MOD 项目可通过两种方式接入框架：
  - **单项目模式**：`autotest run --spec-dir ../my-mod/tests/cases/`（CLI 参数直接指定）
  - **工作区模式**：框架 `sts2-autotest.yaml` 中配置多个 MOD 项目路径，按项目筛选执行
- `autotest run --all` 作为总调度入口，一键完成"发现 -> 审查 -> 编译 -> 运行 -> 汇总"。
- 同时保留独立命令，让审查、编译、运行可分别执行。
- 框架输出标准 Markdown 规格模板和 pytest 代码模板，协助 MOD 项目按规范输出。

## 非目标

- 当前阶段不追求支持完全自由散文式规格输入。
- 当前阶段不直接实现通用 AI 测试代理。
- 当前阶段不要求首批能力覆盖所有游戏场景，只要求基于首批代表性场景打通完整闭环。
- 框架不存储任何 MOD 项目的测试用例规格——规格归属 MOD 项目自身管理。

## MOD 项目接入方式

### 方式 1：CLI 参数直接指定（单项目模式）

MOD 开发者在自己的项目目录中执行：

```bash
autotest review --spec-dir ../my-mod/tests/cases/
autotest compile --spec-dir ../my-mod/tests/cases/ --output-dir ../my-mod/tests/
autotest run --spec-dir ../my-mod/tests/cases/
```

通过 `--spec-dir` 告诉框架去哪里找规格文件、输出产物放哪里。

### 方式 2：工作区配置（多项目模式）

框架的 `sts2-autotest.yaml` 中声明：

```yaml
# sts2-autotest.yaml
workspace:
  projects:
    - name: my-mod
      spec_dir: ../my-mod/tests/cases/
      output_dir: ../my-mod/tests/
    - name: another-mod
      spec_dir: ../another-mod/tests/cases/
      output_dir: ../another-mod/tests/
```

然后按项目名称筛选：

```bash
autotest run --project my-mod --all
autotest run --project my-mod --cases TC-PREPARE-NEW-RUN
```

不指定 `--project` 时，默认跑工作区中所有项目的全部规格。

两种方式互不排斥——工作区配置提供默认值，CLI 参数可覆盖。

## 总体分层

### 1. 规格分层

测试规格分为两层：

- **第一层：最小颗粒度的完整测试用例**
  - 必须可独立启动
  - 不依赖其他用例先执行
  - 必须具备完整的 `Given / When / Then`
  - 必须声明起始稳定状态与结束稳定状态
- **第二层：测试用例组合**
  - 由多个最小测试用例组合形成更大的完整场景
  - 组合层不定义新的原子动作
  - 组合层只负责顺序、目标、模式、汇总要求

### 2. 命令分层

CLI 也采用与规格类似的分层思想：

- **独立命令**
  - `autotest review ...`：只做规格审查
  - `autotest compile ...`：只做规格编译与测试代码生成
  - `autotest run ...`：执行已有测试产物
- **总调度命令**
  - `autotest run --all`：先调度审查，再调度编译，再调度运行，形成一键闭环

这样设计的原因是：

- 单独调试"规格问题"时，不必每次都跑完整测试
- 单独验证"生成器输出"时，不必启动游戏
- 自动化流水线仍可通过 `run --all` 一键完成全部任务

## 规格格式

### 最小测试用例（Case）

最小测试用例以 Markdown 为权威输入，推荐模板如下：

```md
# TC-PREPARE-NEW-RUN 进入新局地图

## Metadata
- id: TC-PREPARE-NEW-RUN
- level: case
- tags: smoke, bootstrap
- priority: P0

## Start State
- 任意可恢复状态
- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN

## End State
- 到达 Act 1 地图
- 当前可选择首个可达节点

## Given
- 已安装并可连接 STS2-Cli-Mod
- 游戏可被启动
- 如存在旧 run，框架应负责回收并回到可重新开局状态

## When
1. 如 Steam 未启动，则启动 Steam
2. 如游戏未启动，则启动游戏
3. 检测当前游戏状态
4. 若存在旧 run，则返回主菜单并重新开始
5. 选择标准模式
6. 选择 Ironclad
7. 开始新 run
8. 持续推进直到进入 Act 1 地图，且首个节点可选

## Then
- 不应出现 crash
- 最终应位于地图界面
- 应能识别至少一个可达节点
- 应产出日志与截图
```

约束如下：

- `Given` 只写前置条件与环境假设
- `When` 只写动作步骤，尽量一行一步
- `Then` 只写可验证结果
- 每个最小用例必须是自洽且可独立执行的

### 测试用例组合（Suite）

组合文件推荐模板如下：

```md
# SUITE-FIRST-BATTLE-SMOKE 首次战斗冒烟

## Metadata
- id: SUITE-FIRST-BATTLE-SMOKE
- level: suite
- tags: smoke, first_battle
- priority: P0

## Goal
- 验证从启动游戏到完成首次战斗的完整主链路可用

## Mode
- execution: sequential_shared_session

## Includes
1. TC-PREPARE-NEW-RUN
2. TC-RESOLVE-NEOW
3. TC-FINISH-FIRST-BATTLE

## Then
- 整条链路应可连续完成
- 任一子用例失败时应给出失败位置
- 应生成组合级执行摘要
```

组合层只定义：

- 目标
- 执行模式
- 引用哪些最小用例
- 组合级别断言

不重复定义原子动作细节。

## 审查模型

规格审查阶段输出两份产物：

### 1. Review Report

用于指出规格问题，问题分 4 类：

- **模糊项**
  - 人能理解，但程序无法稳定执行或验证
  - 例如：`适当选择`、`正常继续`、`尽快赢下战斗`
- **缺失项**
  - 缺少关键上下文，无法可靠执行
  - 例如：未声明起始状态、结束状态、角色、模式、处理策略
- **不可实现项**
  - 当前 adapter / DSL / 规则组合无法支持
- **待扩展能力**
  - 把"为什么当前不可实现"沉淀成能力缺口，便于后续框架演进

### 2. Revised Draft

`revised draft` 指的是：

- 在尽量不改变原意的前提下
- 根据当前框架已实现能力
- 自动生成一版更具体、更可执行的候选 Markdown 用例稿

它不是测试代码，而是"可执行规格候选稿"。

## 内部模型

Markdown 审查通过后，不直接生成 pytest 代码，而是先转成稳定的内部模型。

### TestSpec

最小测试用例编译为 `TestSpec`，包含：

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

### SuiteSpec

组合规格编译为 `SuiteSpec`，包含：

- `id`
- `title`
- `tags`
- `priority`
- `goal`
- `execution_mode`
- `includes`
- `suite_assertions`

## 从自然语言到 Fluent DSL

`TestSpec` 的核心价值是把自然语言步骤转成稳定的框架语义步骤，而不是直接拼 Python 字符串。

例如 `steps` 可逐步映射为标准步骤类型：

- `ensure_steam_running`
- `ensure_game_running`
- `reset_to_main_menu`
- `start_new_run`
- `select_mode`
- `select_character`
- `resolve_event`
- `choose_map_node`
- `combat_policy_loop`
- `skip_reward`

最终再由生成器把这些步骤映射到 `Fluent DSL` 的动作描述函数与断言函数。

## 测试代码生成目标

生成器必须产出走框架主链路的 pytest 测试，而不是再次生成旁路脚本。

目标链路为：

`pytest fixture -> TestOrchestrator -> ActionDescriptor -> Fluent DSL -> adapter`

示意：

```python
def test_tc_prepare_new_run(autotest, _session_loop):
    result = (
        define("TC-PREPARE-NEW-RUN", autotest, _session_loop)
        .setup(
            ensure_game_running(),
            reset_to_main_menu(),
            select_mode("standard"),
            select_character("IRONCLAD"),
            embark_new_run(),
        )
        .execute(
            advance_until_map(),
        )
        .assert_that(
            game_reached_state(GameScreen.MAP),
            has_travelable_node(),
            no_crash_detected(),
        )
    )
    assert result.passed, result.failures
```

要求：

- 测试文件只能依赖 DSL 暴露的标准动作与断言
- 不允许生成直接 new adapter、手写轮询状态机的旁路脚本
- 生成的测试文件输出到 MOD 项目的 `tests/` 目录（由 `--output-dir` 或工作区配置决定）

## CLI 总体流水线

### 独立命令

- `autotest review [--spec-dir DIR] [--project NAME]`
  - 发现 Markdown 规格
  - 执行审查
  - 输出 `review report` 与 `revised draft`
- `autotest compile [--spec-dir DIR] [--output-dir DIR] [--project NAME]`
  - 读取已通过审查的规格
  - 编译为 `TestSpec` / `SuiteSpec`
  - 生成 `pytest + Fluent DSL` 测试文件到输出目录
- `autotest run [--spec-dir DIR] [--project NAME] [--cases ...] [--suite ...]`
  - 运行现有测试产物
  - 可针对 case、suite、全部、失败重跑等目标执行

### `autotest run --all`

`autotest run --all` 作为统一调度入口，不直接承担全部业务逻辑，而是按顺序调度独立命令：

1. 根据 `--spec-dir` / `--project` 定位 MOD 项目
2. 自动发现规格
3. 调度 `review`
4. 调度 `compile`
5. 调度 `run`
6. 汇总报告

因此：

- 一键自动化能力保留
- 分层能力也保留
- 单步调试与完整流程都能支持

## 首批闭环场景

首批落地范围不是"支持所有能力"，而是基于代表性场景打通完整闭环。

当前确认的首批最小测试用例为：

- `TC-PREPARE-NEW-RUN`
  - 从任意可恢复状态进入新局地图
- `TC-RESOLVE-NEOW`
  - 处理开局祝福/初始事件，推进到首个节点可选
- `TC-FINISH-FIRST-BATTLE`
  - 进入首战并完成战斗，直到胜利/失败及后续善后

组合场景为：

- `SUITE-FIRST-BATTLE-SMOKE`
  - 顺序串联上述 3 个最小测试用例
  - 验证首次战斗冒烟主链路

## 现有旁路脚本的迁移原则

以 `tests/e2e_first_battle.py` 为例，后续应按以下原则迁移：

- 不再作为独立旁路脚本保留
- 它的业务知识要被拆回：
  - Markdown case / suite 规格（转移到对应的 MOD 项目）
  - `TestSpec` 步骤类型
  - DSL 动作与断言原语
  - 生成后的 pytest 测试文件

迁移完成后，真实执行入口应变为：

- `autotest review --spec-dir <MOD 项目 tests/cases/>`
- `autotest compile --spec-dir <MOD 项目 tests/cases/> --output-dir <MOD 项目 tests/>`
- `autotest run --all`

而不是手动运行独立 Python 脚本。

## 实施顺序

1. 定义 `TestSpec` / `SuiteSpec` 数据模型（内部 Python 数据类）
2. 实现 Markdown 解析器（将 .md 规格解析为结构化模型）
3. 实现规格审查器 + `revised draft` 生成器
4. 实现 `TestSpec -> pytest + Fluent DSL` 代码生成器
5. 实现 MOD 项目发现机制（`--spec-dir` + 工作区配置）
6. 改造 CLI：添加 `review`、`compile` 命令，调整 `run --all` 使其调度全流程
7. 输出标准模板：Markdown 规格模板、生成代码模板、集成指引说明
8. 用首次战斗场景（TC-PREPARE-NEW-RUN、TC-RESOLVE-NEOW、TC-FINISH-FIRST-BATTLE）打通端到端闭环
9. 退场或重构现有旁路脚本

## 相关文件

- **设计文档**：`docs/superpowers/2026-05-16-natural-language-test-pipeline-design.md`（本文）
- **对话中对比的方案**：`docs/case-registry-design.md`（文档2，对比参考）

此框架内不存放 MOD 项目的测试规格与生成代码——它们归属具体的 MOD 项目仓库。

## 结论

本方案确认以下核心原则：

- 框架提供能力，MOD 项目提供内容——职责边界清晰
- MOD 项目通过 `--spec-dir`（单项目）或工作区配置（多项目）接入
- 规格分两层：最小完整用例 + 用例组合
- 命令分两层：独立命令 + `run --all` 总调度
- Markdown 是权威输入
- 审查先于编译，编译先于运行
- `revised draft` 是候选 Markdown 规格稿，不是代码
- 生成结果必须走 `pytest fixture -> TestOrchestrator -> ActionDescriptor -> Fluent DSL` 主链路
- 首批目标是用首次战斗场景打通完整闭环，而不是一次性做完所有能力
