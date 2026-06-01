# B10 Level 2 修复建议 — Review Handoff

> 类型：代码审查 Handoff
> 日期：2026-05-31
> 实现完成，待 Review

---

## 功能概述

当测试会话崩溃时，自动分析 crash evidence pack 并生成结构化修复建议（`repair_suggestions.json`）。从 Level 1（"发生了什么"）升级到 Level 2（"为什么会发生 + 怎么修"）。

## 架构

```
EvidencePackager.create_pack()
    → summary.json 写入
    → _generate_report_for() → summary.md 写入
    → RepairAdvisor.analyze(summary) → repair_suggestions.json 写入  ← B10 新增
```

**L1 规则引擎**：6 条硬编码规则，匹配 `ErrorCategory` → 预定义建议（confidence 0.35–0.60）

**L2 堆栈解析**：正则提取 Python/C# stack trace 中的 `file:line`，填充 `source_location`，confidence +0.2（上限 0.80）

**L3 按需重现**（未实现，预留接口）：`RepairAdvisor.analyze_from_exception()` 静态方法

## 变更清单

| 操作 | 文件 | 行数 | 说明 |
|------|------|------|------|
| Modify | `src/sts2_autotest/common/evidence.py` | +37 | `RepairSuggestion`、`RepairReport` 模型；`SummaryJson.repair_report` 字段 |
| **Create** | `src/sts2_autotest/core/repair_advisor.py` | 278 | L1 规则引擎 + L2 堆栈解析 + `RepairAdvisor` 类 |
| Modify | `src/sts2_autotest/evidence/packager.py` | +19 | `create_pack()` 中调用 `RepairAdvisor.analyze()`（try/except 包裹） |
| Modify | `tests/unit/test_common_evidence.py` | +211 | 3 个新测试类（12 tests） |
| **Create** | `tests/unit/test_repair_advisor.py` | 336 | 5 个测试类（31 tests） |
| Modify | `tests/unit/test_packager.py` | +37 | `TestPackagerB10`（2 tests） |
| **Create** | `tests/integration/test_repair_advisor_integration.py` | 120 | 3 个集成测试 |

## 关键设计决策

### 数据模型（`common/evidence.py`）

```python
class RepairSuggestion(BaseModel):
    model_config = ConfigDict(frozen=True)
    confidence: float = Field(ge=0.0, le=1.0)
    category: Literal["code_fix", "config_change", "env_fix", "investigation_needed"]
    title: str
    description: str
    source_location: str | None = None    # "文件:行号" or None
    patch: str | None = None              # unified diff or None
    related_docs: list[str] = []

class RepairReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    crash_signature: str
    suggestions: list[RepairSuggestion]
    generated_at: str                     # ISO 8601 UTC
    source: str                           # "rule_engine" | "rule_engine+stack_trace" | "replay_capture"
    analysis_duration_ms: float
```

- `repair_report: RepairReport | None = None` 已添加到 `SummaryJson`（`artifact_path` 之后）
- `repair_report is None` ≠ `suggestions=[]`：前者=分析未执行，后者=分析执行但无命中

### L1 规则表（6 条，`_match_rules()`）

| # | 条件 | category | confidence |
|---|------|----------|------------|
| 1 | `type == "crash_error"` | code_fix | 0.50 |
| 2 | `type == "adapter_error"` + message 含 version_mismatch/版本 | config_change | 0.60 |
| 3 | `type == "timeout_error"` | code_fix | 0.45 |
| 4 | `type == "assertion_error"` | code_fix | 0.55 |
| 5 | `type == "session_error"` | env_fix | 0.40 |
| 6 | `type == "game_error"` | investigation_needed | 0.35 |

### L2 堆栈解析（`_parse_stack_trace()`）

- Python: `File "(.+?)", line (\d+)`
- C#: `at .+? in (.+?):line (\d+)`
- 两个正则合并结果，Python 帧优先

### 集成策略

- `create_pack()` 末尾 try/except 包裹——修复建议生成失败**绝不阻塞**证据包创建
- 两个输出：
  - `reports/repair_suggestions.json`（独立文件，CI/AI Agent 消费）
  - `summary.json` 内嵌 `repair_report` 字段（同一内容，pack 级加载）

## 与计划的偏离

1. **`analyze_from_exception()` fallback 逻辑**（`repair_advisor.py:253-259`）：当原始 Python 异常（如 `RuntimeError`、`ValueError`）无法匹配任何 L1 规则时，生成一个 `confidence=0.25` 的通用调查建议。原计划中此情况会返回空 `suggestions`，导致 L2 堆栈位置无附加对象。**这是对设计缺陷的修复，请 Review 确认是否合理。**

2. **`game_state` 类型修正**：`analyze_from_exception()` 的 `game_state` 参数类型从 `dict | None` 改为 `dict[str, object] | None`（mypy strict 要求泛型参数）。

3. **测试数量增加**：计划 25 个单元测试，实际 31 个（增加了 `analyze_from_exception` 的边界用例覆盖）。

4. **未创建临时文件**：计划中 Task 3 Step 3 提到创建 `tests/unit/test_packager_b10.py` 再合并删除。实际直接将 B10 smoke tests 追加到 `tests/unit/test_packager.py` 末尾，避免了临时文件。

## Review 重点

### 必查项

- [ ] `common/evidence.py`：新模型是否符合 pydantic frozen 规范？`Field` 约束和 `Literal` 类型是否正确？
- [ ] `core/repair_advisor.py`：6 条规则的条件和 confidence 值是否匹配设计文档？
- [ ] `core/repair_advisor.py:253-259`：fallback 建议逻辑是否合理？confidence 0.25 是否合适？
- [ ] `evidence/packager.py:145-160`：try/except 是否正确包裹？失败时是否只 log warning 不抛异常？
- [ ] `core/repair_advisor.py` → `common/evidence.py` + `common/errors.py`：依赖方向是否合法？（`core/` → `common/` ✅）
- [ ] 类型注解完整性：所有公共 API 是否有类型注解？
- [ ] 测试覆盖：31 + 12 + 2 + 3 = 48 个新增测试是否全部通过？

### 建议查

- [ ] `RepairSuggestion` 的 `category` 用 `Literal` 后，`_match_rules()` 中的字符串字面量是否被 mypy 接受？
- [ ] `_enrich_with_stack_locations()` 的 confidence cap 0.80 逻辑是否正确？
- [ ] `AnalyzeFromException` 测试中 `inner()` 嵌套函数能否在 CI 的 Python 3.11–3.14 全版本通过？
- [ ] `repair_suggestions.json` 和 `summary.json` 内嵌 `repair_report` 内容是否一致？

## 测试命令

```bash
# B10 单元测试
python -m pytest tests/unit/test_repair_advisor.py tests/unit/test_common_evidence.py -v

# B10 packager smoke tests
python -m pytest tests/unit/test_packager.py::TestPackagerB10 -v

# B10 集成测试
python -m pytest tests/integration/test_repair_advisor_integration.py -v

# 全量单元测试
python -m pytest tests/unit/ -v

# 类型检查（仅 B10 文件）
mypy src/sts2_autotest/core/repair_advisor.py src/sts2_autotest/common/evidence.py src/sts2_autotest/evidence/packager.py --strict

# 导入隔离
lint-imports
```

## 相关文件

- 设计文档：`docs/superpowers/specs/2026-05-31-b10-repair-suggestions-design.md`
- Handoff：`docs/superpowers/specs/2026-05-31-b10-repair-suggestions-handoff.md`
- 实现计划：`docs/superpowers/plans/2026-05-31-b10-repair-suggestions.md`
- 路线图：`docs/beta-roadmap.md`（P3，B10 "crash pack → patch.diff"）
