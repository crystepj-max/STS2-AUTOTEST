# B13 桌面通知 — 设计规格

> 日期：2026-05-31
> 来源：beta-roadmap.md P3，"运行完成后通知"
> 前置 handoff：[2026-05-31-b13-desktop-notifications-handoff.md](./2026-05-31-b13-desktop-notifications-handoff.md)

## 概述

测试运行完成后（无论成功/失败/崩溃），向用户桌面发送系统通知。主要用于长时间无人值守测试（≥4h）、CI 本地模拟、多任务并行时提示哪个 session 已完成。

## 设计决策

| 决策点 | 选择 |
|--------|------|
| 平台实现 | 方案 A：分层专用，零新依赖（Windows `ctypes`、macOS `osascript`、Linux `notify-send`） |
| 正文格式 | 方案 B：`"⚠️ 测试会话完成（通过: 3, 失败: 1, 崩溃: 1）— 42 分钟"` |
| 通知级别 | 区分：全部通过 = info，有失败/崩溃 = warning |
| MVP 范围 | 仅 `session_end` hook；Watchdog/DiskGuard 等场景未来可插拔扩展 |
| 交互按钮 | V2（非 MVP） |

## 模块布局

| 模块 | 位置 | 职责 |
|------|------|------|
| `DesktopNotifier` Protocol | `common/types.py` | 通知器接口：`notify(title, message, level)` |
| 平台实现 + 工厂 | `core/notifier.py` | `create_desktop_notifier()` → `WindowsNotifier` / `MacOSNotifier` / `StubNotifier` |
| 通知配置 | `config/schema.py` | `NotificationsConfig`（enabled/on_success/on_failure/on_crash） |
| 回调注册 | `pytest_plugin/plugin.py` | 在 `pytest_configure` 中注册 `session_end` 回调 |

## 数据流

```
pytest_sessionfinish(session, exitstatus)
  → fire("session_end", exitstatus=exitstatus)
    → _on_session_end_notify(exitstatus)
      → 根据 exitstatus 判断级别 (0=info, 非0=warning)
      → 尝试从 evidence 目录读取 summary.json（latest/ 或当前 run）
      → 若 summary.json 存在：提取 pass/fail/crash 计数 + duration_ms
      → 若 summary.json 不存在：使用 fallback 简单消息（仅含 exit code）
      → 格式化正文
      → DesktopNotifier.notify(title, message, level)
        → Windows: ctypes → Shell_NotifyIconW
        → macOS: subprocess → osascript
        → 未知平台: StubNotifier (no-op)
```

回调通过读取磁盘上的 `summary.json` 获取详细统计，而非通过 hook 参数传入。这样无需修改 hook 签名，且在 CLI 直接运行和 pytest 子进程两种路径下均可用。

## 接口定义

### DesktopNotifier Protocol（`common/types.py`）

```python
class DesktopNotifier(Protocol):
    """Protocol for desktop notification — implemented by platform backends."""

    def notify(self, title: str, message: str, level: str) -> None: ...
```

`level` 取值：`"info"` | `"warning"`。平台实现负责映射到原生通知级别。

### 通知正文格式

```
标题：STS2 Autotest — 测试完成
正文：⚠️ 测试会话完成（通过: 3, 失败: 1, 崩溃: 1）— 42 分钟
```

- 全部通过时 emoji 为 ✅，级别 info
- 有失败/崩溃时 emoji 为 ⚠️，级别 warning
- 耗时格式：`<1 分钟` / `X 分钟` / `X 小时 Y 分钟`

## 配置

### NotificationsConfig（`config/schema.py`）

```python
class NotificationsConfig(BaseModel):
    """Desktop notification configuration."""
    model_config = ConfigDict(frozen=True)
    enabled: bool = True
    on_success: bool = True   # 全部通过也通知
    on_failure: bool = True   # 有失败就通知
    on_crash: bool = True     # 崩溃立即通知
```

集成到 `STS2Config`：
```python
class STS2Config(BaseModel):
    # ... 现有字段 ...
    notifications: NotificationsConfig = NotificationsConfig()
```

### 环境变量

```
STS2_NOTIFICATIONS__ENABLED=true
STS2_NOTIFICATIONS__ON_SUCCESS=true
STS2_NOTIFICATIONS__ON_FAILURE=true
STS2_NOTIFICATIONS__ON_CRASH=true
```

### CI 自动禁用

在 `pytest_configure` 注册时检查 `CI` 环境变量，若为真则跳过注册。`--ci` CLI flag 由 `autotest doctor --ci` 传入。

## 平台实现

### WindowsNotifier

- 通过 `ctypes` 调用 `Shell32.Shell_NotifyIconW`
- 零外部依赖
- 通知级别映射：`info` → `NIIF_INFO`，`warning` → `NIIF_WARNING`
- 无需持久图标（`NIF_INFO` + `NIIF_NOSOUND` 或用户可配声音）

### MacOSNotifier

- 通过 `subprocess.run` 调用 `osascript -e 'display notification ...'`
- 零外部依赖
- 通知级别映射：`warning` 时追加 `with icon caution`
- macOS 通知中心原生集成

### StubNotifier

- 所有方法 no-op
- 用于未知平台和 CI 模式
- 也是单元测试的默认实现

## 回调注册

### pytest_plugin/plugin.py 改动

1. **`pytest_sessionfinish`**：fire 时传入 `exitstatus`（pytest 内置参数）：

```python
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    fire("session_end", exitstatus=exitstatus)
```

2. **`pytest_configure`**：新增通知回调注册逻辑：

```python
def pytest_configure(config: pytest.Config) -> None:
    # ... 现有 marker 注册 ...
    _register_notification_callback()
```

`_register_notification_callback()` 从环境变量读取配置，若启用则创建 notifier 并注册 `session_end` 回调。

### 回调函数

```python
def _on_session_end_notify(exitstatus: int) -> None:
    """session_end hook 回调：发送桌面通知。"""
    # 1. 读取配置决定是否发送（on_success/on_failure/on_crash）
    # 2. 尝试从 evidence 目录读取 summary.json 获取统计数据
    # 3. 若 summary.json 存在 → 提取 pass/fail/crash + duration → 格式化正文
    # 4. 若 summary.json 不存在 → fallback: "测试会话完成 (exit code: X)"
    # 5. 根据 exitstatus 判断级别 (0=info, 非0=warning)
    # 6. notifier.notify(title, message, level)
```

回调内部异常被 `HookRegistry.fire()` 静默 catch + log，不影响主流程。summary.json 读取依赖证据打包器在 session 结束前完成写入；若时序不满足，fallback 消息确保通知仍会发出。

## 错误处理

- 通知失败不得阻塞主流程：`HookRegistry.fire()` 已对每个回调做 try/except
- 平台后端内部异常（如 `osascript` 超时、`ctypes` 调用失败）由各自实现 catch 并 log warning
- `subprocess` 调用设置 5 秒超时，防止僵尸进程

## 测试策略

| 测试层级 | 内容 |
|----------|------|
| 单元测试 | `StubNotifier` 验证调用参数（title/message/level 正确传入） |
| 单元测试 | 平台实现各自独立测试（mock `ctypes`/`subprocess`） |
| 单元测试 | `_on_session_end_notify` 回调逻辑（exitstatus 分支、on_success/on_failure/on_crash 跳过逻辑） |
| 单元测试 | `NotificationsConfig` 配置解析（默认值、环境变量覆盖） |
| 集成测试 | `pytest_configure` 注册流程（验证 hook 已注册到 registry） |

## 不包含（V2+）

- 交互按钮（"打开报告"、"查看摘要"）
- Watchdog/DiskGuard 通知
- Linux 平台实现（`notify-send` 子进程，代码预留但暂不激活）
- 通知声音定制

## 相关文件

- `src/sts2_autotest/common/types.py` — 新增 `DesktopNotifier` Protocol
- `src/sts2_autotest/core/notifier.py` — 新增：平台实现 + 工厂函数
- `src/sts2_autotest/config/schema.py` — 新增 `NotificationsConfig` + 集成到 `STS2Config`
- `src/sts2_autotest/pytest_plugin/plugin.py` — 改动：`pytest_sessionfinish` 传参 + `pytest_configure` 注册回调
- `src/sts2_autotest/pytest_plugin/hooks.py` — 无改动（现有基础设施已满足需求）
- `tests/unit/test_notifier.py` — 新增：通知器单元测试
- `tests/unit/test_notifications_config.py` — 新增：配置单元测试
- `tests/unit/test_plugin.py` — 改动：通知回调注册测试
