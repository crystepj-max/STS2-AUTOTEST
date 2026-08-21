# STS2-Agent Adapter 使用指南

## 概述

AgentAdapter 通过 HTTP API 对接 STS2-Agent，提供异步原生游戏控制，
支持多人游戏、元数据查询和 MCP tool profile 切换。

## 前置条件

- STS2-Agent 已安装并运行（默认 `http://127.0.0.1:8080`）
- STS2-Agent 与游戏版本兼容

## 配置方式

### 方式一：YAML 配置文件

```yaml
adapter:
  cli:
    enabled: false
  agent:
    enabled: true
    endpoint: "http://127.0.0.1:8080"
    timeout: 30
    tool_profile: "guided"   # guided | layered | full
    debug_actions: false
```

### 方式二：环境变量

```bash
export STS2_ADAPTER__AGENT__ENABLED=true
export STS2_ADAPTER__CLI__ENABLED=false
export STS2_ADAPTER__AGENT__ENDPOINT=http://127.0.0.1:8080
export STS2_ADAPTER__AGENT__TIMEOUT=30
# 需要 give_card / set_seed / set_hp / win_combat 等调试动作时显式开启：
export STS2_ADAPTER__AGENT__DEBUG_ACTIONS=true
```

### 方式三：CLI 参数

```bash
autotest run --all --adapter agent
```

## 适配器切换

CLI 与 Agent 适配器互斥，不可同时启用。
通过 `--adapter` 参数可临时覆盖配置：

```bash
# 使用 Agent 适配器（即使配置为 CLI）
autotest run --all --adapter agent

# 使用 CLI 适配器（即使配置为 Agent）
autotest run --all --adapter cli
```

## Tool Profile

| Profile | 说明 | 适用场景 |
|---------|------|---------|
| guided  | 高层动作 + 最强护栏（默认） | 常规测试执行 |
| layered | 多层 agent 控制 | 复杂场景编排 |
| full    | 原始工具访问 | 调试/兼容性回归 |

## Capabilities

| 能力 | AgentAdapter | CliModAdapter |
|------|-------------|--------------|
| 单机控制 | ✅ | ✅ |
| 多人控制 | ✅ | ❌ |
| 元数据查询 | ✅ | ❌ |
| Debug Actions | 默认关闭 | ❌ |
| 异步原生 | ✅ (httpx) | ❌ (asyncio.to_thread) |

### Debug Actions 与 give_card 能力门闸

`give_card`（以及 `set_seed` / `set_hp` / `give_block` / `win_combat` / `enable_travel`）
依赖适配器调试能力：

- AgentAdapter 仅在 `debug_actions=True`（`STS2_ADAPTER__AGENT__DEBUG_ACTIONS=true`）时暴露并支持这些动作；
- CliModAdapter 走 `sts2` CLI 子进程，无调试控制台通道，不支持 `give_card`。

框架在动作下发前做能力门闸：无 `supports_debug_actions` 的适配器上执行 `give_card` 会
以可机读原因 `DEBUG_ACTIONS_UNAVAILABLE` 明确失败/阻断，而不会把 `give_card` 打到不存在的
CLI 子命令上（避免「未识别命令 give_card」）。含 `give_card` 的用例（如
TC-IRONCLAD-TWIN-STRIKE-DAMAGE）应以 Agent + debug 为权威运行路径。
