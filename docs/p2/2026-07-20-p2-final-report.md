# P2 通用测试平台收口 — 最终报告

日期：2026-07-20
执行者：Claude Code
依据：`docs/handoff/2026-07-20-p2-platform-generalization-handoff.md`

## 总体结论

- P2-1 专属规则迁移：**PASSED**
- P2-2 多 Agent 真实接入：**PASSED**（五类 Agent 全部完成真实接入验收；Hermes 于 2026-07-24 第四轮完成真正的「取消原任务 → 恢复原任务 → 恢复 PASSED」）
- 是否仍存在角色或 Mod 隐性默认值：**否**（迁移清单 11 项 A 类全部处理完毕；剩余 Gawain 字样仅为边界声明文档字符串与中性测试样例）
- 是否修改公共任务含义：**否**（六操作、状态名称、报告结构均未改变；`get_magic_web_layer` 为平台适配器上的项目专属只读接口，移除前全仓无调用方）
- 是否需要用户补充权限：**否**（本轮真实运行和公共生命周期回归均已完成）

> 2026-07-26 最终收口：P2-2 保持原五类 Agent `VERIFIED`，本轮仅做六操作无项目生命周期无回归；P2-1 补齐统一项目校验、移除项目专属证据脚本，并用通用工具一次生成新的 4/4 真实证据包。最终结论仍为双 PASSED。

## P2-1 结果

### 迁出内容（A 类，11 项，全部完成）

详见迁移清单 `docs/p2/2026-07-20-p2-1-gawain-migration-inventory.md`：

1. `set_seed` 硬编码 Gawain 调试指令 → 项目配置 `seed_command_template`（默认空=如实失败）
2. `give_card` 一律加 `GAWAINMOD-` 前缀 → 项目配置 `card_id_prefixes`（默认空=透传）
3. 平台适配器内置 `get_magic_web_layer`（Gawain 专属）→ 移除（全仓无调用方）
4. 角色别名表硬编码 Gawain → 项目配置 `character_aliases` 注入
5. NL 硬编码"选择魔网共鸣"→ 通用"选择营火选项 N"，Gawain 规格改写
6. NL 审查器硬编码 Gawain 角色名 → 通用角色名模式
7. Test Agent 冒烟固定 Gawain 选角 → 测试计划 YAML 提供角色标识
8. 裸 Key 检查硬编码 `GAWAINMOD-` 前缀 → 测试计划 YAML 提供前缀列表
9. 冒烟断言固定 Gawain 初始遗物/牌组 → 测试计划 YAML 提供期望值
10. `gen-report` 默认目录硬编码 `STS2-GAWAIN` → 按 `--mod-project` 解析
11. 平台仓库 9 个过期 Gawain 生成测试文件 → 移除（权威版本在 Gawain 仓库）

### 保留为中性样例的内容（B 类）

- 单元测试中的 GAWAINMOD 字符串样本（验证平台对任意 Mod 数据的通用处理能力）
- `minion_queue_ids_are` 断言与"仆从队列"展示（通用状态字段读取，无 Gawain 固定值）
- 适配器角色模糊匹配逻辑（对任意 Mod 前缀通用，注释举例已中性化）
- `run_service.py` 边界声明文档字符串
- 文档中的 Gawain 调用示例（示例性质，已统一标注）

### Gawain 项目承接内容

- `automation/autotest/config/sts2-autotest.yaml`：新增 `project_extension` 段（卡牌前缀映射、种子命令模板、角色别名）
- `automation/autotest/scripts/_env.sh`：同步导出三个 `STS2_PROJECT__*` 环境变量
- `automation/autotest/config/agent-test-plan.yaml`（新建）：冒烟期望（角色标识、初始遗物/牌组、本地化前缀）
- `automation/autotest/specs/cases/TC-GAWAIN-REST-MAGIC-RESONANCE.md`："选择魔网共鸣"→"选择营火选项 2"
- 重编译验证：92 个生成文件行为输出与迁移前一致

### 三角色短目标（同一公共入口、同一套平台规则，只更换 character_id）

| 角色 | run_id | 终态 | 防重 | 证据 |
|---|---|---|---|---|
| IRONCLAD | run-20260720-133607-e084461b | PASSED | 同键同号 ✓ | tests/output/cross-agent-p2/short-goals-20260720-p2a-ironclad-retry/ |
| SILENT | run-20260720-132719-0e4f3db3 | PASSED | 同键同号 ✓ | tests/output/cross-agent-p2/short-goals-20260720-p2a/ |
| GAWAINMOD-GAWAIN（Mod） | run-20260720-132850-c5668d7a | PASSED | 同键同号 ✓ | 同上 |

说明：IRONCLAD 首次正式运行（run-20260720-132609-6a60f2b7）如实报 FAILED_PLATFORM——随机事件抽到不可跳过网格选牌（平台通用处理的已知薄弱点，与迁移无关，证据完整保留）；补跑一次即 PASSED。平台未修改任何规则。

### 整章任务

| 轮次 | run_id | 终态 | 说明 |
|---|---|---|---|
| 第一次 | run-20260720-134047-4a5e1eea | FAILED_PLATFORM | 任务服务环境缺调试动作开关（STS2_ADAPTER__AGENT__DEBUG_ACTIONS），win_combat 不可用，基础战斗第 6 层战败；与迁移无关，证据完整 |
| 第二次（P0 同法：agent 适配器 + 调试动作） | run-20260720-142730-23590575 | **PASSED** | 258.6 秒完成第一章并进入第二章稳定地图；win_combat 快速结束 7 场战斗（trace 中 action=win_combat 条目数）；证据包 44 个文件 |

### 取消与恢复任务（P1 能力回归，三柱全绿）

驱动：`scripts/p1_v11_acceptance.py`；输出：`tests/output/cross-agent-p1/p2-regression-v12-20260720/`

| 柱 | run_id | 终态 |
|---|---|---|
| ORIG | run-20260720-143541-405f13a8 | CANCELLED |
| RESUME | run-20260720-143738-e7c1a9b1 | PASSED |
| SECOND | run-20260720-143821-ec7dbe35 | CANCELLED |

判定：V11_PASS=true，failed_checks=[]。

### Gawain 冒烟回归（迁移后 Gawain 回归不失效）

`autotest agent-test --mod-project ../STS2-GAWAIN --task-id gawain-smoke-v3 --test-plan ../STS2-GAWAIN/automation/autotest/config/agent-test-plan.yaml --skip-deploy --skip-launch-game`

结果：**PASSED**（10 项通过、2 项跳过：Deploy Mod / Launch Game 按参数跳过）。
报告：`STS2-GAWAIN/automation/autotest/output/gawain-smoke-v3/test-report.md`

干净环境复验（2026-07-24，三个 STS2_PROJECT__* 环境变量全部未设置，项目配置经 sts2-mod.yaml 指向的已提交配置文件读取）：**PASSED**（10 项通过、2 项跳过），报告：`STS2-GAWAIN/automation/autotest/output/gawain-smoke-cleanenv4/test-report.md`。

**顺带修复的平台既有缺陷**（均被本轮验收暴露，均有单元检查覆盖）：

1. runner 假通过：未分类异常仍写 PASSED 报告 → 一律如实判 FAILED（`test_run_reports_failed_on_unhandled_error`）
2. 事件循环缺陷：逐次 asyncio.run 使 HTTP 客户端绑定到已关闭循环，冒烟在健康检查后必崩 → 共享单循环执行
3. 手牌时序：战斗开场动画未结束即读手牌 → 有界等待手牌真实出现
4. 不可跳过网格选牌（Review 复核 #4）：跳过被游戏拒绝后改选第一张可用牌推进（`test_progress_until_falls_back_to_grid_select_card_when_skip_rejected` 等 2 条新检查）
5. 冒烟导航不认识卡牌奖励页（干净环境复验暴露）：通用离开路径（跳过/继续/领取）

**Review 复核后的结构性修复**：

- Gawain 承接配置可交付化：`.gitignore` 解除排除并提交（Gawain 仓库 `9390081`，仅含本轮 5 个承接文件，他人未提交内容未触碰）
- 平台统一读取项目配置：新增 `adapters/project_extension.py`——项目配置文件（常规名或 sts2-mod.yaml 的 autotest.config 指针）为基、`STS2_PROJECT__*` 环境变量为覆盖；pytest fixtures、CLI 适配器装配、NL 代码生成共用，不再依赖本机临时启动设置（5 条新单元检查；干净环境实测：三个环境变量全部未设置时前缀/模板/别名/适配器装配全部正确）

### 失败或阻塞（全部如实记录，证据完整）

- run-20260720-132609-6a60f2b7（IRONCLAD 首跑）：FAILED_PLATFORM，不可跳过网格选牌，既有薄弱点
- run-20260720-134047-4a5e1eea（整章首跑）：FAILED_PLATFORM，任务服务缺调试动作开关
- run-20260720-150011-900c4d7e（Claude Code 取消轮第一跑）：FAILED_PLATFORM，卡牌奖励索引越界，既有薄弱点

### 证据

- 三角色：`tests/output/cross-agent-p2/short-goals-20260720-p2a*/`（raw 公共响应 + verdict.json）
- 整章：`tests/output/artifacts/run-20260720-142730-23590575_passed.zip`
- 取消恢复：`tests/output/cross-agent-p1/p2-regression-v12-20260720/raw/`（17 个原始响应）
- Gawain 冒烟：`STS2-GAWAIN/automation/autotest/output/gawain-smoke-v3/`

## P2-2 结果

接入说明：`docs/agent-quickstart.md`（客户端无关，六操作双通道）

| Agent | 正常任务 | 防重 | 取消/断线恢复 | 最终报告 | 证据 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| ChatGPT/Codex | run-20260720-151308-e24fbe0b PASSED | 同键同号同时 ✓ | run-20260720-152046 CANCELLED + run-20260720-152236 PASSED ✓ | 可读 ✓ | tests/output/cross-agent-p2/agent-codex-20260720/ | VERIFIED（第一轮原任务自然完成未取消；第二轮在局内快取消成功） |
| WorkBuddy | run-20260720-065603-29c9b852 PASSED | 同键同号同时 ✓ | ORIG CANCELLED + RESUME PASSED ✓ | 可读 ✓ | tests/output/cross-agent-p1/workbuddy-v11-replication-20260720-145438/ | VERIFIED（P1 基线与 V11j 同轮验收） |
| Claude Code | run-20260720-145745-ee612623 PASSED | 同键同号 ✓ | run-20260720-150530 CANCELLED + run-20260720-150820 PASSED ✓ | 可读 ✓ | tests/output/cross-agent-p2/agent-claude-code-20260720/ | VERIFIED（CLI JSON 通道，MCP 等价的 autotest 命令） |
| OpenClaw | run-20260720-153200-06d5e031 PASSED | 同键同号同时 ✓ | R2: run-20260720-153848 CANCELLED + run-20260720-154101 PASSED ✓ | 可读 ✓ | tests/output/cross-agent-p2/agent-openclaw-20260720/ | VERIFIED（第一轮原任务自然完成未取消；第二轮快取消成功 + 恢复 PASSED） |
| Hermes | run-20260724-021557-7bca75e8 CANCELLED | 同键同号同时 ✓ | **真恢复链**：原任务局内取消 CANCELLED → resume 原任务 run-20260724-021744-e2bed936 **PASSED**（resumed_from 正确指向原任务）✓ | 双报告可读 ✓ | tests/output/cross-agent-p2/agent-hermes-20260720/（r4- 前缀独立保存） | **VERIFIED**（第四轮按 Claude Code 同法的公开操作轮询循环抢先取消成功；前三轮因旅程 15-60 秒即完成、逐步查询间隔过长未抢先，均如实记录） |

## 自动检查与真实验收

- 小范围检查：受影响单元测试（适配器/代码生成/审查器/冒烟/配置）全部通过
- 完整检查：单元全量 **1752 passed**；集成 **25 passed, 10 skipped**（当前复核环境；P1 基线环境为 30/5，差异均为真实游戏/外部服务条件自动跳过）；compileall OK；lint-imports KEPT；mypy 错误与基线一致（5 项均为 macOS 平台 stub 既有项）
- 跳过项及原因：本环境集成测试 10 项因现场游戏或外部控制条件自动跳过；P1 基线为 5 项跳过，差异已单独登记
- 真实任务编号：见上表
- 证据包：`tests/output/artifacts/` 中各 run 的 zip 均可读

## Review 复核响应（2026-07-24）

复核结论（双 PASSED 被拒，标 PARTIAL）指出 5 项问题，逐项处理如下：

| # | 复核问题 | 处理 | 证据 |
|---|---|---|---|
| 1 | Gawain 承接配置被 gitignore 排除，不可交付 | 解除排除并提交（仅本轮 5 个承接文件，他人未提交内容未触碰） | Gawain 仓库 `9390081`；`git check-ignore` 复核三项均可提交 |
| 2 | Hermes 未完成真正的取消→恢复→PASSED | 第四轮按 Claude Code 同法（公开操作轮询循环抢先取消）完成真恢复链 | `agent-hermes-20260720/raw/r4-*`：原任务 run-20260724-021557-7bca75e8 CANCELLED → 恢复 run-20260724-021744-e2bed936 PASSED，resumed_from 正确；前三轮未抢先的原始记录（r2/r3 前缀）独立保留未覆盖 |
| 3 | 报告数字错误 | 逐项按原始证据修正：整章耗时 258.586s（duration_ms 原值）、win_combat 7 场（trace 中 action=win_combat 条目数，非字符串出现次数 185）、Gawain 冒烟 10 通过 2 跳过、集成检查按环境如实标注 | 本文相关段落 |
| 4 | 不可跳过网格选牌重复出现 | 平台修复：跳过被拒后改选第一张可用牌推进（TDD：先复现失败再修复） | `navigation.py`；`test_progress_until_falls_back_to_grid_select_card_when_skip_rejected`、`test_progress_until_raises_when_skip_rejected_and_no_card_selectable` |
| 5 | 成果未形成稳定版本 | AUTOTEST 与 Gawain 两侧均提交（见"工作区"节） | 见下 |

另：复核环境集成检查为 25 通过 10 跳过、本环境为 30 通过 5 跳过——差异来自现场检查项在服务/游戏不可达时自动跳过，与 P1 基线（30/5）在本环境一致；复核中 1 项单元检查因系统截图权限失败，属环境权限项，非 P2 功能回归。

## 第二轮 Review 复核响应（2026-07-24 下午）

第二轮复核（仍 CHANGES REQUIRED）指出 3 项阻塞 + 1 项格式问题，逐项处理：

| # | 复核问题 | 处理 | 证据 |
|---|---|---|---|
| 1 | 9 个过期 Gawain 文件未进入提交（早前 stash 操作取消暂存所致） | 补提交删除 | `db8433b`；`git ls-tree -r HEAD tests/generated/` 中 Gawain 文件数为 0 |
| 2 | 项目配置由服务启动位置决定，未按任务生效 | 新增按任务解析：任务携带 `project` → workspace 配置的 manifest 指针 → 项目根目录 → 该项目 project_extension；平台根目录新增 `sts2-autotest.yaml` 声明 gawain 项目；`Workspace.from_yaml` 补齐 manifest 字段填充；`--card-id` 经 CLI `--detach` 提交时未传入工作进程的既有缺陷一并修复 | 单元检查 4 条（AUTOTEST 目录启动 + env 为空 + gawain 任务正确读取 / 无 project 保持中性 / 未知项目回退中性 / env 覆盖 YAML）；真实验收：从公共服务目录提交 card_test——`project=gawain` 时 PASSED（trace 含 GAWAINMOD-STRIKE_GAWAIN，run-20260724-070830-e7062846）；不传 project 的对照任务如实失败（`Card 'GAWAIN:STRIKE_GAWAIN' not found`，run-20260724-071221-1963dbcd），证明无 project 即无 Gawain 规则 |
| 3 | 权威文档保留旧结论 | Hermes 矩阵行更新为 r4 真恢复链；数字统一为 1711；提交号 990cfa5→b8b0173 并补 db8433b | `docs/cross-agent-acceptance.md`、本文 |
| 4 | 提交格式检查未过 | handoff 文档行尾硬换行改独立段落、去末尾空行 | `git diff --check` 通过 |

干净环境复核（第二轮）：

- 按任务配置：公共服务目录 + 项目扩展环境变量为空 + card_test 项目任务 → 按 project 正确读取（上表 #2）
- 项目隔离：不传 project 时平台保持中性，对照任务如实失败（同上）
- Gawain 冒烟（干净环境）：PASSED 不变（第一轮复核已验，本轮代码改动不影响该路径）

## 第三轮 Review 复核响应（2026-07-24 晚）

第三轮复核指出"按任务隔离项目配置"仍未完整实现（2 项阻塞 + 1 项文档），逐项处理：

| # | 复核问题 | 处理 | 证据 |
|---|---|---|---|
| 1 | 项目解析只支持平台预登记的 Gawain；平台仓库重新出现 Gawain 固定登记 | `project` 同时支持两种通用输入：直接项目目录（含 sts2-mod.yaml 或项目配置文件）与已登记名称（本地 workspace 配置）；平台仓库中的 Gawain 预登记已删除（`git rm sts2-autotest.yaml`），该文件名加入 .gitignore 作为本地设置 | 单元检查 `test_create_adapter_with_project_directory`（目录直接接入，无登记）、`test_create_adapter_with_registered_name`（本地登记名）；真实验收：以 `project=../STS2-GAWAIN` 目录直接接入的 card_test **PASSED**（run-20260724-082728-f89cda40，trace 含 GAWAINMOD-STRIKE_GAWAIN），全程无平台登记 |
| 2 | 项目配置未覆盖角色别名与连接恢复 | 角色别名随任务 project 读取（编译路径贯通）；恢复重建入口（`_dispatch_orchestrator` 及 5 处调用点）全部携带 project，连接恢复后项目规则不丢失 | 单元检查 `test_character_aliases_follow_project`、`test_compile_cmd_passes_project_aliases`（公共服务目录编译）、`test_recovery_factory_keeps_project_config`（强制重建后配置保留） |
| 3 | 权威文档三套数字并存 | 统一为当时最终值，登记 `8ef63e2`；删除"以 git log 为准"写法 | `docs/cross-agent-acceptance.md`、本文 |

第三轮全量回归：单元 1715 passed（含该轮按任务解析与隔离的新增检查；第四轮后统一为 1723）；集成 30 passed, 5 skipped；compileall / lint-imports / git diff --check 全部通过。按复核说明，未重跑完整第一章；真实项目目录 card_test 已覆盖关闭条件。

## 第四轮 Review 复核响应（2026-07-24 晚）

第四轮复核指出 4 项 P1 阻塞 + 1 项文档问题，全部处理：

| # | 复核问题 | 处理 | 证据 |
|---|---|---|---|
| 1 | 目录型项目只读配置，规格来源与输出仍回退平台目录（假通过风险） | 目录型项目同时决定项目配置、规格来源与默认输出：优先项目自己的 workspace 配置（`workspace.projects[0]` 的 spec_dir/output_dir），其次 mod manifest 的 `autotest.spec_dirs[0]`/`evidence_dir`；不回退平台默认目录 | 单元检查 `test_project_directory_determines_spec_and_output_dirs` |
| 2 | 编译后执行阶段丢失项目规则 | 项目上下文跨进程传递：`STS2_PROJECT_DIR` 标准环境变量（`resolve_base_dir` 统一优先级）；pytest fixtures、CLI 适配器装配、`run_tests_in_dir`（新增 project_dir 参数）、`run --all` 管线全部贯通 | 单元检查 `test_run_tests_in_dir_injects_project_dir_env`；**端到端真实验收**：从平台目录执行 Gawain 自包含套件（含 give_card/set_seed）PASSED（42.5s，见下） |
| 3 | CLI 恢复 card_test 丢失目标卡牌 | CLI 提交持久化 `card_id` 到任务 metadata（与 MCP 路径一致），恢复时还原到工作进程参数；链式恢复不丢失 | 单元检查 `test_cli_submit_persists_card_id_and_resume_restores_it` |
| 4 | 目录型 project 绕过路径白名单 | 提交阶段对目录型 project 执行与 spec_dir 同一允许范围校验；项目声明指向的配置文件同样必须在白名单内 | 单元检查 `test_submit_rejects_directory_project_outside_allowed_roots`、`test_submit_rejects_project_config_outside_allowed_roots` |
| 5 | 报告保留动态提交号 | 直接登记 `3510c91`；本段为最终定稿 | 本文工作区段 |

**端到端真实验收（复核 #2 要求的完整链路）**：

1. `first_battle` 旅程以 `project=../STS2-GAWAIN` 目录接入到达首战（run-20260724-095826-608d0870，PASSED）；
2. 从平台仓库目录执行 Gawain 自包含套件 `test_suite_gawain_m2_multi_trigger.py`（`STS2_PROJECT_DIR` 传入，无其他项目环境变量）→ **PASSED**（42.5s）：`give_card("gawain:emergency_recruit")` 经项目前缀映射为 `GAWAINMOD-EMERGENCY_RECRUIT`、`set_seed` 经项目命令模板执行、三张牌真实打出、多仆从触发断言通过。

**最终标准证据包（2026-07-26 通用工具一次生成）**：`p2-final-e2e-20260726-0405`，由 `scripts/pipeline_evidence.py` 从平台目录接收外部项目目录、套件、任务编号和时限后执行。三个项目覆盖环境变量均已清空。结果为 **4/4 passed、19.516 秒、34 个文件**；压缩包 `tests/output/artifacts/p2-final-e2e-20260726-0405_passed.zip` 已重新解压并通过无损检查。四个逻辑用例均有独立记录，其中零原子步骤的导航用例也明确记录 `0 steps / passed`；卡牌记录直接显示 `gawain:emergency_recruit → GAWAINMOD-EMERGENCY_RECRUIT`，种子设置和三次真实出牌均可读。证据清单 `reports/pipeline-evidence.json` 在压缩前生成并包含于包内。

## 第五轮 Review 复核响应（2026-07-25）

第五轮复核指出 4 项代码问题 + 1 项证据问题，全部处理：

| # | 复核问题 | 处理 | 证据 |
|---|---|---|---|
| 1 | 显式项目缺少规格/输出声明时仍回退平台目录（假通过风险） | 显式项目缺少声明或声明目录不存在时结构化失败 `PROJECT_CONFIG_INVALID`，绝不回退平台默认目录 | `test_explicit_project_without_declarations_fails_structurally`、`test_explicit_project_with_nonexistent_spec_dir_fails` |
| 2 | 项目声明内部的规格/输出路径可越过允许范围 | 提交阶段解析项目声明，对声明的 spec_dir/output_dir 执行同一白名单校验 | `test_submit_rejects_declared_spec_outside_allowed_roots`、`test_submit_rejects_declared_output_outside_allowed_roots` |
| 3 | Agent 可见的完整流程无法传入 project | `run_test`/`run_pipeline` 公开参数补登 `project`；run_pipeline 将 project 贯穿编译（角色别名）与执行（项目上下文），目录型 project 同白名单校验 | `test_run_test_and_pipeline_schemas_expose_project`、`test_run_pipeline_passes_project_to_compile_and_run` |
| 4 | `_find_game_bundle` 忽略构造时传入的安装目录 | 优先使用 `self.game_dir`，其次环境变量与默认路径 | `test_find_game_bundle_prefers_constructor_game_dir`、`test_find_game_bundle_none_when_nothing_exists` |
| 5 | 42.5s 端到端未形成标准证据包 | 经公共执行入口重跑并打包，run_id 与压缩包已登记（见上"标准证据包"段） | `tests/output/artifacts/e2e-m2-multi-trigger-20260724-01_passed.zip`（内容已逐项核验） |

**端到端顺带修复的两个平台既有缺陷**（均有单元检查）：

- 状态机不承认 EVENT→BUNDLE_SELECTION / EVENT→TRI_SELECT（涅奥捆绑选择与三选一是 EVENT 的合法后继，Hermes r3 轨迹与本次端到端均真实遇到）→ 已补充合法转移；
- 编排器硬重启经 `open steam://run` 启动（env 经已运行 Steam 转发无法到达游戏），重启后调试控制台丢失 → SteamController 改为 macOS 直接启动游戏包并注入调试环境（`test_start_game_injects_debug_env_on_macos`）。
复核要求的"干净环境重新验证"（2026-07-24 完成）：

- 项目配置：三个 `STS2_PROJECT__*` 环境变量全部未设置时，`load_card_id_prefixes/load_seed_command_template/load_character_aliases` 与 pytest fixtures 装配的适配器均正确解析出 Gawain 前缀/模板/别名（经 `sts2-mod.yaml` 指向的已提交配置文件）
- Gawain 冒烟（干净环境）：PASSED（10 通过 2 跳过），`gawain-smoke-cleanenv4/test-report.md`；其间暴露的"冒烟导航不认识卡牌奖励页"缺口已修复并补单元检查
- 三角色短目标：仍为 2026-07-20 的 PASSED 证据（平台规则未再变动）
- Hermes 恢复：r4 真恢复链（上表 #2）

## 第六轮最终收口（2026-07-26）

本轮不重复五类 Agent 验收，也不重跑完整第一章，只关闭 P2-1 的项目隔离与证据复现缺口，并对 P2-2 做公共契约无回归。

| 收口项 | 最终处理 | 验收证据 |
|---|---|---|
| 所有公开入口的项目校验不一致 | 任务提交、规格编译、测试执行、完整流水线共用同一解析与允许范围；未知名称、目录越界、项目配置/规格/输出越界均返回 `PROJECT_CONFIG_INVALID`，禁止回退中性配置 | 项目解析与四入口一致性检查 |
| 平台保留项目专属证据脚本 | 删除 `scripts/p2_e2e_pipeline_evidence.py`；保留完全参数化的 `scripts/pipeline_evidence.py`；其他 P2 驱动移除 Mod 角色默认值 | 通用工具源码无 Gawain 路径、套件、角色或任务编号默认值 |
| 证据可事后拼装或混入旧文件 | 唯一任务编号、独立逐案目录、每案必有记录、汇总与记录新鲜度检查、压缩前清单、压缩后成员/任务编号/结果/用例列表复核；任一矛盾即失败并删除未通过压缩包 | `p2-final-e2e-20260726-0405`，4/4、34 文件、19.516 秒、ZIP 无损 |
| 零操作用例缺记录 | 即使没有可追踪的原子操作，也生成“零步骤、最终结果”记录 | `TC-GAWAIN-NAVIGATE-TO-COMBAT/case.log` |
| 动作证据看不到项目映射结果 | 逐步记录同时展示调用方标识和游戏实际标识 | `gawain:emergency_recruit → GAWAINMOD-EMERGENCY_RECRUIT` |
| P2-2 公共生命周期无回归 | 不带 project 调用六操作，验证防重、两次取消、原任务恢复、三份报告和证据封存 | `tests/output/cross-agent-p2/p2-final-six-ops-pass-20260726/`；ORIG `run-20260726-041523-0a5ff435` CANCELLED → RESUME `run-20260726-041620-4a8517ec` PASSED；SECOND `run-20260726-041652-0911dc2c` CANCELLED；`V11_PASS=true` |

真实复核中发现并修复两个公共生命周期缺口：开发态主菜单的“放弃旧局”曾错误走局内结束动作；验收脚本曾强制要求当前游戏版本未发布的存档布尔字段。最终判定同时要求主菜单可开始新局，且不存在继续/放弃旧局入口，不降低干净起点标准。

P2-2 的五类 Agent 原始验收均保持有效，本轮仅确认六操作名称、参数含义、状态与报告结构未变化。

## 工作区

- 修改范围：STS2-AUTOTEST（统一项目校验、通用证据工具、逐案记录、公共生命周期修复、检查与文档）；STS2-GAWAIN 本轮不再修改
- 原有未提交内容如何保留：两个仓库均只做增量修改；Gawain 仓库中他人的未提交修改（AGENTS.md、MainFile.cs 等）未触碰
- 是否提交：STS2-GAWAIN `9390081`；STS2-AUTOTEST 既有收口提交 `b8b0173`、`db8433b`、`8ef63e2`、`3510c91`、`7a686b9`、`a89a233`；2026-07-26 最终收口由本报告所在提交承载
- 是否推送：未推送（留待用户决定）

本轮最终收口由本报告所在提交统一承载；提交保留在本地，未推送。
