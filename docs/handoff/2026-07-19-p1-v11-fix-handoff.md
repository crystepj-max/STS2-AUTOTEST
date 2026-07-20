# 2026-07-19 P1 跨 Agent 通用性 V11 修复与验收交接

## 零、本交接与 V10 复核交接的关系

本交接接续 `docs/handoff/2026-07-19-p1-v10-review-handoff.md`。V10 复核的权威结论
是：**P1 尚未完成，V10 不接受 PASS**——干净主菜单假阳性、最多启动一次未实现、
六操作与幂等未真实验证。

V11 的工作目标：修复上述全部问题，并用真实三柱验收（ORIG 局内取消 / RESUME 恢复
PASSED / SECOND 连续任务取消）重新取证。执行者为 Claude Code。

## 一、V11 执行的修复（全部有单元检查覆盖）

### 0. 生产适配器信号层（V11 收官阶段发现的最隐蔽根因）

**现象**：v11–v11h 连续七轮真实验收中，取消收尾与开局前清理反复判
「无法确认干净」或空转超时，但同一时段用 HTTP 通道手动探针一切正常。

**根因**：平台有两条游戏控制通道。Agent 适配器（HTTP）把真实动作列表
**内嵌在状态里**；而生产任务实际使用的 CliMod 适配器（sts2 CLI 子进程）
**状态不内嵌动作**（菜单动作由屏幕类型静态派生，须经适配器协议方法
`get_available_actions()` 单独获取）。最初几版判定逻辑从状态里读动作，
在 CliMod 路径下永远读到空列表 → 永远判"无法确认干净"。

**修复**：判定信号统一走 `_frame_signals`——状态内嵌动作优先，缺失时
退回适配器协议方法。内省字段 `menu.has_run_save`（两条通道都提供）为
旧局判定的最高信任级；CliMod 的静态动作列表与 Agent 的陈旧动作列表
一样，**不再**作为旧局判据。现场探针（CliMod 生产路径：建局→杀游戏→
重启→放弃旧局→干净确认）27 秒全绿：`reports/v11/probe-recovery-cli.log`。

### 1. 干净主菜单判定重写（V10 复核 P0-1 / P0-2）

- 稳定读取函数 `_settle_main_menu_state` 从「只返回布尔」改为返回
  （最近可判定帧, 三态结论 dirty/clean/undecidable）；
- 空操作列表 = 菜单仍在初始化/重建，该帧不可用于判定；
- 干净结论要求**连续 3 个可判定帧**均满足干净定义（双向防瞬态：既防旧局
  信号晚到的假干净，也防放弃成功后菜单重建期的瞬态帧）；
- `ok`（取消成功的唯一出口）要求满足干净主菜单全部条件，不再只看
  `screen=MAIN_MENU`；
- 取消终态严格映射：干净+证据封存 → CANCELLED；能控制但清不掉/无法确认 →
  FAILED_PLATFORM；控制入口不可用 → BLOCKED_ENVIRONMENT。

### 2. 存档信号信任层级（V11 真实验收新发现，V10 未覆盖）

真实证据（v11 轮 RESUME 证据包轨迹 + 操作序列）：

- 放弃旧局成功后，菜单重建时会**短暂摆出陈旧动作**（continue_run/abandon_run
  仍在动作列表中），但内省字段 `menu.has_run_save=false`，且 start_new_run
  可**直接开局、无放弃确认框**——证明存档确已删除、动作是伪影；
- 因此判定改为三态信任层级：`has_run_save` 显式 True → 有旧局；显式 False →
  无旧局（忽略陈旧动作）；字段未发布 → 退回动作列表判断。

### 3. 模组加载等待（V11 真实验收新发现，截图实证）

- 游戏重启后画面先显示主菜单，**控制模组仍在加载**（界面右下角「正在加载
  模组运行」，实测 60s～>180s 波动），期间动作列表为空；
- 取消收尾现在先等「菜单真正可操作」（动作非空，operational_timeout=360s），
  等不到 → 如实 BLOCKED_ENVIRONMENT，禁止第二次启动；
- 开局前清理（`_ensure_clean_main_menu`）同样先等可操作（300s）再判定。

### 4. 最多启动一次真实实现（V10 复核 P0-3）

- 删除「首次等不到主菜单就再启动一次」的第二次启动路径；
- `restart_count` 只在一处启动点后自增，自动检查断言启动方法恰被调用一次。

### 5. 报告完整状态（V10 复核 P1-1）

- 取消报告 `recovery.final_state` 保存恢复后完整快照：screen、
  has_run_save（三态）、available_actions、continue/abandon/new_run 标志、
  状态时间戳、采集时间（ISO 8601 UTC）；
- 干净确认**之后**采集最终截图（`*_recovery_final.jpg`）随证据包封存。

### 6. 验收驱动成为项目资产（V10 复核 P0-4 / P1-3）

- `scripts/p1_v11_acceptance.py`：项目内可复用驱动，逐步保存全部公共响应到
  `raw/`，判定 `--verdict-only` 可从 raw 重算，禁止写死期望值；
- 六操作全部真实调用（get_report 不再只在本地缺包时兜底）；
- 幂等重复提交实测（相同 run_id + 相同 created_at）；
- capabilities 真实核对六操作清单；
- 取消时机改为「确认已开局」的首个页面（CHARACTER_SELECT 即可取消，此时
  存档已创建）——等到 EVENT 再取消会与旅程完成竞争（v11c SECOND 实测在
  取消生效前 PASSED）。

### 7. 单元检查（先复现失败，再修复）

`tests/unit/test_cli.py::TestCleanMainMenuRecovery` 共 10 条 + 更新 2 条过期
检查，全部注入无等待时钟（单条 <1s）：

1. 首帧干净、稳定帧晚到 continue_run → 必须放弃并判干净；
2. 放弃后瞬态干净、旧局复现 → 不得判干净（V10 假阳性复现）；
3. 旧局清不掉 → FAILED_PLATFORM 而非 BLOCKED_ENVIRONMENT；
4. 可操作但无开新局能力 → 不得判干净（不只信 screen）；
5. has_run_save 显式 False + 陈旧 continue/abandon 动作 → 判干净（伪影容忍）；
6. 内省字段未发布 + continue_run → 判脏并放弃；
7. 放弃后空动作重建帧 → 等到菜单发布再判（V11 假阴性复现）；
8. 模组加载期空动作菜单 → 等到可操作再判；
9. 模组永不发布动作 → BLOCKED_ENVIRONMENT；
10. 一次启动后未到主菜单 → 禁止第二次启动、restart_count 真实、环境阻塞；
11. 成功路径 restart_count=1 且恰启动一次；
12. 报告保留完整恢复后状态（P1-1）。

## 二、自动检查结果（最终代码）

- 单元全量：**1678 passed / 5 failed**（`reports/v11/unit-full-final2.txt`）。
  经 git stash 对照实验证明这 5 项在本轮改动之前即失败（非本轮引入），
  但其中 3 项 `test_cli_command_mapping` 属于「局内正常返回主菜单」
  能力与检查预期不一致，**与 P1 恢复能力领域相关，不能表述为完全无关**；
  1 项为新增页面状态（TRI_SELECT）未同步到旧检查；1 项为启动检查的
  模拟环境与当前 macOS 启动方式不一致。结论：**仓库完整回归为 PARTIAL，
  不能表述为"自动检查全部通过"**——5 项失败随 P1.1 清理。
- 集成：30 passed / 5 skipped（`reports/v11/integration.txt`）；
- `python -m compileall -q src` 通过；`lint-imports` KEPT；
- `git diff --check` 通过。

## 三、真实验收轮次记录（证据全部保留，未覆盖）

| 轮次 | 目录 | 结果 | 说明 |
| --- | --- | --- | --- |
| v11 | `tests/output/cross-agent-p1/p1-platform-fix-20260719-v11/` | PASS=false | 新判定首次实战：restart_count=1、放弃真实执行、证据封存、幂等通过、RESUME PASSED；暴露菜单重建空动作窗口（6s 太短）→ FAILED_PLATFORM 如实上报 |
| v11b | `.../p1-platform-fix-20260719-v11b/` | PASS=false | 窗口放宽到 15s 仍不够（并行跑全量单测抢 CPU，实验失误）；ORIG EVENT 取消、RESUME PASSED |
| v11c | `.../p1-platform-fix-20260719-v11c/` | PASS=false | 截图实证「正在加载模组运行」根因；SECOND 取消与旅程完成竞争（PASSED 抢先）→ 驱动取消时机修正 |
| v11d | `.../p1-platform-fix-20260719-v11d/` | PASS=false | 可操作等待 180s 仍不够（>180s，当日第 12 次硬重启后环境降级）→ 放宽到 360s；RESUME PASSED；SECOND 因开局前清理 >10 分钟未在驱动窗口内入局 |
| v11e | `.../p1-platform-fix-20260719-v11e/` | PASS=false | 环境静置后仍超时（预检探测抖动触发不必要重启 + 就绪放行空动作菜单，即"重启搅拌机"）→ 预检重试与就绪可操作修复 |
| v11f | `.../p1-platform-fix-20260719-v11f/` | PASS=false | 环境重启后预检仍抖动；深度探针证实恢复函数 16 秒可用，锁定生产通道差异 |
| v11g | `.../p1-platform-fix-20260719-v11g/` | PASS=false | 同根因（CliMod 状态不内嵌动作）空转；用户配合保留现场 |
| v11h | `.../p1-platform-fix-20260719-v11h/` | PASS=false | 预检抖动修复生效（无不必要重启），但 CliMod 信号层缺陷仍在 → 信号层统一修复 |
| **v11i** | `.../p1-platform-fix-20260719-v11i/` | **PASS=true** | **三柱全绿：ORIG CANCELLED（78s）、RESUME PASSED（41s）、SECOND CANCELLED；31 项判定全部通过** |
| **v11j** | `.../p1-platform-fix-20260720-v11j/` | **PASS=true** | **P1.1 复核后收官：截图判定失败路径修复 + 报告动作来源语义中性化后重跑；33 项判定全部通过；新报告真实包含 actions_source/actions_note** |

失败引导记录：`p1-platform-fix-20260719-v11-attempt0-preflight/`（防睡眠残留前置拦截）。

## 四、V11 已验证的跨 Agent 能力（真实证据，不随 v11e 结果改变）

1. **六操作全部可经公共 MCP 调用**（capabilities 清单实测含全部六项）；
2. **幂等重复提交返回同一 run_id**（多轮实测，created_at 一致）；
3. **每次取消最多真实启动一次**（每轮 recovery.restart_count=1）；
4. **RESUME 恢复链路四轮 100% 通过**：resume_run 创建新 run_id、
   resumed_from 精确指向原任务、最终 PASSED、证据封存；
5. **平台不再谎报**：不确定时如实 FAILED_PLATFORM / BLOCKED_ENVIRONMENT，
   并附完整 final_state 供独立审计；
6. **放弃旧局真实生效**：放弃后 start_new_run 直接开局、无确认框（v11 轮
   RESUME 操作序列实证）；
7. **开局前清理路径真实验证**：面对陈旧动作不误判、不滥用重启（孤儿
   RESUME 轮实证）；
8. **取消后最终截图入包**（`*_recovery_final.jpg`）；
9. **防睡眠生命周期**：运行中存在、正常结束后无残留（孤儿进程已被识别
   并清除过一次历史泄漏）。

## 五、已知环境风险（供下一轮注意）

- 游戏重启后模组加载时长波动巨大（60s～>180s），当日多次硬重启后显著
  变长——判定时必须等「可操作」而非「画面到了」；
- 取消时机过晚会与旅程完成竞争（旅程 PASSED 优先于取消生效）；
- 长时间多轮硬重启会让单次验收周期拉长（每柱 3-8 分钟），驱动等待窗口
  需按此预算。

## 六、v11i 最终结果：P1 = **PASSED**

### 三柱终态（全部经公共 MCP 六操作完成，原始响应在 `raw/`）

| 柱 | run_id | 终态 | 关键数据 |
| --- | --- | --- | --- |
| ORIG | `run-20260719-231648-5ffe6829` | **CANCELLED**（78 秒） | pre_cancel=EVENT；restart_count=1；clean_main_menu=true；old_run_abandoned=true；final_state 无旧局且含开新局能力；恢复后截图入包；证据封存 |
| RESUME | `run-20260719-231806-8f123c65` | **PASSED**（41 秒） | resumed_from 精确指向 ORIG；从干净起点开局；证据封存 |
| SECOND | `run-20260719-231848-a41cf0c0` | **CANCELLED** | pre_cancel 局内；restart_count=1；干净主菜单判定为真；不继承 RESUME 残局；证据封存 |

### 判定（`raw/16-verdict.json`，31 项全过）

- 六操作清单真实核对 ✓；幂等重复提交同一 run_id（created_at 一致）✓
- 两柱取消：局内取消 ✓、CANCELLED ✓、get_run/get_report 一致 ✓、
  实际启动一次 ✓、干净主菜单为真 ✓、恢复后完整状态无旧局 ✓、
  最终截图入包 ✓、证据封存且压缩包可读 ✓
- 恢复：新 run_id ✓、resumed_from 正确 ✓、PASSED ✓、报告一致 ✓
- 防睡眠：运行中存在 ✓、结束后无残留 ✓；无 BLOCKED_ENVIRONMENT ✓

### 结论（经两轮独立复核确认）

对照 V10 复核交接「完成定义」14 条：全部满足。独立复核重新计算判定
结果一致，人工查看 ORIG/SECOND 最终截图确认为无旧局入口的干净主菜单。
正式登记状态：

> **P1 跨 Agent 通用性：PASSED（V11i 三柱验收 + V11j 复核收官复验）**
> **仓库完整回归：全绿（单元 1693 / 集成 30 / 编译 / 层级 / diff）**

任何外部 Agent 仅凭六个公开操作即可完成"提交→幂等→局内取消→干净
恢复→恢复任务→报告取证"全生命周期，且平台在失败时如实区分环境阻塞
与平台失败，不再假报。

### 两项非阻塞风险（随 P1.1 处理）

- 🟡 **最终状态报告语义矛盾**：`final_state` 同时记录 `has_run_save=false`
  与动作清单含 continue/abandon（CliMod 静态动作名）。人工截图与后续
  任务已证明菜单确实干净——是报告展示问题，不是恢复失败。建议区分
  「真实可点击动作」与「静态动作名称」，避免其他 Agent 误读。
- 🟡 **截图内容未入机器判定**：当前仅校验最终截图存在于压缩包，不自动
  判断内容（游戏窗口/非黑屏/非旧画面）。本轮已由人工核验真实有效。

### P1.1 验收收尾（已完成）

1. **清理 5 项失败检查（完成）**：
   - 3 项 `test_cli_command_mapping`：更新预期为「局内不广告 return_to_menu」
     （sts2 CLI 仅 GAME_OVER/VICTORY 屏支持该动作，回菜单由受控重启兜底——
     与 P1 决策一致的口径固化）；
   - 1 项 `test_common_state`：GameScreen 期望集合同步 TRI_SELECT；
   - 1 项 `test_lifecycle`：启动检查改为拦截 launch() 计数（与 macOS open /
     Popen 启动机制无关），消除 fake_popen 与 macOS 启动方式不一致。
   **单元全量 1693 passed / 0 failed**（`reports/v11/unit-full-p11j.txt`）。
2. **消除报告动作矛盾（完成，经 V11j 真实公共入口验证）**：`final_state`
   新增 `actions_source`（state_reported / adapter_derived / none）——
   只描述「谁报告的」而不声称「此刻可点击」：state_reported 为状态接口
   报告值（菜单重建期可能为陈旧报告），adapter_derived 为适配器派生/
   静态名称；两种来源下 continue/abandon 都不单独作为旧局证据，
   派生来源下对应标志置 None，均附 `actions_note` 指明旧局以
   has_run_save 为准。V11j 新 ORIG/SECOND 报告已真实包含上述字段。
3. **截图内容入机器判定（完成，含失败路径单测）**：验收驱动
   `_verify_final_screenshot`——最终截图必须存在、体积 ≥50KB、
   **JPEG 尺寸必须可解析且 ≥1280×720**（纯 stdlib SOF 解析、跨平台；
   读取失败/无法解析一律判失败，杜绝「读不出尺寸」假通过路径——
   V11 复核复现项，已补 10 条失败路径单测 `tests/unit/test_p1_v11_acceptance.py`）。
   V11j 真实证据复核通过（ORIG 331.9KB、SECOND 330.7KB，均 1920×1080）。
4. **整理提交（完成）**：P1 完整工作（V10+V11+P1.1）作为一个连贯提交入库
   （`10f26e5`，标题「feat: 完成 P1 跨 Agent 通用性验收
   （V11i+V11j PASSED）」）。本地配置（.env）、工具目录、缓存与大体型探针
   截图未入库；提交已推送至 `origin/main`，当前本地 `main` 与远端一致。
