# 交接文档：修复「测试时游戏被频繁重启」（启动即二次拉起）

- **交接日期**：2026-08-19（本地 20:50，UTC+8）
- **交接人**：WorkBuddy 会话（grill-with-docs 追问 + 根因排查）
- **接手人**：后续任意 agent
- **会话背景**：对腾讯文档 `[PERF] CliMod 适配器每次调用重启子进程 (P1)`（file_id `PNOPEsocVTPY`，https://docs.qq.com/markdown/DUE5PUEVzb2NWVFBZ?_fid=PNOPEsocVTPY ）做事实核验时，用户指出真正关心的问题不是「CLI 子进程冷启动」，而是**游戏本体进程在自动化测试时被频繁重启**（一次启动后不到 1 分钟又被拉起）。

---

## 1. 任务目标（用户原话要点）

> 开发工作流跑到集成测试/回归测试环节，autotest MCP 服务启动游戏后，不到 1 分钟又启动一次。第一次启动时屏幕切到游戏画面，不到 1 分钟又因启动游戏切回游戏画面。
> 理想期望：自动化测试时**不需要重启游戏**；即便做不到，也不希望频繁重启。
> 用户极少遇到崩溃/卡死，怀疑是「游戏还未启动成功，就被误诊为崩溃/未就绪」导致。

**成功标准（可验收）**：集成/回归测试启动游戏后，不再出现「启动后 ≤1 分钟被再次拉起」；需要重开一局时优先走游戏内「系统设置→放弃游戏→确认」回主菜单，硬重启仅作兜底。

---

## 2. 已确认根因（带代码证据）

### 根因 A（主因）——启动误诊为陈旧进程 → 杀 A 拉 B

触发链：
1. `test_agent_runner._step_launch_game`（`src/sts2_autotest/core/test_agent_runner.py:1046`）拉起游戏实例 A；
2. 紧接着预检 `ensure_environment_ready`（`src/sts2_autotest/cli/main.py:2125`，集成/回归前的环境就绪门）**不等待启动完成**就被调用；
3. `ensure_environment_ready`（`src/sts2_autotest/core/lifecycle.py:590-709`）中 `_probe_ready` 对「进程在、但屏幕仍是 UNKNOWN / 主菜单动作列表为空（模组未加载完）」判定为 `GAME_PROCESS_STALE` → **立即 terminate + 重新 launch**。

→ 这就是「启动后不到 1 分钟又被拉起」的可避免根因。

对照：`cli/main.py:813` 的取消恢复路径是正确的——terminate 后 launch，再 `wait_for_controllable`（`lifecycle.py:445`，`api_timeout=150s`）等待启动完成。`ensure_environment_ready` 没复用这个等待。

### 根因 B（次因）——Mid-run 重置直接硬重启，不用游戏内放弃

- `orchestrator._auto_reset_to_main_menu`（`src/sts2_autotest/core/orchestrator.py:242`）在需要把游戏重置回主菜单时，遇到 `MAP`/`COMBAT`（一局进行中）**直接 break 跳去硬重启**（杀进程+重拉），注释假设「进行中的局无法软导航到主菜单」——**该假设错误**。
- 框架其实**已具备**游戏内「放弃」能力：`abandon_run` 动作（`adapters/cli_mod.py:781` 直接 `_run_cli("abandon_run")`；`adapters/agent.py:509/791` 走 HTTP 且 `abandon_run` 会被映射为战斗内的 die 控制台命令，「只适合运行中」；`core/journeys.py:353` 用 `_act_confirmed("abandon_run")` 处理确认弹窗）。
- 即：需要重开一局时，本可先「放弃→主菜单→新一局」，框架却选择关掉整个游戏程序冷重启。

### 根因 C（保留，需区分）——真崩溃 / 卡死态

- 任何 `CRASH_ERROR` 第一次发生就直接 `GAME_RESTART`（`core/recovery.py`，原 :306 附近）——真崩溃合理，但「崩溃」可能含误诊的可恢复状态。
- phantom combat / travel hang → `relaunch_run`（`core/lifecycle.py:122/125`），注释断言「无 in-game 动作能退出」——这条是**真正不可避免的硬重启**，暂维持原逻辑。
- `core/watchdog.py` 只杀进程不重启，把决策交给 recovery。

---

## 3. 已完成的改动（工作区未提交）

原 WorkBuddy 改动（5 文件）+ 接手会话补齐：

| 文件 | 改动 | 意图 |
|---|---|---|
| `src/sts2_autotest/core/orchestrator.py` | ① `_AUTO_RESET_ABANDON_TIMEOUT`；② MAP/COMBAT 先 `_try_abandon_to_main_menu()`；③ 确认框走共享 `_abandon_confirm_action`（`confirm_modal` 优先，否则 `dismiss_modal`）；`start_session` 的 abandon 确认框复用同一 helper | 根因 B（Q9）+ P0 modal 对齐 |
| `src/sts2_autotest/core/lifecycle.py` | `_wait_until_ready` + `ensure_environment_ready` 启动宽限期 | 根因 A（Q10） |
| `src/sts2_autotest/core/recovery.py` | `_state_info`、`_abandon_confirm_action`、`_try_soft_reset_to_main_menu`；`_execute_game_restart` 硬重启前软放弃缓冲 | 根因 C + P0 modal 对齐 |
| `src/sts2_autotest/core/test_agent_runner.py` | Quartz/AppKit 的 `type: ignore` 从 `import-not-found` 改为 `import-untyped` | P0 mypy 三件套（本机已安装这些包） |
| `tests/unit/test_lifecycle.py` | 宽限期契约：可控不杀 / 耗尽才 terminate+重拉 | 根因 A |
| `tests/unit/test_orchestrator.py` | abandon 优先/失败兜底/`dismiss_modal`；硬重启单测注入假 Steam，避免真 `start_game` 空等 60s | Q9 + P0 |
| `tests/unit/test_recovery_strategy.py` | `_try_soft_reset_to_main_menu` 白名单/失败不掩盖崩溃/`dismiss_modal`；GAME_RESTART 软放弃成功则跳过 steam.restart | Q10 缓冲专项单测 |

**不要提交的他人改动**：`tests/generated/*.py` 等生成测试在接手时已是脏工作区，与本任务无关。

### 验证状态（接手会话 2026-08-19 22:03 UTC+8 复跑）

- ✅ 相关单测：`pytest tests/unit/test_lifecycle.py tests/unit/test_orchestrator.py tests/unit/test_orchestrator_lifecycle.py tests/unit/test_recovery_strategy.py -q` → **167 passed in 8.34s**
- ✅ 全量单测：`pytest tests/unit/ -q` → **1915 passed, 2 warnings in 474.39s**
- ✅ `mypy src/sts2_autotest --strict` → **Success: no issues found in 71 source files**
- ✅ `lint-imports` → **1 kept, 0 broken**
- ✅ `ruff check`（本任务改动文件）→ **All checks passed**
- ⚠️ 未跑：全量 `ruff check src/ tests/`（仓库无 ruff 配置，主干即有大量既有 style 报错）；集成/`requires_game` 真机测试

---

## 4. 待办任务

### P0 — 收尾质量门禁：**已完成**（2026-08-19 接手会话）

1. 三件套已跑通（见 §3 验证状态）。`test_agent_runner.py` 的 mypy `import-untyped` 已修。
2. `confirm_modal` / `dismiss_modal` 已抽到 `recovery._abandon_confirm_action`，与 `start_session`、journeys/AgentAdapter 的确认框命名对齐；仅有 `dismiss_modal` 时软放弃也会点掉。

### P1 — Q11：更正腾讯 PERF 文档
- 文档 `PNOPEsocVTPY` 当前定位是「CLI 子进程冷启动」；应改写为真实问题「**游戏进程在测试中被频繁硬重启**」。
- 核心修复方向写入：① 优先游戏内放弃（`abandon_run`）而非硬重启；② 启动宽限期防误诊（`ensure_environment_ready` 等待可控再判陈旧）。附本交接 §2 的代码行号佐证。
- 更新状态：若文档与 issue-37 同源，标注「阶段 A/C 已完成、仅剩阶段 B（常驻进程，5–6 人天，需 CLI 团队对齐接口）」。
- 说明：WorkBuddy 因自身 429 限流无法继续改文档，已交接手接会话；**不是**腾讯文档服务不可访问。

### P2 — 真机验证（需 Windows 11 / macOS + 游戏环境）
- 集成/回归流程启动游戏后，观察 **1 分钟内是否仍被二次拉起**（验收根因 A 修复）。
- 跑一场完整流程验证 Mid-run 重置走「游戏内放弃」而非冷重启（验收根因 B）。
- 用 `cli_launch_count` 埋点（`cli_mod.py:134/165`，注释注明供「同场战斗启动次数 ↓≥50%」验收）跑 5 场战斗取基线，判断阶段 B（常驻进程）是否值得 5–6 人天。

### P3 — 提交：**本轮已提交代码 + 本 handoff**
- 范围：`lifecycle.py` / `orchestrator.py` / `recovery.py` / `test_agent_runner.py` / 对应 3 个 `tests/unit/test_*.py` / 本文件。
- 未纳入：`tests/generated/`、`.tmpdir`、`.DS_Store`、`junit*.xml`（他人/无关脏文件）。

---

## 5. 注意事项 / 坑

1. **WorkBuddy 429 限流**（2026-08-19）：WorkBuddy 调腾讯文档 `get_content`/更新时被自身限流，无法继续干活，因此交接。腾讯文档本身仍可访问；接手会话应直接改 `PNOPEsocVTPY`，不要把「WorkBuddy 限流」当成「文档服务不可用」。
2. **仓库红线**（AGENTS.md）：不 revert/reset/checkout 未创建的改动；不改 public API 名称/签名；禁止 `model_construct()`；禁止裸 `except:`；新增修改同步单测。
3. **启动宽限期实现细节**：`_wait_until_ready` 用 `self.api_timeout`（150s）作窗口、`self.poll_interval` 作轮询间隔——单测里把两者调小以提速，真机行为需用默认值再确认一次。
4. **`_try_soft_reset_to_main_menu` 的屏幕白名单**：`MAP/COMBAT/EVENT/SHOP/REST/CHEST/CARD_REWARD/BOSS_REWARD` 才尝试放弃，干净主菜单直接返回 False 不空转。
5. 交接文档本身：接手 agent 完成后建议把本文件更新为「已结项」或归档，避免误导后续会话。

---

## 6. 建议 skills（接手 agent 按需加载）

- `sts2-start-game`：游戏启动原则（仅当无进程/证明失效才重拉；进程在加载中要等待不要重启）——与本次修复哲学一致，真机验证时必读。
- `sts2-independent-run-regression`：独立会话/分模块回归测试方法（127.0.0.1:8080 Agent API、冷重启拿独立新局等）。
- `sts2-gawain-regression-recovery`：M1–M7 真机回归（若验证目标是 Gawain MOD 全流程）。
- `test-report-html`：真机验证后产出标准化 HTML 测试报告。
- `code-review`：提交前按规范+需求双维度审查。

---

## 7. 一句话摘要

根因 = 预检 `ensure_environment_ready` 把「仍在启动的游戏」误判为陈旧进程而杀掉重拉（主因）+ Mid-run 重置不优先用游戏内放弃（次因）。已实现：启动宽限期、放弃优先、崩溃前软放弃缓冲（含 `dismiss_modal` 对齐）。P0 质量门禁已通过（1915 单测 / mypy / lint-imports）。剩余：更正腾讯文档 `PNOPEsocVTPY`、真机验证。
