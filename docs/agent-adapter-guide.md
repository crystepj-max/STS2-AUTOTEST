# STS2-Agent Adapter 使用指南

## 概述

AgentAdapter 通过 HTTP API 对接 STS2-Agent，提供异步原生游戏控制，
支持多人游戏、元数据查询和 MCP tool profile 切换。

## 前置条件

- STS2-Agent 已安装并运行（默认 `http://localhost:8080`）
- STS2-Agent 与游戏版本兼容

## 配置方式

### 方式一：YAML 配置文件

```yaml
adapter:
  cli:
    enabled: false
  agent:
    enabled: true
    endpoint: "http://localhost:8080"
    timeout: 30
    tool_profile: "guided"   # guided | layered | full
    debug_actions: false
```

### 方式二：环境变量

```bash
export STS2_AGENT_ENABLED=true
export STS2_CLI_ENABLED=false
export STS2_AGENT_ENDPOINT=http://localhost:8080
export STS2_AGENT_TIMEOUT=30
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
