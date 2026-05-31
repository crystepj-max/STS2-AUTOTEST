# B10 Level 2 修复建议 — 设计文档

> 类型：设计文档（Design Spec）
> 日期：2026-05-31
> 来源：beta-roadmap.md P3，"crash pack → patch.diff"
> 关联 Handoff：[2026-05-31-b10-repair-suggestions-handoff.md](./2026-05-31-b10-repair-suggestions-handoff.md)

## 1. 概述

当测试会话崩溃时，自动分析 crash evidence pack，生成结构化修复建议（`repair_suggestions.json`）。从 Level 1（"发生了什么" — `summary.json` + 截图 + 日志）升级到 Level 2（"为什么会发生 + 怎么修"）。

### 目标用户与消费方式

修复建议需要**双重消费者**：

1. **人类可读**：开发者/QA/游戏设计师查看崩溃包时，快速理解问题并定位责任人
2. **AI 可读**：AI Agent 解析 JSON 后，基于终端输出和分析报告自动修复缺陷

### 设计策略

**常规走安全路径，必要时深入**：

| 层级 | 数据源 | 触发 | 精度 |
|------|--------|------|------|
| L1 规则引擎 | `FailureInfo`（`summary.json` 已持久化数据） | 始终执行 | 泛化建议，confidence 0.35–0.60 |
| L2 堆栈解析 | `FailureInfo.stack_trace`（已持久化） | 始终执行 | 文件名+行号定位，confidence +0.2 |
| L3 按需重现 | 原始 Exception + GameState（一手数据） | 自动（置信度 < 0.5）+ 手动（CLI `autotest replay`） | 最高精度，confidence +0.2 |

L1+L2 是 MVP，L3 作为独立模块快速跟进。

## 2. 架构

### 2.1 模块位置

```
evidence | dsl | pytest_plugin | cli | config
    ↓
  core/                          ← repair_advisor 在核心层
  ├── orchestrator.py
  ├── recovery.py
  ├── repair_advisor.py          ← 新增：修复建议生成（L1+L2）
  ├── repair_replay.py           ← 新增：L3 按需重现（独立模块）
  ├── ...
    ↓
 adapters
    ↓
 common/
  ├── evidence.py                ← 新增 RepairSuggestion / RepairReport 数据模型
  ├── errors.py                  ← 已有 ErrorCategory（复用）
  ├── ...
```

**依赖方向：** `core/repair_advisor.py` → `common/evidence.py` + `common/errors.py`。严格遵守 `core/` → `common/` 的单向依赖。

### 2.2 调用链

```
EvidencePackager.create_pack()
    → summary.json 写入
    → _generate_report_for() → summary.md 写入
    → RepairAdvisor.analyze(summary) → repair_suggestions.json 写入  ← 新增（不改变现有流程）
```

仅在 `create_pack()` 末尾追加一步，不侵入崩溃恢复流程。

## 3. 数据模型

所有新增模型放于 `common/evidence.py`，frozen pydantic。

### 3.1 RepairSuggestion

```python
class RepairSuggestion(BaseModel):
    """单条修复建议"""
    model_config = ConfigDict(frozen=True)

    confidence: float              # 0.0–1.0
    category: str                  # code_fix | config_change | env_fix | investigation_needed
    title: str                     # 一句话标题（人类可读）
    description: str               # 详细说明（人类可读）
    source_location: str | None    # "文件:行号"，堆栈解析能定位就填，否则 null
    patch: str | None              # unified diff 字符串，能生成就填，否则 null
    related_docs: list[str]        # 相关文档链接，可为空
```

### 3.2 RepairReport

```python
class RepairReport(BaseModel):
    """一次分析的报告"""
    model_config = ConfigDict(frozen=True)

    crash_signature: str           # 来自 crash_signature() 的确定性签名
    suggestions: list[RepairSuggestion]
    generated_at: str              # ISO 8601 UTC
    source: str                    # "rule_engine" | "rule_engine+stack_trace" | "replay_capture"
    analysis_duration_ms: float
```

### 3.3 关键语义

| 字段 | 规则 |
|------|------|
| `confidence` | L1 规则引擎：0.35–0.60；L2 堆栈定位成功：+0.2（上限 0.80）；L3 重现捕获：+0.2（上限 0.95） |
| `category` | 4 种固定值：`code_fix`、`config_change`、`env_fix`、`investigation_needed` |
| `source_location` | `None` 表示"无法定位"——合法状态，不是错误 |
| `patch` | `None` 表示"无法生成 patch"或"不需要代码修改"——合法状态 |
| `suggestions` | 空列表合法——表示"分析完成但所有模式都未命中" |
| `source` | 标记生成方式，方便 debug 和 A/B 对比 |

### 3.4 SummaryJson 扩展

```python
class SummaryJson(BaseModel):
    # ... 现有字段不变 ...
    repair_report: RepairReport | None = None  # 新增：可选修复报告
```

`repair_report` 为 `None` 意味着分析未执行（如证据包生成时分析功能被禁用），与 `suggestions=[]`（分析执行了但无命中）语义不同。

## 4. L1 规则引擎 + L2 堆栈解析

### 4.1 L1 规则引擎

纯函数 `_match_rules(failure: FailureInfo) -> list[RepairSuggestion]`。规则表硬编码在 `core/repair_advisor.py` 内。

**MVP 规则表（6 条）：**

| # | 条件 | title | category | confidence |
|---|------|-------|----------|------------|
| 1 | `type == "crash_error"` | 游戏进程异常退出，检查最近修改的 C# 代码是否有未处理异常 | `code_fix` | 0.50 |
| 2 | `type == "adapter_error"` + message 含 `version_mismatch` | 适配器与游戏/BaseLib 版本不兼容，更新 BaseLib 或 CLI Mod | `config_change` | 0.60 |
| 3 | `type == "timeout_error"` | 操作超时，检查 Mod 初始化代码是否有死循环或资源阻塞 | `code_fix` | 0.45 |
| 4 | `type == "assertion_error"` | 状态转换断言失败 | `code_fix` | 0.55 |
| 5 | `type == "session_error"` | 会话级别错误，检查运行环境 | `env_fix` | 0.40 |
| 6 | `type == "game_error"` | 游戏内部错误，需进一步调查 | `investigation_needed` | 0.35 |

**规则 4 特殊处理：** 当 `FailureInfo.expected` 和 `FailureInfo.actual` 都有值时，在 `description` 中动态填入"预期 {expected}，实际 {actual}"。

**规则表策略：** MVP 硬编码，后续若规则增长到 20+ 条再考虑 YAML 配置文件外部化。

### 4.2 L2 堆栈解析

独立函数 `_parse_stack_trace(stack_trace: str) -> list[SourceLocation]`。纯正则，不依赖外部库。

- **Python traceback:** 匹配 `File "(.+?)", line (\d+)` → 提取文件路径 + 行号
- **C# stack trace:** 匹配 `at .+? in (.+?):line (\d+)` → 提取 `.cs` 文件路径 + 行号
- **未识别格式：** 返回空列表

**L1+L2 组合逻辑：** 遍历 L1 产出的 suggestion 列表，依次尝试匹配堆栈帧。第一个成功匹配的填入 `source_location`，confidence +0.2（上限 0.80）。

### 4.3 RepairAdvisor 类

```python
class RepairAdvisor:
    """修复建议生成器。L1+L2 必需，L3 可选。"""

    def __init__(self, *, enable_replay: bool = False):
        ...

    def analyze(self, summary: SummaryJson) -> RepairReport | None:
        """从 SummaryJson 生成修复报告。
        
        如果 summary.failure 为 None，返回 None。
        """
        ...

    @staticmethod
    def analyze_from_exception(
        exc: Exception,
        exit_code: int | None,
        game_state: dict | None,
    ) -> RepairReport:
        """从一手异常对象生成报告。供 L3 replay 使用。"""
        ...
```

## 5. L3 按需重现

### 5.1 触发方式

| 触发方式 | 场景 | 入口 |
|----------|------|------|
| **自动** | L1+L2 所有 suggestion confidence < 0.5，或 suggestions 为空 | `RepairAdvisor` 标记需要重现，由 orchestrator 流水线触发 |
| **手动** | 用户审阅 `repair_suggestions.json` 后，指定 pack_id 重跑 | CLI `autotest replay <pack_id>` |

配置项（默认关闭）：

```bash
STS2_EXECUTION__REPAIR_REPLAY_ENABLED=false   # 默认关闭
STS2_EXECUTION__REPAIR_REPLAY_TIMEOUT=120.0    # 重现超时
```

### 5.2 重现流程

独立模块 `core/repair_replay.py`，不在 MVP 中实现：

```python
class RepairReplay:
    """L3: 重放失败测试用例以捕获一手崩溃数据。"""

    def __init__(self, adapter_factory: Callable[[], GameAdapterProtocol]):
        self._adapter_factory = adapter_factory

    async def replay_and_analyze(
        self,
        pack_id: str,
        summary: SummaryJson,
        failure: FailureRecord,
    ) -> RepairReport:
        """重新执行失败操作，在崩溃点捕获完整异常。
        
        1. 创建新 adapter → 重放到失败步骤
        2. 在崩溃点捕获原始 exception + GameState
        3. 用一手数据调用 RepairAdvisor.analyze_from_exception()
        """
```

### 5.3 关键约束

| 约束 | 说明 |
|------|------|
| **默认关闭** | CLI 参数 `--enable-repair-replay` 或配置项 `repair_replay_enabled`。重现启动游戏，有成本 |
| **超时保护** | 重现最多尝试 1 次，超时 120s，超时则返回 L1+L2 报告 + 标记 `"replay_timeout"` |
| **不污染证据** | 重现产生的崩溃证据写入独立 pack（附加 `_replay` 后缀） |
| **失败降级** | 重现失败不抛异常，返回 L1+L2 报告 + `source: "rule_engine+stack_trace"`（降级） |

## 6. 证据包结构

B10 之后 `EvidencePackager` 生成的包：

```
pack_dir/
  summary.json                    ← 已有，新增 repair_report 字段（RepairReport 内嵌序列化）
  summary.md                      ← 已有（修复建议章节留待未来迭代，见第 9 节）
  screenshot.png                  ← 已有
  session.log                     ← 已有
  reports/
    junit.xml                     ← 已有
    scene-coverage.json           ← 已有
    scene-coverage.md             ← 已有
    repair_suggestions.json       ← 新增：RepairReport 的独立 JSON 文件，与 summary.json 内嵌字段内容相同
```

**`repair_suggestions.json` 与 `summary.json` 的关系：** `repair_suggestions.json` 是独立的可读文件（放在 `reports/`，方便 CI 和 AI Agent 直接读取），同时 `summary.json` 也内嵌一份 `RepairReport`（通过 `repair_report` 字段），便于以 pack 为单位整体加载。两份内容相同，一主一副——`summary.json` 是权威源。

## 7. 测试策略

### 7.1 单元测试（`tests/unit/core/test_repair_advisor.py`）

| 用例 | 预期 |
|------|------|
| `test_all_rules_match` — 6 种 ErrorCategory 各构造一个 FailureInfo | 每条规则返回 ≥1 个 suggestion，confidence 在预期范围 |
| `test_no_rules_match` — `type: "未知类型"` | suggestions 为空列表，report 正常生成 |
| `test_assertion_dynamic_description` — assertion_error + expected/actual 有值 | description 包含预期值和实际值 |
| `test_python_traceback_parsed` — stack_trace 含 `File "...", line N` | source_location 含文件名和行号 |
| `test_csharp_traceback_parsed` — stack_trace 含 `at ... in ....cs:line N` | source_location 非空 |
| `test_mixed_traceback` — 同时含 Python 和 C# 帧 | 取第一个匹配的帧 |
| `test_empty_stack_trace` — stack_trace 为空或 None | 所有 source_location 为 None |
| `test_confidence_below_threshold` — 所有 confidence < 0.5 | report 标记需要重现 |
| `test_report_roundtrip` — RepairReport model_dump → model_validate | 往返一致 |
| `test_summary_json_with_report` — SummaryJson + repair_report | 序列化/反序列化正常 |
| `test_analyze_from_exception` — 直接传 Exception 对象 | source="rule_engine" |

### 7.2 集成测试（`tests/integration/test_repair_advisor_integration.py`）

| 用例 | 预期 |
|------|------|
| `test_generates_repair_suggestions_json` — 真实 failed pack | `repair_suggestions.json` 存在且格式合法 |
| `test_missing_failure_is_noop` — summary.json 不含 failure | repair_report 为 None，不报错 |
| `test_cli_replay_command` — `autotest replay <pack_id>` | adapter 创建成功，重跑到失败点，生成新 report |

### 7.3 端到端（`tests/e2e/test_repair_e2e.py`）

| 用例 | 预期 |
|------|------|
| `test_full_crash_to_repair_flow` — 故意触发崩溃 | evidence pack 含 `repair_suggestions.json`，≥1 条 suggestion |

### 7.4 测试边界

- **不测试** 每条规则描述的具体措辞（会频繁调整）——只验证 suggestion 非空、字段类型合法
- **不测试** C# 编译器变体的堆栈格式兼容性——只测标准格式
- **Mock 策略** L1+L2 完全纯函数，无需 mock；L3 重现需要 mock adapter

## 8. 非功能约束

| 约束 | 说明 |
|------|------|
| **零外部依赖** | MVP 纯 Python stdlib，不引入 LLM SDK、机器学习库、OCR 库 |
| **确定性** | 相同输入 → 相同输出。不依赖随机性或外部网络 |
| **向后兼容** | `repair_suggestions.json` 缺失时，现有流程不受影响。`SummaryJson.repair_report` 默认 None |
| **不侵入恢复流程** | L1+L2 从已持久化的 `summary.json` 读取，在打包阶段执行。恢复路径（`recovery.py`）不感知 |
| **不是自动修复** | B10 是 Level 2 "建议"，不是 Level 3 "自动修复"。`patch` 字段仅供人类/AI 审查后手动 apply |
| **与 B8 无关** | B8 Visual QA 的 OCR/VLM 输出未来可作 B10 的输入信号，但 B10 MVP 不依赖 B8 |

## 9. 待确定（未来迭代）

| 项目 | 说明 |
|------|------|
| 规则表 YAML 外部化 | 当规则增长到 20+ 条时，从硬编码迁移到配置文件 |
| L3 重现实现 | `core/repair_replay.py`，依赖 adapter factory 注入 |
| B8 VLM 集成 | VLM 分析截图 + 日志 → 高精度建议，填充预留的 `source_location` + `patch` 字段 |
| `summary.md` 修复建议章节 | 人类可读 report 中新增修复建议部分 |
