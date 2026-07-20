# B10 Level 2 修复建议 — Session Handoff

> 类型：设计前 handoff（brainstorming 尚未完成）
> 日期：2026-05-31
> 来源：beta-roadmap.md P3，描述为 "crash pack → patch.diff"
> 下一个 session：从 brainstorming 第一步"探索项目上下文 + 提出 2-3 方案"继续

---

## 任务概述

当测试会话崩溃时，自动分析 crash evidence pack，生成结构化修复建议（`patch.diff` 或其他机器可读格式）。从 Level 1（"发生了什么" — summary.json + 截图 + 日志）升级到 Level 2（"为什么会发生 + 怎么修"）。

## 现有崩溃处理流程（理解上下文）

```
断言失败 / 游戏崩溃 / 适配器异常
    ↓
RecoveryStrategy.decide() → RecoveryDecision (FAST_PATH/RECREATE/GAME_RESTART/FULL_RESTART/TERMINATE)
    ↓
如果 TERMINATE 或所有恢复路径耗尽：
    capture_bug_snapshot() → EvidencePack { summary.json, screenshot.png, session.log }
    ↓
EvidencePackager.export_artifact() → <pack_id>_<result>_<timestamp>.zip
    ↓
人工查看 ZIP → 分析 → 修复
```

**B10 要插入的环节：** 在 `export_artifact()` 之前，对 EvidencePack 追加一个 `repair_suggestions.json`（或 `.diff`），包含：
- 崩溃签名（已有 `crash_signature()` → `recovery.py:72-80`）
- 根因分析（基于错误类别 + 堆栈 + 游戏状态）
- 修复建议（针对性的代码/配置修改）

## 已有资产（可直接复用）

| 资产 | 位置 | 说明 |
|------|------|------|
| `FailureRecord` | `core/recovery.py:50-58` | dataclass：error_type, message, timestamp, exit_code |
| `crash_signature()` | `core/recovery.py:72-80` | 确定性崩溃签名生成（type+exit_code） |
| `ErrorCategory` (6 类) | `common/errors.py` | adapter_error, game_error, assertion_error, crash_error, timeout_error, session_error |
| `STS2Error` | `common/errors.py` | {type, message, detail, timestamp} |
| `GameState` | `common/state.py` | frozen pydantic，15 种 GameScreen + 额外字段 |
| `SummaryJson` | `common/evidence.py` | 证据包摘要：test_run + failure + screenshots + metrics |
| `EvidencePackager` | `evidence/packager.py` | 打包 pipeline：collect → summarize → JUnit XML → ZIP |
| `RecoveryDecision` | `core/recovery.py:36-47` | recovery 决策结果 + is_p0 标记 |
| `GameAdapterProtocol` | `adapters/base.py` | 7 个核心方法（含 `get_state`, `get_available_actions`, `capture_bug_snapshot`） |
| `ActionDescriptor` | `core/action_model.py` | 动作描述 + TestResult |

## 关键设计问题（下一个 session 需要讨论）

### 1. 修复建议的粒度

- **选项 A：基于错误类别的规则引擎**
  - 输入的 FailureRecord.error_type/category → 查表 → 输出预定义建议
  - 例如：`crash_error + exit_code=0xC0000005` → "内存访问违例，检查 Mod 中空指针引用"
  - 优点：简单、确定性、零延迟
  - 缺点：建议泛化，不能定位具体代码行
- **选项 B：基于堆栈 + 日志的模式匹配**
  - 解析 Python/C# 堆栈 trace → 定位源码文件+行号 → 结合已知 bug 模式库
  - 优点：精确到文件和行号
  - 缺点：需要维护模式库，跨游戏版本可能失效
- **选项 C：LLM 辅助分析（VLM）**
  - 把 summary.json + 截图 + 最后 N 帧日志发给 LLM → 生成修复建议
  - 优点：最灵活，能处理未知崩溃模式
  - 缺点：延迟高、成本、依赖外部服务、隐私（日志可能含敏感信息）

### 2. 输出格式

- **选项 A：`patch.diff`（传统 unified diff）**
  - 优点：可直接 `git apply`，CI 原生支持
  - 缺点：仅适用于代码修复（配置问题、环境问题无法表示）
- **选项 B：`repair_suggestions.json`（结构化 JSON）**
  - 优点：可表示多种修复类型（代码、配置、环境），机器可读
  - 缺点：不能直接 apply
- **选项 C：两者都输出** — `repair_suggestions.json` 为权威格式，可选附 `patch.diff`（当建议涉及代码修改时）

### 3. 修复建议的置信度和自动化程度

- 高置信度（规则引擎命中）：自动生成 patch
- 中置信度（模式匹配命中）：生成建议 + 标记 `needs_review: true`
- 低置信度（LLM）：仅生成人类可读描述

### 4. 与现有 Evidence Pack 的集成点

当前 `EvidencePackager.export_artifact()` 的流程：
```
pack_dir/
  summary.json       ← 已有
  screenshot.png     ← 已有
  session.log        ← 已有
  reports/
    junit.xml        ← 已有 (B12)
    scene-coverage.json ← 已有
    scene-coverage.md   ← 已有
```

B10 追加：
```
  reports/
    repair_suggestions.json  ← 新增
    (可选) patch.diff        ← 新增
```

## 建议的 MVP 设计（供讨论）

### 新增模块：`core/repair_advisor.py`

```
RepairSuggestion {            # 单条修复建议
  confidence: float           # 0.0–1.0
  category: str               # code_fix / config_change / env_fix / investigation_needed
  title: str                  # 一句话描述
  description: str            # 详细说明
  source_location: str | None # 文件:行号 (如果可以定位)
  patch: str | None           # unified diff (如果 category=code_fix)
  related_docs: list[str]     # 相关文档链接
}

RepairReport {
  crash_signature: str        # 来自 crash_signature()
  suggestions: list[RepairSuggestion]
  generated_at: str           # ISO 8601
  analysis_duration_ms: float
}
```

### 第一阶段规则引擎（MVP）

MVP 覆盖最常见的 5-8 种崩溃模式：

1. `crash_error + exit_code != 0` → "游戏进程异常退出，检查本次修改的 C# 代码是否有未处理异常"
2. `adapter_error + version_mismatch` → "适配器版本与游戏版本不兼容，更新 BaseLib 或 CLI Mod"
3. `timeout_error + game_state==LOADING` → "游戏加载超时，检查 Mod 初始化代码是否有死循环"
4. `assertion_error + expected_screen != actual_screen` → "状态转换断言失败：预期 {expected}，实际 {actual}"
5. `session_error + FileNotFoundError` → "关键文件缺失，检查 STS2_CLI_MOD_PATH 配置"

## 需要注意的约束

1. **不引入重量级依赖**：MVP 应纯 Python stdlib，不需要 LLM SDK、机器学习库。
2. **确定性**：相同输入 → 相同输出。不依赖随机性或外部网络。
3. **向后兼容**：`repair_suggestions.json` 缺失时，现有流程不受影响。
4. **与 B8 (Visual QA) 的关系**：B8 如果实现，其 OCR/VLM 输出可以作为 B10 的输入信号，但 B10 MVP 不依赖 B8。
5. **不是自动修复**：B10 是 Level 2 "建议"，不是 Level 3 "自动修复"。生成的 `patch.diff` 仅供人类审查后手动 apply。

## 相关文件

- `src/sts2_autotest/core/recovery.py` — 恢复策略 + FailureRecord + crash_signature
- `src/sts2_autotest/evidence/packager.py` — 证据打包 pipeline，B10 的集成点
- `src/sts2_autotest/common/errors.py` — ErrorCategory + STS2Error
- `src/sts2_autotest/common/evidence.py` — SummaryJson
- `src/sts2_autotest/common/state.py` — GameState + GameScreen
- `docs/beta-roadmap.md` — B10 在 P3 (line 106)，描述 "crash pack → patch.diff"

## 下一个 session 的启动建议

```
/brainstorming B10 Level 2 修复建议 — 从 handoff 继续
```

Session 应该先审阅此 handoff + 相关源文件，然后按照 brainstorming checklist：确认范围 → 讨论规则引擎 vs LLM vs 混合方案 → 呈现设计 → 生成 spec → 进入 writing-plans。
