# STS2-AUTOTEST 领域词汇（CONTEXT.md）

本文件是项目的领域词汇表：给架构讨论中的关键概念以唯一命名，使「接缝」（seam）有名字可指。
架构讨论使用的设计词汇——module（模块）、interface（接口）、depth（深度）、seam（接缝）、
adapter（适配器）、leverage（杠杆）、locality（局部性）——见全局 codebase-design 词汇表，不在此重复。
新增术语随决策即时落笔；已废弃的概念应删除而非留档。

## 任务与运行时

- **任务（Run）**：框架执行过的一次测试工作，由 `RunStore`（core/run_service.py）排队与持久化，记录为 `RunRecord`。提交方有两处：CLI（`autotest run`）与 MCP（`handle_submit_run`），即「提交面」。
- **旅程任务（Journey Run）**：以目标场景为终点的端到端游戏执行。类型词表：`new_run / resume_run / first_battle / card_test / goal_scene / act_traversal / finish_interstitials`。
- **任务运行时（run_executor）**：一次旅程任务从预检到终态归类的完整生命周期执行器（`core/run_executor.py`：`JourneyExecutor.execute()`、`run_journey_worker()`）。终态归类、证据封存、进度发布、取消收尾、干净主菜单恢复都藏在它的 interface 之后。
- **worker**：由 `RunStore`/`spawn_worker` 拉起的后台子进程任务。旅程类 worker 的进程入口是 `python -m sts2_autotest.core.run_executor`；套件类 worker 仍是 `python -m sts2_autotest.cli.main`。
- **阶段位（Phase）**：worker 执行期间写入 RunRecord 的进度标记：`PRECHECK → PREPARING → STARTING → RUNNING`。

## 执行语义

- **干净主菜单（Clean Main Menu）**：主菜单 + 无旧局 + 具备开新局能力，三条件同时成立。判定依赖 `has_run_save` 三态内省（True / False / None，None = 不可判定）与连续帧稳定检查。
- **受控重启（Controlled Restart）**：取消收尾或开工前的有界恢复动作：终止游戏 → 等进程消失 → 至多一次重新拉起 → 等待可操作。由 lifecycle 层执行；「至多一次」是硬约束。
- **终态归类（Terminal Classification）**：一次任务的结果收敛为五值之一：`PASSED / CANCELLED / FAILED_PLATFORM / BLOCKED_ENVIRONMENT / FAILED_PRODUCT`；进程退出码三档：0（通过或干净取消）、1（失败）、2（仅预检环境阻塞）。环境失败与产品失败的二分依据是错误信息中的环境信号词表。PASSED/FAILED 的 `RunRecord.status` 由 worker 收口（`complete_record`）写入，任务运行时不写；取消终态由任务运行时经 `finish_cancel` 先行写入且不可覆盖。
- **证据封存（Evidence Sealing）**：截图、日志、`journey-trace.json`、`evidence-manifest.json` 与 ZIP 产物核对打包，`run-result.json` 落盘到 `evidence_root/{run_id}/reports/` 并镜像到 `.runs/{run_id}/reports/` 双路径。
- **进度发布（Progress Publishing)**：旅程执行中由 journeys 层构造进度载荷（章节/楼层/屏幕/房间序列等），经回调写入 `RunRecord.progress`；对外经 `serialize_record` 拍平。

## 接缝

- **适配器（Adapter）**：`GameAdapterProtocol`（adapters/base.py）的接缝。两个生产实现：`CliModAdapter`（sts2 CLI 子进程）、`AgentAdapter`（HTTP/MCP）——两实现并存使此接缝为真。测试用手写 fake 直接实现协议。
- **证据钩子（Evidence Hooks）**：`build_evidence_hooks` 工厂产生的截图/日志/打包注入点；`STS2_AUTOTEST_EVIDENCE` 控制真实/降级实现。
- **生命周期（Lifecycle）**：core/lifecycle.py，游戏进程拉起、调试 API 注入、进程在场探测与终止。

## 已知债务标记（勿视为接口）

- `adapter._cache_stale` 直写、`lifecycle.api_timeout` 强写：行为敏感，改动需专门验证。（前者的公开方法 `mark_state_stale()` 已落地，run_executor 已改走公开方法；`lifecycle.api_timeout` 强写仍在。）
- 回主菜单恢复存在三份实现（run_executor / journeys.reset_to_main_menu / orchestrator._auto_reset_to_main_menu），语义不同（前者唯一带受控重启全流程）；统一是独立决策。
- 「动作后等到状态稳定」（settle）存在四种判定口径：adapter 内部轮询、orchestrator 的 intermediate settle、fluent 双快照、navigation fingerprint——归一依赖语义层进一步整合。
- 「事件/奖励推进到 MAP」的推进序列在 cli_mod / agent / navigation / journeys 各有适配传输的版本；合并需要先归一动作词表的参数形状。
- fluent 的规格 Given 文本启动校验依赖中文魔法子串正则（`_check_start_state` 等）；清除需 code_generator 产出结构化 StartStateRequirements 并全量重生成 tests/generated。
- `run-result.json`、`supported_target_scene` 上报列表、runner 三件套（bash）等多处知识存在副本或包外实现；收敛在后续架构轮次处理。
