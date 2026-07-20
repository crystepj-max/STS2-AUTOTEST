# B11 CI/CD 流水线 — 设计方案

> 类型：设计规格（brainstorming 产出）
> 日期：2026-05-31
> 来源：beta-roadmap.md P3
> 状态：设计已确认，待 writing-plans

---

## 1. 目标

为 STS2-AUTOTEST 搭建 GitHub Actions CI/CD 流水线，实现两项核心能力：

1. **框架自检**：代码检查 → 单元测试 → 集成测试 → 部署，全自动化
2. **MCP 服务**：通过常驻 MCP Server 对外暴露测试能力，MOD 项目无需升级依赖即可调用

跨平台策略：**Mac 自托管 Runner 全流程 + Windows GitHub-hosted Runner 仅非游戏测试**。

---

## 2. 架构概览

```
Phase 1 ──────────────────────────  Phase 2 ──────────────────────
框架自检 CI 流水线                   常驻 MCP 测试服务
                                    
ci-pr.yml (PR→main)                MCP Server (launchd 守护)
  lint ‖ mypy ‖ lint-imports         ↓ 暴露 6 个工具 + 1 个流水线工具
  → unit test                        ↓ JSON-RPC 2.0 over HTTP
  → CLI integration test             ↓ Token 认证 + 路径白名单

ci-main.yml (push→main)            MOD 项目 (Gawain / 其他)
  ci-pr 全部 + Mod 部署              ↓ 通过 MCP client 调用
                                     ↓ AUTOTEST 升级 → MOD 零改动
ci-game.yml (workflow_dispatch)
  requires_game 游戏集成测试

ci-nightly.yml (schedule cron)
  全量回归 + 证据打包
```

---

## 3. Phase 1：框架自检 CI 流水线

### 3.1 Workflow 文件

| 文件 | 触发 | Job 链 | Runner |
|------|------|--------|--------|
| `ci-pr.yml` | `pull_request → main` | lint ‖ (mypy + lint-imports) → unit test → CLI integration | Mac self + GitHub windows-latest |
| `ci-main.yml` | `push → main` | ci-pr 全部 + deploy (dotnet publish Gawain) | Mac self only |
| `ci-game.yml` | `workflow_dispatch` | requires_game 集成测试 | Mac self only |
| `ci-nightly.yml` | `schedule` (cron) | 全量回归 + 4h 长跑 + 证据打包 + 清理 | Mac self only |

### 3.2 PR 流水线并行策略

```
lint ────┐
mypy ────┼──→ unit test ──→ CLI integration test
lint-imports ┘
```

前三项并行执行，全部通过后运行单元测试，最后跑 CLI 集成测试。Windows Runner 在 CLI integration 阶段跳过（无 CLI 环境）。

### 3.3 双平台分工

**Mac 自托管 Runner**（标签 `self-hosted, macos, autotest`）：
- lint / mypy / lint-imports
- 单元测试 (pytest)
- CLI-only 集成测试
- requires_game 游戏集成测试（手动触发 + 每夜）
- Mod 部署（push to main）
- 证据打包

**GitHub-hosted `windows-latest`**：
- lint / mypy / lint-imports
- 单元测试
- 不跑 CLI 集成测试、requires_game、部署

### 3.4 PR 反馈机制

**Job Summary**（`$GITHUB_STEP_SUMMARY`）：
- Markdown 格式摘要，渲染在 PR Checks 面板
- 包含：通过/失败统计、测试时长、JUnit XML 链接、证据包链接

**Checks Annotations**（`::error` / `::warning` 命令）：
- lint/mypy/lint-imports 的违反项直接标记到 PR Files Changed 对应行
- 每次请求限制 10 条 annotation

### 3.5 Mod 部署步骤

`ci-main.yml` 在测试全部通过后，执行 Gawain 构建部署：

```yaml
- name: Build and deploy Gawain
  run: |
    cd ${{ env.STS2_WORKSPACE }}/STS2-GAWAIN
    dotnet publish Gawain.csproj
    cp -r Gawain/bin/Release/net9.0/publish/* ${{ env.STS2_MODS_DIR }}/
```

部署仅发生在 push to main，PR 不触发部署。

---

## 4. Phase 2：常驻 MCP 测试服务

### 4.1 协议

MCP (Model Context Protocol)，JSON-RPC 2.0 over HTTP。端点：`POST /mcp`。

服务端实现基于现有 `health_server.py` 的 asyncio 传输层（纯 stdlib，无 web 框架）。

### 4.2 MCP Tools

| Tool | B25 节点 | 输入 | 返回 | 映射模块 |
|------|----------|------|------|----------|
| `health_check` | — | 无 | `{status, adapter, game_connected}` | 现有 /health 逻辑 |
| `review_spec` | review | `spec_path` (必填) | `{issues[], revised_draft}` | `spec_reviewer.py` |
| `compile_spec` | compile | `spec_path` (必填), `output_dir` (可选) | `{generated_file, warnings[]}` | `code_generator.py` |
| `run_test` | run | `spec_dir` (必填), `suite` (可选), `timeout` (可选, 默认 60s) | `{run_id, passed, failed, duration_ms, junit_xml_url}` | `Orchestrator` |
| `run_pipeline` | review→compile→run | `spec_dir` (必填), `stages` (可选) | `{review_issues[], compiled_files[], test_result}` | 编排上述三个模块，等价 CLI `autotest run --all` |
| `get_report` | — | `run_id` | `{summary, failures[], evidence_pack_url}` | 证据目录 |
| `list_specs` | — | `spec_dir` (可选) | `{specs: [{name, path, type}]}` | 文件系统扫描 |

### 4.3 服务端架构

三层结构，复用现有 B25 模块：

```
mcp_server.py     (传输层) → asyncio TCP Server, HTTP + JSON-RPC 双端点, Token 校验
     ↓
mcp_protocol.py   (协议层) → JSON-RPC 2.0 编解码, tools/list, tools/call, initialize
     ↓
mcp_tools.py      (工具层) → 6 个 tool 实现, 映射到 spec_reviewer / code_generator / Orchestrator
```

### 4.4 部署方式

**运行方式**：macOS `launchd` 守护进程，`KeepAlive=true` 崩溃自动重启。

**配置**：
```xml
<!-- ~/Library/LaunchAgents/com.sts2.autotest-mcp.plist -->
<key>KeepAlive</key><true/>
<key>ProgramArguments</key>
<array>
  <string>/path/to/python</string>
  <string>-m</string>
  <string>sts2_autotest.cli.mcp_server</string>
  <string>--host</string><string>127.0.0.1</string>
  <string>--port</string><string>8090</string>
</array>
```

### 4.5 安全模型

三层防护：

1. **网络隔离**：默认 bind `127.0.0.1`，仅本机可访问。远程 MOD CI 通过 SSH tunnel 或 VPN。
2. **共享密钥**：HTTP Header `X-MCP-Token`，环境变量 `STS2_MCP_TOKEN`。MOD CI 通过 GitHub Secrets 注入。
3. **路径白名单**：`spec_path` / `spec_dir` 必须在预配置的工作区路径内，拒绝 `../` 遍历攻击。

### 4.6 MOD 接入方式

**方式 1：离散单步调用**（精细控制）：
```
review_spec → 检查 issue 数量 → compile_spec → run_test → get_report
```

**方式 2：一键流水线**（等价 `autotest run --all`）：
```
run_pipeline(spec_dir, stages=["review","compile","run"])
```

MOD CI 示例（Gawain `.github/workflows/test.yml`）：
```yaml
- name: Run tests via AUTOTEST MCP
  env:
    MCP_TOKEN: ${{ secrets.AUTOTEST_MCP_TOKEN }}
  run: |
    curl -X POST http://<autotest-host>:8090/mcp \
      -H "X-MCP-Token: $MCP_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
           "params":{"name":"run_pipeline",
                     "arguments":{"spec_dir":"tests/cases/"}}}'
```

**AUTOTEST 升级 0.1.0 → 0.2.0 → 1.0.0，Gawain 这段配置完全不变。**

---

## 5. 文件变更清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `.github/workflows/ci-pr.yml` | 新增 | PR 触发：lint ‖ mypy ‖ lint-imports → unit → CLI integration |
| `.github/workflows/ci-main.yml` | 新增 | Push main：ci-pr 全部 + Mod 部署 |
| `.github/workflows/ci-game.yml` | 新增 | workflow_dispatch：requires_game 测试 |
| `.github/workflows/ci-nightly.yml` | 新增 | Cron：全量回归 + 证据打包 + 清理 |
| `scripts/setup-mac-runner.sh` | 新增 | Mac 自托管 Runner 一键配置（安装 gh actions runner + launchd plist） |
| `src/sts2_autotest/cli/health_server.py` | 重构 | 提取传输层为可复用基类，供 MCP server 继承 |
| `src/sts2_autotest/cli/mcp_server.py` | 新增 | MCP Server 主程序（launchd 守护进程入口） |
| `src/sts2_autotest/cli/mcp_protocol.py` | 新增 | JSON-RPC 2.0 编解码 + MCP 协议实现 |
| `src/sts2_autotest/cli/mcp_tools.py` | 新增 | 6 个 MCP 工具实现 + `run_pipeline` 编排 |

### 代码边界

遵循现有层级隔离规则：
- `mcp_server.py` / `mcp_protocol.py` / `mcp_tools.py` 位于 `cli/` 包内
- `mcp_server.py` 仅导入 `mcp_protocol.py`、`mcp_tools.py`、`common/`
- `mcp_protocol.py` 仅导入 `common/`
- `mcp_tools.py` 导入 `core/` 的 Orchestrator、spec_reviewer、code_generator（单向向下，符合 import-linter）

### 测试

- `tests/unit/test_mcp_protocol.py`：JSON-RPC 编解码、tools/list、tools/call、错误响应格式
- `tests/unit/test_mcp_tools.py`：6 个工具的 mock 测试
- `tests/unit/test_mcp_server.py`：HTTP 请求路由、Token 校验、路径白名单
- 新增 CI workflow 本身无法单元测试，需通过实际 PR/push 触发验证

---

## 6. 错误处理与恢复

### 6.1 Runner 离线

**现象**：Mac 关机/断网时，自托管 Runner 不可达。
**策略**：PR 上 CI 降级——lint/mypy/unit test 自动 fallback 到 GitHub-hosted Runner。push to main 的部署步骤失败时 workflow 标记为 failed，通知维护者。

### 6.2 MCP Server 崩溃

**现象**：进程异常退出。
**策略**：launchd `KeepAlive=true` 自动重启。MOD CI 侧添加 3 次重试 + 指数退避（1s → 2s → 4s）。MCP Server 启动时执行自检。

### 6.3 测试超时

**现象**：游戏测试因崩溃挂起。
**策略**：所有 test job 设置 `timeout-minutes=15`。超时后标记 failed + 上传已收集的部分证据。

### 6.4 磁盘空间不足

**现象**：每夜构建证据包累积。
**策略**：CI job 中调用现有 DiskGuard 检查。证据包保留期限为 7 天，`ci-nightly.yml` 末尾自动清理旧包。

---

## 7. 触发矩阵

| 事件 | lint | mypy | lint-imports | unit | CLI integ | game integ | deploy |
|------|------|------|-------------|------|-----------|-----------|--------|
| PR → main | ✅ M+W | ✅ M+W | ✅ M+W | ✅ M+W | ✅ M | — | — |
| Push main | ✅ M | ✅ M | ✅ M | ✅ M | ✅ M | — | ✅ M |
| workflow_dispatch | — | — | — | — | — | ✅ M | — |
| schedule (cron) | ✅ M | ✅ M | ✅ M | ✅ M | ✅ M | ✅ M | — |

> M = Mac 自托管 Runner，W = GitHub-hosted windows-latest

---

## 8. 与现有功能的集成

| 现有功能 | 集成方式 |
|----------|----------|
| `--ci` flag (cli/main.py:82) | CI workflow 中调用 `autotest doctor --ci` 和 `autotest run --ci` |
| JUnit XML (evidence/packager.py:519) | CI workflow 中 `autotest report --evidence-dir ...` 生成后上传 artifact |
| Health HTTP 端点 (cli/health_server.py) | 传输层重构，MCP server 继承同一基类 |
| spec_reviewer (core/) | `review_spec` MCP 工具直接调用 |
| code_generator (core/) | `compile_spec` MCP 工具直接调用 |
| Orchestrator (core/) | `run_test` MCP 工具直接调用 |
| DiskGuard (core/) | `ci-nightly.yml` 末尾和每夜前调用 |

---

## 9. 不纳入范围

以下内容明确排除在 B11 之外：

- **B13 桌面通知**：独立 Story，P3
- **PyPI 公开发布**：当前仅内部使用，不发布到公共 PyPI
- **Windows 游戏测试自动化**：Windows Steam 自动登录风险较高，后续再考虑
- **多 MOD 并发 MCP 请求的队列/调度**：Phase 2 初期仅单请求处理，后续按需加队列
- **MCP 服务对外网暴露**：仅本机 127.0.0.1，不做公网穿透
