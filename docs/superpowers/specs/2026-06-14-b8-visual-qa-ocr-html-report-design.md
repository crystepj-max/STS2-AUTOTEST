# B8 Visual QA Engine - OCR HTML 报告辅助分析设计

> 类型：设计文档（Design Spec）
> 日期：2026-06-14
> 来源：`docs/beta-roadmap.md` P4，"Visual QA Engine（OCR + OpenCV + VLM）"
> 当前范围：B8 MVP 第一阶段，仅实现截图 OCR 辅助分析并展示在 HTML 测试报告中

## 1. 背景

B8 在 Beta 路线图中定义为 `OCR + OpenCV + VLM 视觉审查`。当前仓库已有截图采集能力，`ScreenCapture` 负责截图、RGB 颜色数量、分辨率和文件大小校验；这些能力只能证明截图有效，不能判断截图中的 UI 文案是否存在 localization 风险。

最新 HTML 报告能力位于远端分支 `origin/feat/b11-cicd-pipeline`，尚未合入 `main`。该分支新增：

- `src/sts2_autotest/report_html.py`：从 `test-results.json` 生成 `test-report.html`，并将截图内联为 base64。
- `src/sts2_autotest/core/test_agent_runner.py`：生成 `test-results.json`，其中 `card_results` 包含 `screenshot_before` / `screenshot_after`。
- `autotest gen-report`：从结构化 JSON 重新生成 HTML 报告。

B8 MVP 应优先服务这个 HTML 报告：在每张测试结果截图旁展示 OCR 辅助分析结论，作为人类审查参考，不改变测试结果。

## 2. 目标与非目标

### 2.1 目标

1. 对 HTML 测试报告中的截图默认附加 OCR 辅助分析结论。
2. 首批规则只识别 localization 文本风险：裸 key、missing localization 占位、明显未替换 token。
3. OCR 结论只作为辅助参考，不影响测试用例的 `通过`、`失败`、`阻塞`、`跳过` 结果。
4. 保留机器可读结构，写入 `test-results.json`，由 `report_html.py` 渲染到 `test-report.html`。
5. 真实 OCR provider 可选；没有 OCR 依赖或执行失败时，报告显示 `未执行` 或 `不可用`，不阻断测试和报告生成。

### 2.2 非目标

1. 不在 MVP 中实现 OpenCV UI 状态识别。
2. 不在 MVP 中实现 VLM 语义审查，只预留后续扩展边界。
3. 不把 OCR 发现升级为测试失败。
4. 不训练自定义 OCR/视觉模型。
5. 不改写 `ScreenCapture` 的截图职责。

## 3. 总体方案

采用“轻量离线优先，VLM 可选”的方案。B8 MVP 先建立 OCR provider、localization detector、Visual QA 数据模型和 HTML 报告接入点。

数据流：

```text
TestAgentRunner 截图
  -> screenshot_before / screenshot_after
  -> VisualQaEngine 分析截图文本
  -> test-results.json 写入 ocr 辅助结果
  -> report_html.py 渲染截图和 OCR 结论
  -> test-report.html 供人类审查
```

`ScreenCapture` 仍只负责生成截图；`VisualQaEngine` 只消费截图路径并产出分析结果；`report_html.py` 只负责展示，不执行 OCR。

## 4. 模块边界

### 4.1 新增数据模型

建议新增 `src/sts2_autotest/common/visual_qa.py`：

```python
class OcrTextBlock(BaseModel):
    text: str
    confidence: float | None = None
    bbox: list[int] | None = None

class VisualQaFinding(BaseModel):
    rule_id: str
    severity: Literal["warning", "info"]
    message: str
    text: str
    confidence: float | None = None
    bbox: list[int] | None = None

class ScreenshotOcrAnalysis(BaseModel):
    status: Literal["passed", "warning", "skipped"]
    provider: str
    findings: list[VisualQaFinding] = []
    extracted_text: list[OcrTextBlock] = []
    message: str | None = None
    duration_ms: float = 0.0
```

`severity` MVP 不使用 `error`，避免暗示测试失败。发现裸 key 或 missing localization 时，`status="warning"`。

### 4.2 新增核心模块

建议新增 `src/sts2_autotest/core/visual_qa.py`：

```python
class OcrProvider(Protocol):
    name: str
    def extract_text(self, image_path: Path) -> list[OcrTextBlock]: ...

class LocalizationTextDetector:
    def analyze(self, blocks: list[OcrTextBlock]) -> list[VisualQaFinding]: ...

class VisualQaEngine:
    def analyze_screenshot(self, image_path: Path) -> ScreenshotOcrAnalysis: ...
```

职责边界：

- `OcrProvider`：只把图片转为 OCR 文本块，不判断对错。
- `LocalizationTextDetector`：只分析文本，识别裸 key / missing localization。
- `VisualQaEngine`：负责调用 provider 和 detector，汇总异常为 `skipped`，保证不向上抛出 OCR 依赖错误。

### 4.3 Provider 策略

MVP provider：

| Provider | 用途 | 默认状态 |
|---|---|---|
| `disabled` | 没有 OCR 依赖时返回 skipped | 默认可用 |
| `static` / `fake` | 单元测试和 fixture 驱动 | 测试使用 |
| `tesseract` | 本地真实 OCR，可选依赖或系统命令 | 可选 |

不在 MVP 中加入 EasyOCR、PaddleOCR 或 VLM provider。它们后续可以实现同一个 `OcrProvider` 或单独 `VisualQaProvider` 接口。

## 5. HTML 报告接入

### 5.1 `test-results.json` 扩展

在每个含截图字段的对象旁增加可选 OCR 字段：

```json
{
  "screenshot_before": "screenshots/card-x-before.png",
  "screenshot_before_ocr": {
    "status": "warning",
    "provider": "tesseract",
    "findings": [
      {
        "rule_id": "localization_text.raw_key",
        "severity": "warning",
        "text": "gawain.card.strike.name",
        "message": "疑似 localization key 出现在截图文本中",
        "confidence": 0.9
      }
    ],
    "message": null,
    "duration_ms": 42.5
  }
}
```

同理支持 `screenshot_after_ocr`。字段缺失时，HTML 报告按旧格式渲染，保持向后兼容。

### 5.2 `TestAgentRunner` 接入

`TestAgentRunner._build_html_report_card_results()` 构造 `card_results` 时，读取每张截图对应的 OCR 分析结果并写入配置。

建议将 OCR 分析缓存为内存字典：

```text
self._screenshot_ocr: dict[str, ScreenshotOcrAnalysis]
```

key 使用规范化后的截图相对路径，例如 `screenshots/card-x-before.png`。截图成功后可立即分析，也可在构建 HTML 配置前批量分析。为减少对冒烟流程的影响，MVP 推荐在 `_build_html_report_config()` 前批量分析已有截图，并捕获所有异常。

### 5.3 `report_html.py` 渲染

`card_html()` 渲染图片后附加一个紧凑 OCR 辅助块：

```text
OCR 辅助分析：发现 1 条可疑文案
- [warning] 疑似 localization key 出现在截图文本中：gawain.card.strike.name
Provider: tesseract
```

状态展示规则：

| status | HTML 文案 |
|---|---|
| `passed` | `OCR 辅助分析：未发现 localization 风险` |
| `warning` | `OCR 辅助分析：发现 N 条可疑文案` |
| `skipped` | `OCR 辅助分析：未执行 - <message>` |
| 字段缺失 | 不显示 OCR 块，兼容旧报告 |

HTML 报告中的测试结果徽章仍只取原有 `result` 字段，不读取 OCR 状态。

## 6. Localization 规则

MVP 规则全部是启发式，目标是高信号提示，不追求完全覆盖。

| rule_id | 条件 | severity | message |
|---|---|---|---|
| `localization_text.raw_key` | 文本匹配疑似 key pattern | warning | 疑似 localization key 出现在截图文本中 |
| `localization_text.missing_marker` | 文本包含 `MISSING`、`TODO_LOCALIZE`、`<missing>`、`missing localization` | warning | 疑似 missing localization 占位出现在截图文本中 |
| `localization_text.unresolved_token` | 文本包含 `{0}`、`{{...}}`、`%s` 等未替换 token | warning | 疑似未替换文本 token 出现在截图文本中 |

默认裸 key pattern：

```text
([A-Za-z0-9_]+[.:/]){2,}[A-Za-z0-9_]+
```

该 pattern 用于捕捉 `gawain.card.strike.name`、`GawainMod:Strike.name`、`character/gawain/name` 等形态。误报时先通过规则白名单处理，暂不引入复杂词典。

## 7. 配置

建议在 `FrameworkConfig` 或新增 `VisualQaConfig` 中加入：

```python
class VisualQaConfig(BaseModel):
    enabled: bool = True
    ocr_provider: Literal["disabled", "static", "tesseract"] = "disabled"
    show_extracted_text: bool = False
    raw_key_pattern: str = r"([A-Za-z0-9_]+[.:/]){2,}[A-Za-z0-9_]+"
```

默认 `enabled=True` 表示报告器会尝试产生 OCR 辅助块；默认 `ocr_provider="disabled"` 表示没有真实 OCR 时输出 `skipped`，不影响报告生成。真实环境可通过环境变量启用 `tesseract`。

## 8. 错误处理

1. 图片文件不存在：返回 `skipped`，message 写明 `screenshot not found`。
2. OCR provider 不可用：返回 `skipped`，message 写明 provider 不可用。
3. OCR 执行超时或异常：返回 `skipped`，message 写明异常类型，不抛出到报告生成器。
4. OCR finding 不改变测试结果。
5. HTML 生成失败不应由 OCR 导致；OCR 块渲染必须对缺字段和畸形字段容错。

## 9. 测试策略

单元测试优先，不依赖真实游戏进程或真实 OCR。除此之外，必须支持把用户提供的真实游戏截图作为 fixture 输入，用于验证 HTML 报告链路能在真实截图文件上生成 OCR 辅助结论，并保持测试结果通过。

新增或扩展测试：

1. `tests/unit/test_visual_qa.py`
   - fake provider 返回裸 key，结果为 `warning`。
   - fake provider 返回正常文案，结果为 `passed`。
   - disabled provider 返回 `skipped`。
   - provider 抛异常时 engine 返回 `skipped`。
2. `tests/unit/test_report_html.py`
   - `screenshot_before_ocr.status=warning` 时，HTML 出现 `OCR 辅助分析` 和 finding 文案。
   - `status=passed` 时，HTML 显示未发现风险。
   - 字段缺失时旧报告仍正常渲染。
3. `tests/unit/test_agent_runner_html_report.py` 或现有 runner 测试
   - `_build_html_report_card_results()` 能把截图 OCR 结果写入 `test-results.json` 配置。
   - OCR skipped 不改变 card/test case 的 `result`。
4. `tests/fixtures/visual_qa/` 游戏截图 fixture
   - 支持放入用户提供的 PNG 截图，例如 `gawain-card-before.png`。
   - 测试用例构造包含该截图的 `test-results.json` 或 card result。
   - 使用 fake/static OCR provider 时，按截图文件名或测试注入文本返回可控 OCR 文本，确保测试不依赖本机 OCR 安装。
   - 生成 HTML 后断言图片和 OCR 辅助块同时存在，且对应测试结果仍为 `通过`。

可选集成测试：

- 在本机安装 Tesseract 后，对用户提供的 fixture 截图执行真实 OCR。该测试默认跳过，不作为 CI 必需项。

## 10. 验收标准

1. HTML 报告中每张有 OCR 结果的截图旁都会显示 OCR 辅助分析块。
2. 发现裸 key / missing localization 时，HTML 显示 warning，但测试结果仍保持原值。
3. OCR 不可用时，HTML 显示未执行或不显示 OCR 块，报告生成成功。
4. `test-results.json` 可承载截图级 OCR 结果，旧字段保持兼容。
5. 不安装真实 OCR/VLM 依赖时，现有单元测试和 HTML 报告测试仍可通过。
6. 用户提供一张游戏截图作为 fixture 后，测试能用该截图生成 HTML 报告，报告中显示截图和 OCR 辅助分析，且测试结果仍为 `通过`。
7. B8 设计不要求 `main` 已合入 HTML 报告分支；实现时应基于包含 `report_html.py` 的分支或先完成分支合并。

## 11. 后续扩展

1. OpenCV detector：黑屏、异常纯色、关键 UI 区域缺失。
2. VLM detector：解释截图语义，辅助判断 UI 状态和视觉破损。
3. Evidence pack 集成：将 OCR/VLM 结论汇总到 `reports/visual_qa.json` 和 `summary.md`。
4. B10 集成：将 Visual QA finding 作为修复建议输入信号。
