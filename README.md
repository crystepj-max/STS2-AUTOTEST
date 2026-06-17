# STS2-AUTOTEST

《杀戮尖塔 2》（Slay the Spire 2）Mod 端到端自动化测试编排框架。

STS2-AUTOTEST 位于游戏控制工具（STS2-Cli-Mod、STS2-Agent）与测试执行之间，提供状态管理、动作编排、断言 DSL、证据采集、适配器抽象，以及自然语言测试规格流水线。

- Python `>=3.11`，主要运行平台 Windows 11，开发可在 macOS 上进行。
- `src/` layout，构建后端 `hatchling`，当前处于 Beta 阶段。

## 安装

```bash
pip install -e ".[dev]"
```

安装后提供 `autotest` 命令行工具，并自动注册 pytest 插件。

## 快速开始

```bash
# 1. 检查环境是否就绪（游戏、CLI、Steam 登录态等）
autotest doctor

# 2. 运行全部测试用例
autotest run --all

# 3. 查看最近一次运行的报告
autotest report
```

### 命令一览

| 命令 | 作用 |
|------|------|
| `autotest run` | 运行测试用例（支持 `--all`、`--resume`、`--ci` 等） |
| `autotest review` | 审查自然语言测试规格 |
| `autotest compile` | 将规格编译为 pytest 测试文件 |
| `autotest doctor` | 检查运行环境就绪情况（支持 `--json`、`--ci`） |
| `autotest report` | 查看测试运行摘要 |
| `autotest queue` | 管理本地测试队列 |
| `autotest progress` | 查看断点续跑进度 |
| `autotest agent-test` | 一键执行完整 Test Agent 工作流（构建 → 本地化检查 → 部署 → 冒烟 → 报告） |
| `autotest serve` | 启动健康检查 HTTP 端点（`/health`、`/health/live`、`/health/ready`） |
| `autotest serve-mcp` | 启动 MCP 测试服务 |
| `autotest gen-report` | 生成 HTML 测试报告 |

详细用法见 [docs/user-manual.md](docs/user-manual.md)。

## 适配器

两种游戏控制适配器，互斥启用（通过 `--adapter` 或 `STS2_ADAPTER__*` 环境变量选择）：

- **CliModAdapter**（默认）：通过 `sts2` CLI 子进程驱动游戏。
- **AgentAdapter**（Beta）：通过 HTTP / MCP 接入 STS2-Agent。

配置见 [.env.example](.env.example)。

## 自然语言测试流水线

Markdown 规格 → 审查 → 修订稿 → 代码生成 → pytest 执行：

```bash
autotest run --all   # 自动走 review → compile → pytest 流水线
```

## 测试

```bash
# 单元测试（纯逻辑 + mock，不依赖真实游戏）
python -m pytest tests/unit/ -q

# 集成测试（需要 STS2-Cli-Mod CLI 环境，部分需游戏运行）
python -m pytest tests/integration/ -q

# 类型检查（src/ 强制 strict）
mypy src/sts2_autotest --strict

# 层级隔离检查
lint-imports
```

集成测试在无游戏环境时会自动跳过（标记 `@pytest.mark.requires_game`），而不是失败。若 `sts2` CLI 不在 `PATH`，设置 `STS2_CLI_PATH` 指向可执行文件。

## 文档

- 用户手册：[docs/user-manual.md](docs/user-manual.md)
- 路线图：[docs/beta-roadmap.md](docs/beta-roadmap.md)
- 项目说明与开发约定：[CLAUDE.md](CLAUDE.md)
- STS2-Cli-Mod CLI 参考：[docs/sts2-cli-mod-reference.md](docs/sts2-cli-mod-reference.md)
- 自然语言测试：[docs/natural-language-testing/](docs/natural-language-testing/)

## 许可

见 [LICENSE](LICENSE)。
