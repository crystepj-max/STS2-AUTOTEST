# P2-1 Gawain 专属内容迁移清单

日期：2026-07-20
执行者：Claude Code
依据：`docs/handoff/2026-07-20-p2-platform-generalization-handoff.md`（P2-1 第一步）

本文登记平台仓库（STS2-AUTOTEST）中全部 Gawain 相关内容的三类归属：

- **A. 必须迁出**：会在真实任务中改变角色选择、卡牌处理、调试操作、构建部署或通过标准的规则。
- **B. 中性样例**：只用于证明平台能处理任意字符串或任意 Mod 数据，不改变真实任务行为的测试样本。
- **C. 历史文档**：明确标注为案例或历史记录，不会被平台执行。

## 一、A 类：必须迁出（改变真实任务行为）

| # | 内容 | 原位置 | 行为影响 | 新归属 | 处理结果 |
|---|------|--------|----------|--------|----------|
| A1 | `set_seed` 动作硬编码调用 Gawain 专属调试指令 `gawain_emergency_recruit_seed {seed}` | `src/sts2_autotest/adapters/agent.py`（约 520-536 行） | 任何项目调用通用"设置种子"动作，实际执行的是 Gawain 卡牌专属命令 | 命令模板改为项目配置 `project_extension.seed_command_template`，Gawain 在其项目配置中提供；未配置时该动作如实返回失败 | 已完成 |
| A2 | `give_card` 动作把 `任意:卡牌` 写法一律翻译为 `GAWAINMOD-` 前缀 | `src/sts2_autotest/adapters/agent.py`（约 594-600 行） | 任何 Mod 用 `mod:card` 写法都会被错误加上 Gawain 前缀 | 前缀映射改为项目配置 `project_extension.card_id_prefixes`（默认为空=原样透传运行时 ID），Gawain 配置 `{"gawain": "GAWAINMOD-"}` | 已完成 |
| A3 | 平台适配器内置 Gawain 专属只读调试接口 `get_magic_web_layer`（`gawain_magic_web_status` 命令） | `src/sts2_autotest/adapters/agent.py`（约 985-1013 行） | 平台公共适配器携带单一 Mod 的专属调试指令 | 从平台移除；全仓（含 Gawain 仓库）无调用方，Gawain 后续如需可经通用 `run_console_command` 自行实现 | 已完成 |
| A4 | 角色别名表硬编码 `"Gawain": "GAWAINMOD-GAWAIN"` | `src/sts2_autotest/core/code_generator.py`（约 74-79 行） | NL 规格"选择 Gawain"由平台默认解析为 Gawain 运行时 ID | 平台只保留原游戏角色别名；Mod 角色别名经项目配置 `project_extension.character_aliases` 注入 | 已完成 |
| A5 | NL 步骤识别硬编码 Gawain 专属营火选项"选择魔网共鸣"→ `choose_rest_option(option_index=2)` | `src/sts2_autotest/core/code_generator.py`（约 184-185 行） | 平台 NL 流水线认识 Gawain 专属机制词汇 | 平台改为通用写法"选择营火选项 N"；Gawain 规格改用通用写法 | 已完成 |
| A6 | NL 审查器支持步骤正则硬编码 `Gawain` 角色名 | `src/sts2_autotest/core/spec_reviewer.py`（约 35 行） | 审查器只对 Gawain 别名开绿灯，其他 Mod 角色名会被误判不支持 | 改为通用角色名模式（任意标识符 + 原游戏角色中文名）；Mod 别名由项目配置注入后同样被接受 | 已完成 |
| A7 | Test Agent 工作流内置 Gawain 角色常量与选角逻辑（`_GAWAIN_CHARACTER_IDS`、`_GAWAIN_CHARACTER_NAMES`、`_select_gawain_character` 等） | `src/sts2_autotest/core/test_agent_runner.py`（约 51-52、1054-1267 行） | 冒烟阶段角色选择固定为 Gawain | 角色标识/别名改为测试计划 YAML 提供；未提供时按通用方式选角 | 已完成 |
| A8 | 本地化裸 Key 识别硬编码 `GAWAINMOD-` 前缀正则 | `src/sts2_autotest/core/test_agent_runner.py`（约 1087-1115 行） | 裸 Key 检查只认识 Gawain 前缀，对其他 Mod 失效 | 前缀列表改为测试计划 YAML 提供（`localization_key_prefixes`）；通用失败特征保留 | 已完成 |
| A9 | 冒烟断言硬编码 Gawain 初始遗物 `GAWAINMOD-MAGIC_TERMINAL` 与 5 张初始卡牌 | `src/sts2_autotest/core/test_agent_runner.py`（约 1137-1184 行） | 初始牌组/遗物通过标准固定为 Gawain 业务事实 | 初始遗物/卡牌期望改为测试计划 YAML 提供；未提供时跳过业务断言只做通用检查 | 已完成 |
| A10 | `gen-report --task-id` 默认证据目录硬编码 `STS2-GAWAIN/automation/autotest/output/<task_id>` | `src/sts2_autotest/cli/main.py`（约 3279-3286 行） | 报告命令默认指向 Gawain 项目目录 | 改为按 `--mod-project`（默认当前目录）解析 `<mod_project>/automation/autotest/output/<task_id>` | 已完成 |
| A11 | 平台仓库残留 9 个 Gawain 生成测试文件（其规格源已不在平台仓库，Gawain 仓库已有更新版本） | `tests/generated/test_tc_gawain_*.py`、`tests/generated/test_suite_gawain_smoke.py` | 平台测试目录携带 Gawain 业务用例，会被平台 pytest 收集 | 从平台仓库移除；权威版本保留在 `STS2-GAWAIN/automation/autotest/generated/` | 已完成 |

## 二、B 类：中性样例（保留，不改变真实任务行为）

| # | 内容 | 位置 | 保留理由 |
|---|------|------|----------|
| B1 | 单元测试用 GAWAINMOD 字符串作为"任意 Mod 数据"样例（选角解析、卡牌选择规则、动作执行、fluent API、MCP 协议、HTML 报告、Visual QA、修复建议栈解析） | `tests/unit/test_start_new_run_flow.py`、`test_card_selection_rules.py`、`test_action_execution.py`、`test_fluent_api.py`、`test_mcp_protocol.py`、`test_report_html.py`、`test_visual_qa.py`、`test_agent_runner_visual_qa.py`、`test_repair_advisor.py`、`test_smoke_card_validation.py` | 只验证平台对任意字符串/任意 Mod 形态数据的通用处理能力，样本换成任何 Mod 名称测试同样成立 |
| B2 | `minion_queue_ids_are` 断言与 fluent 轨迹中的"仆从队列"展示 | `src/sts2_autotest/dsl/assertions.py`、`dsl/fluent.py`、`dsl/__init__.py` | 断言对象由调用方传入、读取状态中的通用 `minion_queue` 字段，不含任何 Gawain 固定值；任何有仆从机制的 Mod 均可复用 |
| B3 | NL 断言模式"仆从队列…"与 `code_generator` 中对应生成规则 | `src/sts2_autotest/core/spec_reviewer.py`、`core/code_generator.py` | 通用中文机制词汇，不指向 Gawain 固定对象 |
| B4 | 适配器角色名模糊匹配（去 Mod 前缀后缀匹配）注释中以 GAWAINMOD 为例 | `src/sts2_autotest/adapters/agent.py`（约 870-928 行） | 逻辑本身对任意 Mod 前缀通用；仅注释举例中性化 |
| B5 | `core/run_service.py` 文档字符串声明"不读取 Gawain 业务字段" | `src/sts2_autotest/core/run_service.py` | 边界声明，正是平台通用性的文字证据 |
| B6 | `cli/main.py` 帮助文本举例 `gawain-localization-key-fix` | `src/sts2_autotest/cli/main.py`（约 188 行） | 仅帮助示例；一并中性化为通用示例 |
| B7 | 文档中以 Gawain 为示例的调用（`--project gawain` 等） | `docs/unified-run-contract.md`、`docs/user-manual.md` | 示例性质，不改变行为；随文档边界复核统一标注 |

## 三、C 类：历史文档（保留为案例/历史记录）

| # | 内容 | 位置 |
|---|------|------|
| C1 | 历史计划/设计/交接文档中的 Gawain 记录 | `docs/superpowers/plans/*`、`docs/superpowers/specs/*`、`docs/natural-language-testing/*`、`docs/handoff/*`、`docs/baselines/*` |
| C2 | 历史运行证据（run.json、worker.log、case-traces、suite-summaries、游戏日志） | `tests/output/` |
| C3 | `docs/platform-capability-inventory.md` 第四节"Gawain 项目应该负责的工作" | 边界声明本身即交付物，迁移完成后更新"当前仓库仍保留"一句 |

## 四、迁移接收位置（Gawain 项目侧）

| 接收物 | 位置 | 状态 |
|--------|------|------|
| 项目扩展配置（卡牌前缀映射、种子命令模板、角色别名） | `STS2-GAWAIN/automation/autotest/config/sts2-autotest.yaml` 增加 `project_extension` 段；`scripts/_env.sh` 同步导出三个 `STS2_PROJECT__*` 环境变量 | 已完成 |
| 冒烟期望（角色标识/别名、初始遗物、初始卡牌、本地化 Key 前缀） | `STS2-GAWAIN/automation/autotest/config/agent-test-plan.yaml`（新建，供 `autotest agent-test --test-plan` 使用） | 已完成 |
| "选择魔网共鸣"规格写法改为通用"选择营火选项 2" | `STS2-GAWAIN/automation/autotest/specs/` 中相关规格 | 已完成 |

## 五、不在本次处理范围

- `tests/output/` 历史证据不删除（交接明确禁止）；
- B 类样例不改写为其他 Mod 名称（避免用另一个项目专属规则替换 Gawain 专属规则）；
- 不重写已通过的目标场景执行流程。
