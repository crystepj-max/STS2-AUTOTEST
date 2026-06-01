# B11 CI/CD 流水线 — 实施 Handoff

> 类型：handoff 文档
> 日期：2026-06-01
> 来源：`docs/superpowers/plans/2026-05-31-b11-cicd-implementation.md`
> 分支：`feat/b11-cicd-pipeline`
> 状态：实施完成，待审查

---

## 概述

完成了 B11 CI/CD 流水线的全部 8 个 Task，涵盖 Phase 1（框架自检 CI）和 Phase 2（MCP 测试服务）。

**10 个 commits，12 个文件变更，+1812 / -63 行。**

---

## Phase 1 产出（CI Workflow 文件）

### Task 1: Runner 配置脚本
- **文件**：[scripts/setup-mac-runner.sh](scripts/setup-mac-runner.sh)（新增，107 行）
- **功能**：一键配置 macOS 自托管 GitHub Actions Runner
  - 检查 gh CLI + 认证
  - 下载 GitHub Actions runner v2.322.0 ARM64
  - 配置标签 `self-hosted,macos,autotest`
  - 写入环境变量到 `.env`
  - 安装 launchd plist（开机自启）
- **审查修复**：heredoc 引用修复（`'EOF'` → `EOF`），launchctl 幂等性

### Task 2: PR 触发 Workflow
- **文件**：[.github/workflows/ci-pr.yml](.github/workflows/ci-pr.yml)（新增，137 行）
- **功能**：PR → main 时触发
  - 阶段 1：lint ‖ mypy ‖ lint-imports（三平台并行矩阵）
  - 阶段 2：unit-test（依赖阶段 1）
  - 阶段 3：cli-integration（Mac only）
  - 阶段 4：summary（Job Summary 表格）
- **审查修复**：summary job `${{ needs.$job.result }}` 改为硬编码引用；添加 `timeout-minutes`

### Task 3: Push to Main Workflow
- **文件**：[.github/workflows/ci-main.yml](.github/workflows/ci-main.yml)（新增，107 行）
- **功能**：push → main 时触发
  - quick-checks（矩阵执行 lint/mypy/lint-imports/unit-test）
  - cli-integration（依赖 quick-checks）
  - deploy-gawain（dotnet publish + 复制到 STS2Mods 目录）
  - summary

### Task 4: 游戏测试 + 每夜 Workflow
- **文件**：
  - [.github/workflows/ci-game.yml](.github/workflows/ci-game.yml)（新增，54 行）— workflow_dispatch，60min 超时
  - [.github/workflows/ci-nightly.yml](.github/workflows/ci-nightly.yml)（新增，65 行）— cron UTC 03:00 + 手动，360min 超时，7 天证据保留

---

## Phase 2 产出（MCP 测试服务）

### Task 5: health_server.py 重构
- **文件**：[src/sts2_autotest/cli/health_server.py](src/sts2_autotest/cli/health_server.py)（修改）
- **变更**：
  - 新增 `_HTML_405` 常量
  - `_parse_request()` → `_parse_http_request()`（返回 method, path, headers, body）
  - 新增 `_HttpServer` 基类（`handle_request()`, `start()`, `stop()`, `_handle_client()`）
  - `HealthServer` 继承 `_HttpServer`
  - 向后兼容：`run_server()` 和 `serve_cmd()` 签名不变
- **验证**：所有 1193 个现有测试通过，mypy strict 零错误

### Task 6: MCP 协议层
- **文件**：
  - [src/sts2_autotest/cli/mcp_protocol.py](src/sts2_autotest/cli/mcp_protocol.py)（新增，173 行）
  - [tests/unit/test_mcp_protocol.py](tests/unit/test_mcp_protocol.py)（新增，11 个测试）
- **内容**：JSON-RPC 2.0 编解码
  - `McpError`（Exception + `to_dict()`）、`McpRequest`、`McpResponse`、`McpTool`、`McpServerInfo`
  - `decode_request()`, `encode_response()`, `make_error_response()`, `make_initialize_response()`
  - 5 个标准错误码常量
- **设计决策**：`McpError` 继承 `Exception` 而非 `@dataclass`，确保可与 `pytest.raises()` 协作

### Task 7: MCP 工具层
- **文件**：
  - [src/sts2_autotest/cli/mcp_tools.py](src/sts2_autotest/cli/mcp_tools.py)（新增，454 行）
  - [tests/unit/test_mcp_tools.py](tests/unit/test_mcp_tools.py)（新增，10 个测试）
- **内容**：7 个 MCP 工具
  1. `health_check` — 服务健康检查
  2. `review_spec` — B25 审查阶段（调用 `spec_reviewer`）
  3. `compile_spec` — B25 编译阶段（调用 `code_generator`）
  4. `run_test` — B25 运行阶段（子进程调用 pytest）
  5. `run_pipeline` — 一键流水线（review → compile → run）
  6. `get_report` — 获取历史报告
  7. `list_specs` — 列出可用测试规格
- **关键设计**：
  - 包装函数模式（`review_spec_file` 等在模块级别定义，测试时可 mock）
  - `ToolRegistry` 类提供 `list_tools()` / `dispatch()`
  - 路径白名单验证（`STS2_MCP_PATH_WHITELIST`）
  - 延迟导入 core 模块（符合 import-linter 层级规则）

### Task 8: MCP Server 入口
- **文件**：
  - [src/sts2_autotest/cli/mcp_server.py](src/sts2_autotest/cli/mcp_server.py)（新增，176 行）
  - [tests/unit/test_mcp_server.py](tests/unit/test_mcp_server.py)（新增，11 个测试）
- **内容**：
  - `McpServer` 继承 `_HttpServer`
  - 路由：`GET /health`（健康检查），`POST /mcp`（JSON-RPC 2.0）
  - MCP 方法：`initialize`、`tools/list`、`tools/call`
  - Token 认证（`X-MCP-Token`）+ 环境变量 `STS2_MCP_TOKEN`
  - CLI 入口 `serve_cmd()` + argparse `--host`/`--port`

---

## 验证结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| mypy strict | ✅ | 4 个新文件零错误 |
| lint-imports | ✅ | 层级合约保持（cli 导入 core/common，未违反隔离规则）|
| MCP 协议测试 | ✅ | 11/11 PASS |
| MCP 工具测试 | ✅ | 10/10 PASS |
| MCP Server 测试 | ✅ | 11/11 PASS |
| 现有测试回归 | ✅ | 1193 个测试通过（与基线一致）|
| YAML 语法验证 | ✅ | 4 个 workflow 文件全部有效 |
| Bash 语法验证 | ✅ | `bash -n setup-mac-runner.sh` 静默通过 |

---

## 架构合规

新增代码遵循项目层级隔离规则（`.importlinter`）：

```
mcp_server.py ──→ _HttpServer (health_server.py)
     │                ↓
     ├──→ mcp_protocol.py  (纯 stdlib，无项目依赖)
     │
     └──→ mcp_tools.py ──→ core/spec_reviewer.py
                           core/code_generator.py
                           core/markdown_parser.py
```

所有跨包导入单向向下（`cli/` → `core/` → `adapters/` → `common/`）。

---

## 已知限制

1. **Runner 离线无自动 fallback**：GitHub Actions 不原生支持。自托管 job 离线时会排队；matrix 中的 GitHub-hosted job 继续正常运行。建议将自托管 job 设为 `allow-failure`。
2. **MCP launchd plist 未自动生成**：Task 8 提供 CLI 入口，但 plist 文件写入需手动操作。
3. **`state.py` 存在预先的 `GameScreen` 前向引用问题**：缺少 `from __future__ import annotations`，导致 `frozenset[GameScreen]` 在类定义完成前解析失败。此问题与 B11 无关，属于预先存在的 codebase 问题。
4. **`python3.14` 环境下 pytest 不可用**：需使用 `python3.11` 执行测试。

---

## 审查要点

请审查以下方面：

1. **CI Workflow 正确性**：4 个 `.yml` 文件的触发条件、job 依赖链、环境变量引用是否正确？
2. **MCP 协议合规**：`mcp_protocol.py` 是否严格遵循 JSON-RPC 2.0 和 MCP 2024-11-05 规范？
3. **安全模型**：
   - Token 认证是否正确实现（`_check_token`）？
   - 路径白名单是否有效防止目录遍历？
   - 默认 `127.0.0.1` 绑定是否正确？
4. **错误处理**：MCP 错误码使用是否一致？异常是否被正确捕获和转换？
5. **向后兼容**：`health_server.py` 重构是否保持 `run_server()` / `serve_cmd()` 签名不变？
6. **测试覆盖**：32 个新增测试是否充分覆盖关键路径？

---

## 部署步骤（审查通过后）

1. **合并分支**：`feat/b11-cicd-pipeline` → `main`
2. **配置 Mac Runner**：`./scripts/setup-mac-runner.sh`
3. **验证 CI**：向 main 提交 PR 观察 ci-pr.yml 触发
4. **启动 MCP Server**：
   ```bash
   python3.11 -m sts2_autotest.cli.mcp_server --port 8090 &
   curl http://127.0.0.1:8090/health
   ```
5. **配置 MCP Token**（生产环境）：`export STS2_MCP_TOKEN=<shared-secret>`
6. **安装 launchd plist**（可选，开机自启）
