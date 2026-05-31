# B13 桌面通知 — Session Handoff

> 类型：设计前 handoff（brainstorming 尚未完成）
> 日期：2026-05-31
> 来源：beta-roadmap.md P3，描述为 "运行完成后通知"
> 下一个 session：从 brainstorming 第一步"探索项目上下文 + 提出 2-3 方案"继续

---

## 任务概述

测试运行完成后（无论成功/失败/崩溃），向用户桌面发送系统通知。主要用于：
- 长时间无人值守测试（≥4h），用户不需一直盯着终端
- CI 本地模拟（`autotest run --all` 在后台运行）
- 多任务并行时，提示哪个 session 已完成

## 已有基础设施（完美集成点）

| 资产 | 位置 | 说明 |
|------|------|------|
| `session_end` hook | `pytest_plugin/hooks.py:16` | 7 个生命周期 hook 之一 |
| `pytest_sessionfinish` | `pytest_plugin/plugin.py:100-102` | pytest hook，调用 `fire("session_end")` |
| `HookRegistry` | `pytest_plugin/hooks.py:25-51` | 模块级回调注册表，支持多回调 |
| `ExitStatus` | pytest 内置 | `exitstatus` 参数传入 `pytest_sessionfinish`（int） |
| `SummaryJson` | `common/evidence.py` | 包含 pass/fail/crash 统计 + duration_ms |
| `TestOrchestrator` | `core/orchestrator.py` | 会话生命周期管理 |

**关键洞察：** `session_end` hook 已经存在并被触发，B13 只需要注册一个回调即可。不需要改动核心架构。

## 关键设计决策点（下一个 session 需要讨论）

### 1. 通知实现方式 — 平台兼容性

这是最大的设计选择。项目主要运行在 Windows 11，开发在 macOS，未来不排除 Linux CI。

- **选项 A：专用平台库（推荐 — 分层设计）**
  - Windows：`win10toast` 或直接 `win32api` Shell_NotifyIcon
  - macOS：`osascript`（AppleScript 通知）或 `pync`（PyObjC 桥接）
  - Linux：`notify-send`（D-Bus）
  - 优点：原生体验，不依赖外部服务
  - 缺点：需要平台检测 + 3 套实现

- **选项 B：统一跨平台库**
  - `desktop-notifier`（纯 Python，支持 Windows/macOS/Linux）
  - `plyer`（Kivy 生态，支持多平台通知）
  - 优点：一套代码
  - 缺点：额外依赖，行为在各平台可能略有差异

- **选项 C：纯 stdlib Monkey-patch**
  - Windows：`ctypes` 调用 `user32.dll` MessageBeep + 终端 title 闪烁
  - macOS/Linux：终端 bell 字符 `\a` + `os.system("osascript ...")`
  - 优点：零依赖
  - 缺点：简陋，无现代通知中心集成

### 2. 通知触发时机

`session_end` hook 是最自然的触发点，但还有几个额外场景值得考虑：

| 场景 | 时机 | 优先级 |
|------|------|--------|
| 测试全部完成 | `session_end` | 必须 |
| 崩溃恢复次数达到阈值 | `RecoveryDecision(action==TERMINATE)` | 建议 |
| 磁盘空间不足 | `DiskGuard` 告警 | 建议 |
| 队列中排队任务开始 | `SessionQueue` pop | 可选 |
| Watchdog 检测到僵尸 session | `Watchdog` 触发 | 可选 |

**建议 MVP 仅覆盖 "测试全部完成"**，其他场景通过 HookRegistry 可插拔扩展。

### 3. 通知内容设计

```
标题：STS2 Autotest — 测试完成
正文选项：
  A) "3/5 通过，1 失败，1 崩溃 — 耗时 42m 18s"
  B) "⚠️ 测试会话完成（通过: 3, 失败: 1, 崩溃: 1）— 42 分钟"
  C) "42 分钟内完成 5 个测试 — 点击查看报告"

动作按钮（仅部分平台支持）：
  - "打开证据目录"
  - "查看摘要"
```

### 4. 配置集成

通知行为应该通过 `STS2Config` 控制：

```python
# config/schema.py 新增字段（示例）
STS2_NOTIFICATIONS__ENABLED: bool = True
STS2_NOTIFICATIONS__ON_SUCCESS: bool = True      # 全部通过也通知
STS2_NOTIFICATIONS__ON_FAILURE: bool = True      # 有失败就通知
STS2_NOTIFICATIONS__ON_CRASH: bool = True        # 崩溃立即通知
STS2_NOTIFICATIONS__SOUND: bool = True
```

### 5. 实现层次

```
┌─────────────────────────────────────────────┐
│  pytest_sessionfinish (已存在)                │
│  → fire("session_end")                       │
├─────────────────────────────────────────────┤
│  B13 注册回调 (新增)                          │
│  → _on_session_end_notify(exitstatus, ...)   │
├─────────────────────────────────────────────┤
│  Notifier 抽象层 (新增)                       │
│  → class DesktopNotifier(Protocol)            │
│  → class WindowsNotifier                      │
│  → class MacOSNotifier                        │
│  → class LinuxNotifier                        │
├─────────────────────────────────────────────┤
│  平台后端 (新增)                               │
│  → Windows: ctypes/win32api                   │
│  → macOS: osascript subprocess                │
│  → Linux: notify-send subprocess              │
└─────────────────────────────────────────────┘
```

## 建议的 MVP 设计（供讨论）

### 新增模块概览

| 模块 | 位置 | 职责 |
|------|------|------|
| `DesktopNotifier` (Protocol) | `common/types.py` | 通知器接口定义 |
| `platform_notifier.py` | `core/platform_notifier.py` | 平台检测 + 工厂函数 + 3 种实现 |
| 注册回调 | `pytest_plugin/fixtures.py` 或 `cli/main.py` | 在 session 启动时注册 `session_end` 回调 |
| 配置扩展 | `config/schema.py` | 新增 `NotificationsSettings` |

### MVP 功能范围

1. ✅ `session_end` → 桌面通知（通过/失败/崩溃统计）
2. ✅ Windows 10/11 Toast 通知 + macOS 通知中心
3. ✅ 通过 `STS2Config` 启用/禁用
4. ❌ 交互按钮（"打开报告"等 — 这是 V2）
5. ❌ Watchdog/DiskGuard 通知 — 通过可插拔 hook 未来扩展
6. ❌ Linux 支持 — 暂不实现（非目标平台）

## 需要注意的约束

1. **无阻塞**：通知不得阻塞主流程。`fire("session_end")` 已对回调异常做了 try/except，但通知本身应为 fire-and-forget。
2. **依赖最小化**：优先用 `ctypes` + subprocess，避免引入需要编译的库（如 PyObjC）。
3. **静默模式**：`--ci` 模式下通知自动禁用。
4. **测试友好**：`DesktopNotifier` 作为 Protocol，单元测试注入 stub。
5. **与 B3（无人值守）的关系**：无人值守场景是通知的核心用例，但通知不应假设用户一定在屏幕前 — 走系统通知中心，而非终端输出。

## 相关文件

- `src/sts2_autotest/pytest_plugin/hooks.py` — `session_end` hook + `HookRegistry`（关键集成点）
- `src/sts2_autotest/pytest_plugin/plugin.py:100-102` — `pytest_sessionfinish` 触发 `fire("session_end")`
- `src/sts2_autotest/common/types.py` — Protocol 接口定义处（应在此定义 `DesktopNotifier` Protocol）
- `src/sts2_autotest/config/schema.py` — `STS2Config`，需新增通知配置
- `src/sts2_autotest/common/evidence.py` — `SummaryJson`（通知内容的数据来源）
- `docs/beta-roadmap.md` — B13 在 P3 (line 106)，描述 "运行完成后通知"

## 下一个 session 的启动建议

```
/brainstorming B13 桌面通知 — 从 handoff 继续
```

Session 应该先审阅此 handoff + `pytest_plugin/hooks.py`（理解 hook 机制）→ 按照 brainstorming checklist：确认范围 → 讨论平台方案 → 呈现设计 → 生成 spec → 进入 writing-plans。
