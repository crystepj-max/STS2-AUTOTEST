# B13 桌面通知 — Review Handoff

> 类型：实现完成 → 代码审查
> 日期：2026-05-31
> 来源：[设计规格](./2026-05-31-b13-desktop-notifications-design.md)
> 实现计划：[2026-05-31-b13-desktop-notifications.md](../plans/2026-05-31-b13-desktop-notifications.md)
> 审查后：合并到 main，调用 `superpowers:finishing-a-development-branch`

---

## 概述

测试运行完成后向桌面发送系统通知。5 个 Task，27 个新测试，零新依赖，零回归。

## 变更文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/sts2_autotest/common/types.py` | +10 行 | `DesktopNotifier` Protocol：`notify(title, message, level)` |
| `src/sts2_autotest/config/schema.py` | +12 行 | `NotificationsConfig`（enabled/on_success/on_failure/on_crash）+ 集成到 STS2Config |
| `src/sts2_autotest/core/notifier.py` | **新建** ~120 行 | `WindowsNotifier`(ctypes)、`MacOSNotifier`(osascript)、`StubNotifier`、`_build_osascript()`、`create_desktop_notifier()` |
| `src/sts2_autotest/pytest_plugin/plugin.py` | +80 行 | exitstatus 透传、6 个辅助函数、`pytest_configure` 注册回调 |
| `.env.example` | +5 行 | `STS2_NOTIFICATIONS__*` 环境变量 |
| `tests/unit/test_notifier.py` | **新建** ~190 行 | 14 测试：Protocol 合规、工厂分发、macOS/Windows/Stub 实现 |
| `tests/unit/test_notifications_config.py` | **新建** ~40 行 | 4 测试：默认值、自定义值、frozen、STS2Config 集成 |
| `tests/unit/test_plugin.py` | **新建** ~190 行 | 9 测试：hook 触发、回调注册、通知级别、异常隔离 |

## 关键设计决策

- **零新依赖**：Windows 用 `ctypes` → `Shell_NotifyIconW`，macOS 用 `subprocess` → `osascript`
- **失败不阻塞**：通知器创建/发送失败被 try/except 捕获，不影响测试执行。`pytest_configure` 中延迟创建通知器（在回调闭包内）
- **CI 自动禁用**：检查 `CI` 环境变量跳过注册
- **配置读取**：`plugin.py` 直接读环境变量（不能导入 `config/`，因为层级隔离），YAML 配置路径暂不支持
- **消息格式**：emoji + 中文标签 → `"⚠️ 测试会话完成（通过: 3, 失败: 1, 崩溃: 1）— 42 分钟"`

## 数据流

```
pytest_sessionfinish(exitstatus)
  → fire("session_end", exitstatus=exitstatus)
    → _callback (闭包，延迟创建 notifier)
      → _on_session_end_notify(exitstatus, notifier)
        → 读 _load_notifications_config() 判断是否发送
        → 根据 exitstatus 判断级别 (0=info, 非0=warning)
        → _build_notification_message() → 尝试读 summary.json，fallback 简单消息
        → notifier.notify(title, message, level)
```

## 验证结果

- **单元测试**：1113 pass, 4 pre-existing fail（macOS 上 Windows-only `ctypes.windll` 测试）
- **mypy strict**：0 new errors
- **lint-imports**：Contracts kept
- **冒烟测试**：macOS 通知中心正常弹出

## 建议审查重点

1. **Windows 通知**：`NIM_ADD` → 发送 → `NIM_DELETE` 模式是否正确（seq 无法验证图标生命周期）？`NOTIFYICONDATAW` 结构体字段是否与 Vista+ 版本匹配？
2. **`_build_osascript` 转义**：反斜杠 → 双引号 → 换行 → 回车的转义顺序是否正确？是否有 AppleScript 注入风险？
3. **配置一致性**：`_load_notifications_config()` 的环境变量名与 `NotificationsConfig` 字段名是否一致？YAML 路径被绕过的文档是否充分？
4. **测试覆盖盲区**：`_read_latest_summary`、`_format_duration`、`_build_notification_message` 仅有间接测试。是否需要补充隔离单元测试？
5. **导入层级**：`pytest_plugin/plugin.py` 惰性导入 `core/notifier` 是否合规？`core/` 是 `pytest_plugin` 的下层依赖（`pytest_plugin` → `core` → `common`）

## 已知限制

- YAML 配置路径对通知不生效（`pytest_plugin` 无法导入 `config/`）
- Linux `notify-send` 未实现（预留 StubNotifier）
- 无交互按钮（V2）
- 无 Watchdog/DiskGuard 通知（通过 HookRegistry 可插拔扩展）

## 相关 Commit

```
71358a3 feat: add DesktopNotifier Protocol and StubNotifier tests
d71726b feat: add NotificationsConfig with defaults and STS2Config integration
d4ad7ae feat: add platform notifier implementations (Windows/MacOS/Stub)
997e1ba fix: address code review issues for platform notifier implementations
750ce2f feat: wire desktop notifications into session_end hook
43323a0 fix: make notifier creation lazy to prevent pytest startup crashes
dff1427 docs: add B13 notification env vars to .env.example
```
