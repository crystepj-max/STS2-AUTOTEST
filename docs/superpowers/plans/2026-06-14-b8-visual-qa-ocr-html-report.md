# B8 Visual QA OCR HTML 报告实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 HTML 测试报告中的截图增加 OCR 辅助分析块，识别 localization 裸 key / missing marker / 未替换 token，并保持测试结果不受 OCR 影响。

**架构：** 新增 `common/visual_qa.py` 定义截图 OCR 分析模型，新增 `core/visual_qa.py` 封装 provider、detector 和 engine。`TestAgentRunner` 在构建 `test-results.json` 时把截图级 OCR 分析写入 `screenshot_before_ocr` / `screenshot_after_ocr`，`report_html.py` 只负责渲染这些结果，不执行 OCR。

**技术栈：** Python 3.11+、Pydantic v2、pytest、现有 `report_html.py` HTML 字符串渲染、fake/static OCR provider；不引入真实 OCR/VLM/OpenCV 依赖。

---

## 前置条件

此计划必须在包含 HTML 报告实现的代码基线上执行。当前设计依据来自远端 `origin/feat/b11-cicd-pipeline`：

- `src/sts2_autotest/report_html.py`
- `src/sts2_autotest/core/test_agent_runner.py` 中的 `_build_html_report_card_results()` / `_generate_html_report()`
- `tests/unit/test_report_html.py`

开始实现前先确认：

```bash
test -f src/sts2_autotest/report_html.py
test -f tests/unit/test_report_html.py
rg -n "def _build_html_report_card_results|def _generate_html_report" src/sts2_autotest/core/test_agent_runner.py
```

预期：三个命令均成功。如果失败，先合并或切换到包含 `origin/feat/b11-cicd-pipeline` 的工作分支，再执行本计划。

## 文件结构

- 创建：`src/sts2_autotest/common/visual_qa.py`
  - 职责：定义 `OcrTextBlock`、`VisualQaFinding`、`ScreenshotOcrAnalysis` 三个 frozen Pydantic 模型。
- 创建：`src/sts2_autotest/core/visual_qa.py`
  - 职责：定义 `OcrProvider` 协议、`DisabledOcrProvider`、`StaticOcrProvider`、`LocalizationTextDetector`、`VisualQaEngine`。
- 修改：`src/sts2_autotest/core/test_agent_runner.py`
  - 职责：在 HTML 配置构建期间分析截图，并把 OCR 结果写入 `card_results`。
- 修改：`src/sts2_autotest/report_html.py`
  - 职责：在卡牌截图下方渲染 OCR 辅助分析块，缺字段时保持旧报告不变。
- 创建：`tests/unit/test_visual_qa.py`
  - 职责：覆盖 OCR 模型、provider、detector、engine 的核心行为。
- 修改：`tests/unit/test_report_html.py`
  - 职责：覆盖 HTML OCR 块渲染、缺字段兼容、fixture 截图链路。
- 创建：`tests/unit/test_agent_runner_visual_qa.py`
  - 职责：覆盖 runner 写入 `screenshot_before_ocr` / `screenshot_after_ocr`，且不改变测试结果。
- 创建：`tests/fixtures/visual_qa/gawain-card-before.png`
  - 职责：用户提供的真实游戏截图 fixture。若实现时用户尚未提供截图，先放入一张明确标注为临时 fixture 的 1x1 PNG 不可接受；必须向用户索取或使用他们已提供的真实截图。

## 任务 1：新增 Visual QA 数据模型

**文件：**
- 创建：`src/sts2_autotest/common/visual_qa.py`
- 创建：`tests/unit/test_visual_qa.py`

- [ ] **步骤 1：编写失败的模型测试**

在 `tests/unit/test_visual_qa.py` 中新增：

```python
"""Tests for Visual QA OCR analysis models and engine."""

from __future__ import annotations

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)


def test_screenshot_ocr_analysis_roundtrip() -> None:
    analysis = ScreenshotOcrAnalysis(
        status="warning",
        provider="static",
        findings=[
            VisualQaFinding(
                rule_id="localization_text.raw_key",
                severity="warning",
                message="疑似 localization key 出现在截图文本中",
                text="gawain.card.strike.name",
                confidence=0.9,
                bbox=[1, 2, 30, 40],
            )
        ],
        extracted_text=[
            OcrTextBlock(
                text="gawain.card.strike.name",
                confidence=0.9,
                bbox=[1, 2, 30, 40],
            )
        ],
        duration_ms=12.5,
    )

    data = analysis.model_dump(mode="json")
    restored = ScreenshotOcrAnalysis.model_validate(data)

    assert restored.status == "warning"
    assert restored.provider == "static"
    assert restored.findings[0].rule_id == "localization_text.raw_key"
    assert restored.extracted_text[0].text == "gawain.card.strike.name"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -m pytest tests/unit/test_visual_qa.py::test_screenshot_ocr_analysis_roundtrip -q
```

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'sts2_autotest.common.visual_qa'`。

- [ ] **步骤 3：实现最小数据模型**

创建 `src/sts2_autotest/common/visual_qa.py`：

```python
"""Visual QA data models for screenshot OCR analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OcrTextBlock(BaseModel):
    """A single OCR text block extracted from a screenshot."""

    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    bbox: list[int] | None = None


class VisualQaFinding(BaseModel):
    """A non-blocking OCR finding shown as report assistance."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Literal["warning", "info"]
    message: str
    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    bbox: list[int] | None = None


class ScreenshotOcrAnalysis(BaseModel):
    """OCR analysis result for one screenshot.

    Status values are intentionally non-failing. A warning is displayed in
    reports but must not change the underlying test result.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["passed", "warning", "skipped"]
    provider: str
    findings: list[VisualQaFinding] = []
    extracted_text: list[OcrTextBlock] = []
    message: str | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_visual_qa.py::test_screenshot_ocr_analysis_roundtrip -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/sts2_autotest/common/visual_qa.py tests/unit/test_visual_qa.py
git commit -m "feat: add visual QA OCR models"
```

## 任务 2：实现 OCR provider、localization detector 和 engine

**文件：**
- 创建：`src/sts2_autotest/core/visual_qa.py`
- 修改：`tests/unit/test_visual_qa.py`

- [ ] **步骤 1：编写 detector 失败测试**

追加到 `tests/unit/test_visual_qa.py`：

```python
from pathlib import Path

from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    LocalizationTextDetector,
    StaticOcrProvider,
    VisualQaEngine,
)


def test_localization_detector_flags_raw_key() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze(
        [OcrTextBlock(text="gawain.card.strike.name", confidence=0.91)]
    )

    assert len(findings) == 1
    assert findings[0].rule_id == "localization_text.raw_key"
    assert findings[0].severity == "warning"
    assert findings[0].text == "gawain.card.strike.name"


def test_localization_detector_flags_missing_marker() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze([OcrTextBlock(text="<missing> CARD_NAME")])

    assert len(findings) == 1
    assert findings[0].rule_id == "localization_text.missing_marker"


def test_localization_detector_flags_unresolved_token() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze([OcrTextBlock(text="Deal {0} damage")])

    assert len(findings) == 1
    assert findings[0].rule_id == "localization_text.unresolved_token"


def test_localization_detector_passes_normal_text() -> None:
    detector = LocalizationTextDetector()
    findings = detector.analyze([OcrTextBlock(text="打击 造成 6 点伤害")])

    assert findings == []
```

- [ ] **步骤 2：运行 detector 测试验证失败**

运行：

```bash
python -m pytest tests/unit/test_visual_qa.py::test_localization_detector_flags_raw_key -q
```

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'sts2_autotest.core.visual_qa'`。

- [ ] **步骤 3：实现 detector 和 provider 骨架**

创建 `src/sts2_autotest/core/visual_qa.py`：

```python
"""Visual QA engine for non-blocking screenshot OCR assistance."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Protocol

from sts2_autotest.common.visual_qa import (
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)


_RAW_KEY_PATTERN = re.compile(r"([A-Za-z0-9_]+[.:/]){2,}[A-Za-z0-9_]+")
_MISSING_MARKERS = (
    "MISSING",
    "TODO_LOCALIZE",
    "LOCALIZE_ME",
    "<missing>",
    "missing localization",
)
_TOKEN_PATTERN = re.compile(r"(\{[0-9]+\}|\{\{[^}]+\}\}|%s)")


class OcrProvider(Protocol):
    """Extract OCR text blocks from one screenshot."""

    name: str

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        """Return OCR text blocks for image_path."""
        ...


class DisabledOcrProvider:
    """Provider used when OCR is not configured."""

    name = "disabled"

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        raise RuntimeError("OCR provider is disabled")


class StaticOcrProvider:
    """Test provider returning preconfigured text by screenshot filename."""

    name = "static"

    def __init__(self, text_by_name: dict[str, list[str]] | None = None) -> None:
        self._text_by_name = text_by_name or {}

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        return [
            OcrTextBlock(text=text, confidence=1.0)
            for text in self._text_by_name.get(image_path.name, [])
        ]


class LocalizationTextDetector:
    """Detect localization risks in OCR text blocks."""

    def __init__(self, raw_key_pattern: str = _RAW_KEY_PATTERN.pattern) -> None:
        self._raw_key_pattern = re.compile(raw_key_pattern)

    def analyze(self, blocks: list[OcrTextBlock]) -> list[VisualQaFinding]:
        findings: list[VisualQaFinding] = []
        for block in blocks:
            text = block.text.strip()
            if not text:
                continue
            if self._raw_key_pattern.search(text):
                findings.append(
                    VisualQaFinding(
                        rule_id="localization_text.raw_key",
                        severity="warning",
                        message="疑似 localization key 出现在截图文本中",
                        text=text,
                        confidence=block.confidence,
                        bbox=block.bbox,
                    )
                )
                continue
            if any(marker.lower() in text.lower() for marker in _MISSING_MARKERS):
                findings.append(
                    VisualQaFinding(
                        rule_id="localization_text.missing_marker",
                        severity="warning",
                        message="疑似 missing localization 占位出现在截图文本中",
                        text=text,
                        confidence=block.confidence,
                        bbox=block.bbox,
                    )
                )
                continue
            if _TOKEN_PATTERN.search(text):
                findings.append(
                    VisualQaFinding(
                        rule_id="localization_text.unresolved_token",
                        severity="warning",
                        message="疑似未替换文本 token 出现在截图文本中",
                        text=text,
                        confidence=block.confidence,
                        bbox=block.bbox,
                    )
                )
        return findings


class VisualQaEngine:
    """Run OCR and localization detectors for one screenshot."""

    def __init__(
        self,
        provider: OcrProvider | None = None,
        detector: LocalizationTextDetector | None = None,
    ) -> None:
        self._provider = provider or DisabledOcrProvider()
        self._detector = detector or LocalizationTextDetector()
```

- [ ] **步骤 4：运行 detector 测试验证通过**

运行：

```bash
python -m pytest \
  tests/unit/test_visual_qa.py::test_localization_detector_flags_raw_key \
  tests/unit/test_visual_qa.py::test_localization_detector_flags_missing_marker \
  tests/unit/test_visual_qa.py::test_localization_detector_flags_unresolved_token \
  tests/unit/test_visual_qa.py::test_localization_detector_passes_normal_text \
  -q
```

预期：4 passed。

- [ ] **步骤 5：编写 engine 失败测试**

追加到 `tests/unit/test_visual_qa.py`：

```python
def test_visual_qa_engine_returns_warning_for_raw_key(tmp_path: Path) -> None:
    image = tmp_path / "gawain-card-before.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(
        StaticOcrProvider({"gawain-card-before.png": ["gawain.card.strike.name"]})
    )

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "warning"
    assert analysis.provider == "static"
    assert analysis.findings[0].rule_id == "localization_text.raw_key"


def test_visual_qa_engine_returns_passed_for_normal_text(tmp_path: Path) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(StaticOcrProvider({"normal.png": ["打击 造成 6 点伤害"]}))

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "passed"
    assert analysis.findings == []


def test_visual_qa_engine_returns_skipped_when_provider_disabled(tmp_path: Path) -> None:
    image = tmp_path / "normal.png"
    image.write_bytes(b"fake png bytes")
    engine = VisualQaEngine(DisabledOcrProvider())

    analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert analysis.provider == "disabled"
    assert "disabled" in str(analysis.message).lower()


def test_visual_qa_engine_returns_skipped_for_missing_image(tmp_path: Path) -> None:
    engine = VisualQaEngine(StaticOcrProvider({}))

    analysis = engine.analyze_screenshot(tmp_path / "missing.png")

    assert analysis.status == "skipped"
    assert analysis.provider == "static"
    assert "screenshot not found" in str(analysis.message)
```

- [ ] **步骤 6：运行 engine 测试验证失败**

运行：

```bash
python -m pytest tests/unit/test_visual_qa.py::test_visual_qa_engine_returns_warning_for_raw_key -q
```

预期：FAIL，报错包含 `AttributeError: 'VisualQaEngine' object has no attribute 'analyze_screenshot'`。

- [ ] **步骤 7：实现 `VisualQaEngine.analyze_screenshot()`**

在 `src/sts2_autotest/core/visual_qa.py` 的 `VisualQaEngine` 类中追加：

```python
    def analyze_screenshot(self, image_path: Path) -> ScreenshotOcrAnalysis:
        started = time.perf_counter()
        provider_name = getattr(self._provider, "name", "unknown")

        if not image_path.is_file():
            return ScreenshotOcrAnalysis(
                status="skipped",
                provider=provider_name,
                message="screenshot not found",
                duration_ms=self._elapsed_ms(started),
            )

        try:
            blocks = self._provider.extract_text(image_path)
            findings = self._detector.analyze(blocks)
        except Exception as exc:
            return ScreenshotOcrAnalysis(
                status="skipped",
                provider=provider_name,
                message=f"{exc.__class__.__name__}: {exc}",
                duration_ms=self._elapsed_ms(started),
            )

        return ScreenshotOcrAnalysis(
            status="warning" if findings else "passed",
            provider=provider_name,
            findings=findings,
            extracted_text=blocks,
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)
```

- [ ] **步骤 8：运行 Visual QA 测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_visual_qa.py -q
```

预期：全部 PASS。

- [ ] **步骤 9：Commit**

```bash
git add src/sts2_autotest/core/visual_qa.py tests/unit/test_visual_qa.py
git commit -m "feat: add visual QA OCR engine"
```

## 任务 3：在 HTML 报告中渲染 OCR 辅助块

**文件：**
- 修改：`src/sts2_autotest/report_html.py`
- 修改：`tests/unit/test_report_html.py`

- [ ] **步骤 1：编写 warning OCR 块失败测试**

追加到 `tests/unit/test_report_html.py`：

```python
def test_build_report_html_renders_ocr_warning_block(tmp_path):
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    before = screenshots / "card-before.png"
    before.write_bytes(_PNG_1X1)

    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "打击",
                        "result": "通过",
                        "exp": {"伤害": 6},
                        "screenshot_before": "screenshots/card-before.png",
                        "screenshot_before_ocr": {
                            "status": "warning",
                            "provider": "static",
                            "findings": [
                                {
                                    "rule_id": "localization_text.raw_key",
                                    "severity": "warning",
                                    "message": "疑似 localization key 出现在截图文本中",
                                    "text": "gawain.card.strike.name",
                                    "confidence": 0.9,
                                }
                            ],
                        },
                        "screenshot_after": "",
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析：发现 1 条可疑文案" in html
    assert "gawain.card.strike.name" in html
    assert "Provider: static" in html
    assert '<span class="badge badge-pass">通过</span>' in html
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -m pytest tests/unit/test_report_html.py::test_build_report_html_renders_ocr_warning_block -q
```

预期：FAIL，断言找不到 `OCR 辅助分析`。

- [ ] **步骤 3：新增 HTML OCR 渲染 helper**

在 `src/sts2_autotest/report_html.py` 的 `build_report_html()` 内、`card_html()` 前新增嵌套函数：

```python
    def ocr_html(analysis: Any) -> str:
        if not isinstance(analysis, dict):
            return ""
        status = str(analysis.get("status", "skipped"))
        provider = str(analysis.get("provider", "unknown"))
        message = str(analysis.get("message") or "")
        findings = analysis.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        if status == "passed":
            body = "OCR 辅助分析：未发现 localization 风险"
        elif status == "warning":
            rows = []
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                severity = str(finding.get("severity", "warning"))
                text = str(finding.get("text", ""))
                finding_message = str(finding.get("message", ""))
                rows.append(
                    f"<li>[{severity}] {finding_message}: <code>{text}</code></li>"
                )
            joined = "".join(rows)
            body = f"OCR 辅助分析：发现 {len(rows)} 条可疑文案<ul>{joined}</ul>"
        else:
            suffix = f" - {message}" if message else ""
            body = f"OCR 辅助分析：未执行{suffix}"

        return (
            '<div class="ocr-box">'
            f"<div>{body}</div>"
            f'<div class="ocr-provider">Provider: {provider}</div>'
            "</div>"
        )
```

在 CSS 中追加：

```css
.ocr-box{margin-top:6px;padding:8px;border-radius:4px;background:#111827;border:1px solid #374151;font-size:12px;color:#cbd5e1}
.ocr-box ul{margin:6px 0 0 18px}
.ocr-box code{color:#facc15;font-family:"SF Mono",Menlo,monospace}
.ocr-provider{margin-top:4px;color:#64748b;font-size:11px}
```

- [ ] **步骤 4：把 OCR helper 接到 `card_html()`**

在 `card_html()` 中读取 OCR 字段：

```python
        before_ocr = ocr_html(card.get("screenshot_before_ocr"))
        after_ocr = ocr_html(card.get("screenshot_after_ocr"))
```

将图片块替换为：

```python
    <div class="img-box"><div class="img-label">打出前</div>{f'<img src="{before_src}">' if before_src else '<i>无截图</i>'}{before_ocr}</div>
    <div class="img-box"><div class="img-label">打出后</div>{f'<img src="{after_src}">' if after_src else '<i>无截图</i>'}{after_ocr}</div>
```

- [ ] **步骤 5：运行 warning 渲染测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_report_html.py::test_build_report_html_renders_ocr_warning_block -q
```

预期：PASS。

- [ ] **步骤 6：编写 passed/skipped/缺字段兼容测试**

追加到 `tests/unit/test_report_html.py`：

```python
def test_build_report_html_renders_ocr_passed_block(tmp_path):
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    image = screenshots / "card-before.png"
    image.write_bytes(_PNG_1X1)

    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "CARD",
                        "name": "打击",
                        "result": "通过",
                        "screenshot_before": "screenshots/card-before.png",
                        "screenshot_before_ocr": {
                            "status": "passed",
                            "provider": "static",
                            "findings": [],
                        },
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析：未发现 localization 风险" in html


def test_build_report_html_renders_ocr_skipped_block(tmp_path):
    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "CARD",
                        "name": "打击",
                        "result": "通过",
                        "screenshot_before": "",
                        "screenshot_before_ocr": {
                            "status": "skipped",
                            "provider": "disabled",
                            "message": "RuntimeError: OCR provider is disabled",
                        },
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析：未执行 - RuntimeError: OCR provider is disabled" in html


def test_build_report_html_omits_ocr_block_when_field_missing(tmp_path):
    config = {
        "test_run_id": "demo-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "CARD",
                        "name": "打击",
                        "result": "通过",
                        "screenshot_before": "",
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "OCR 辅助分析" not in html
    assert "CARD" in html
```

- [ ] **步骤 7：运行 HTML 报告测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_report_html.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：Commit**

```bash
git add src/sts2_autotest/report_html.py tests/unit/test_report_html.py
git commit -m "feat: render OCR assistance in HTML report"
```

## 任务 4：TestAgentRunner 写入截图 OCR 分析

**文件：**
- 修改：`src/sts2_autotest/core/test_agent_runner.py`
- 创建：`tests/unit/test_agent_runner_visual_qa.py`

- [ ] **步骤 1：编写 runner 配置失败测试**

创建 `tests/unit/test_agent_runner_visual_qa.py`：

```python
"""Tests for TestAgentRunner Visual QA HTML report config."""

from __future__ import annotations

from pathlib import Path

from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis, VisualQaFinding
from sts2_autotest.core.test_agent_runner import TestAgentRunner


def _make_runner(tmp_path: Path) -> TestAgentRunner:
    runner = TestAgentRunner(
        mod_project=tmp_path / "mod",
        task_id="visual-qa-demo",
        infra_path=tmp_path / "infra",
    )
    runner._artifact_dir = tmp_path
    runner._card_results = [
        {
            "card_id": "GAWAINMOD-STRIKE_GAWAIN",
            "name": "打击",
            "status": "OK",
            "screenshot_before": "screenshots/card-before.png",
            "screenshot_after": "screenshots/card-after.png",
            "expected_damage": 6,
        }
    ]
    return runner


def test_build_html_report_card_results_includes_ocr_without_changing_result(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    runner._screenshot_ocr = {
        "screenshots/card-before.png": ScreenshotOcrAnalysis(
            status="warning",
            provider="static",
            findings=[
                VisualQaFinding(
                    rule_id="localization_text.raw_key",
                    severity="warning",
                    message="疑似 localization key 出现在截图文本中",
                    text="gawain.card.strike.name",
                    confidence=1.0,
                )
            ],
        )
    }

    cards = runner._build_html_report_card_results()

    assert cards[0]["result"] == "通过"
    assert cards[0]["screenshot_before_ocr"]["status"] == "warning"
    assert cards[0]["screenshot_before_ocr"]["findings"][0]["text"] == "gawain.card.strike.name"
    assert "screenshot_after_ocr" not in cards[0]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python -m pytest tests/unit/test_agent_runner_visual_qa.py::test_build_html_report_card_results_includes_ocr_without_changing_result -q
```

预期：FAIL，断言找不到 `screenshot_before_ocr`。

- [ ] **步骤 3：在 runner 中增加 OCR 缓存读取**

在 `src/sts2_autotest/core/test_agent_runner.py` 导入区域加入：

```python
from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis
```

在 `TestAgentRunner` 类中新增 helper：

```python
    def _get_screenshot_ocr_payload(self, screenshot_path: str) -> dict[str, Any] | None:
        analysis_by_path = getattr(self, "_screenshot_ocr", {}) or {}
        analysis = analysis_by_path.get(screenshot_path)
        if isinstance(analysis, ScreenshotOcrAnalysis):
            return analysis.model_dump(mode="json")
        if isinstance(analysis, dict):
            return analysis
        return None
```

在 `_build_html_report_card_results()` 构造 `entry` 时，先创建变量：

```python
            entry: dict[str, Any] = {
                "card_id": result.get("card_id", ""),
                "name": result.get("name", ""),
                "cost": result.get("energy_cost"),
                "exp": exp,
                "result": report_result,
                "screenshot_before": before_path,
                "screenshot_after": after_path,
                "state_before": self._normalize_html_artifact_path(f"state/card-{result.get('card_id', '')}-before.json"),
                "state_after": self._normalize_html_artifact_path(f"state/card-{result.get('card_id', '')}-after.json") if after_path else "",
            }
            before_ocr = self._get_screenshot_ocr_payload(before_path)
            if before_ocr is not None:
                entry["screenshot_before_ocr"] = before_ocr
            after_ocr = self._get_screenshot_ocr_payload(after_path)
            if after_ocr is not None:
                entry["screenshot_after_ocr"] = after_ocr
            entries.append(entry)
```

替换原来直接 `entries.append({...})` 的代码块。

- [ ] **步骤 4：运行 runner 配置测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_agent_runner_visual_qa.py::test_build_html_report_card_results_includes_ocr_without_changing_result -q
```

预期：PASS。

- [ ] **步骤 5：编写批量 OCR 分析失败测试**

追加到 `tests/unit/test_agent_runner_visual_qa.py`：

```python
from sts2_autotest.core.visual_qa import StaticOcrProvider, VisualQaEngine


def test_analyze_html_report_screenshots_populates_cache(tmp_path: Path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    before = screenshots / "card-before.png"
    before.write_bytes(b"fake png bytes")
    runner = _make_runner(tmp_path)
    runner._visual_qa_engine = VisualQaEngine(
        StaticOcrProvider({"card-before.png": ["gawain.card.strike.name"]})
    )

    runner._analyze_html_report_screenshots()

    analysis = runner._screenshot_ocr["screenshots/card-before.png"]
    assert analysis.status == "warning"
    assert analysis.findings[0].text == "gawain.card.strike.name"
```

- [ ] **步骤 6：运行测试验证失败**

运行：

```bash
python -m pytest tests/unit/test_agent_runner_visual_qa.py::test_analyze_html_report_screenshots_populates_cache -q
```

预期：FAIL，报错包含 `AttributeError: 'TestAgentRunner' object has no attribute '_analyze_html_report_screenshots'`。

- [ ] **步骤 7：实现 `_analyze_html_report_screenshots()`**

在 `src/sts2_autotest/core/test_agent_runner.py` 导入区域加入：

```python
from sts2_autotest.core.visual_qa import VisualQaEngine
```

在 `TestAgentRunner` 类中新增：

```python
    def _get_visual_qa_engine(self) -> VisualQaEngine:
        engine = getattr(self, "_visual_qa_engine", None)
        if isinstance(engine, VisualQaEngine):
            return engine
        engine = VisualQaEngine()
        self._visual_qa_engine = engine
        return engine

    def _analyze_html_report_screenshots(self) -> None:
        engine = self._get_visual_qa_engine()
        cache: dict[str, ScreenshotOcrAnalysis] = getattr(self, "_screenshot_ocr", {}) or {}
        for result in getattr(self, "_card_results", []) or []:
            for key in ("screenshot_before", "screenshot_after"):
                normalized = self._normalize_html_artifact_path(result.get(key, ""))
                if not normalized or normalized in cache:
                    continue
                cache[normalized] = engine.analyze_screenshot(self._artifact_dir / normalized)
        self._screenshot_ocr = cache
```

在 `_build_html_report_config()` 第一行前加入：

```python
        self._analyze_html_report_screenshots()
```

- [ ] **步骤 8：运行 runner OCR 测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_agent_runner_visual_qa.py -q
```

预期：全部 PASS。

- [ ] **步骤 9：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py tests/unit/test_agent_runner_visual_qa.py
git commit -m "feat: add OCR analysis to HTML report config"
```

## 任务 5：支持用户提供的真实游戏截图 fixture

**文件：**
- 创建：`tests/fixtures/visual_qa/gawain-card-before.png`
- 修改：`tests/unit/test_report_html.py`

- [ ] **步骤 1：放入用户提供的截图 fixture**

将用户提供的 PNG 游戏截图保存到：

```text
tests/fixtures/visual_qa/gawain-card-before.png
```

验收检查：

```bash
file tests/fixtures/visual_qa/gawain-card-before.png
```

预期：输出包含 `PNG image data`。

- [ ] **步骤 2：编写 fixture HTML 失败测试**

追加到 `tests/unit/test_report_html.py`：

```python
from pathlib import Path
import shutil


def test_build_report_html_with_user_game_screenshot_fixture(tmp_path):
    fixture = Path("tests/fixtures/visual_qa/gawain-card-before.png")
    assert fixture.is_file()
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    target = screenshots / "gawain-card-before.png"
    shutil.copy2(fixture, target)

    config = {
        "test_run_id": "fixture-run",
        "test_cases": [
            {
                "id": "Card Smoke Test",
                "name": "Card Smoke Test",
                "result": "通过",
                "steps": [],
                "card_results": [
                    {
                        "card_id": "GAWAINMOD-STRIKE_GAWAIN",
                        "name": "打击",
                        "result": "通过",
                        "exp": {"伤害": 6},
                        "screenshot_before": "screenshots/gawain-card-before.png",
                        "screenshot_before_ocr": {
                            "status": "passed",
                            "provider": "static",
                            "findings": [],
                        },
                    }
                ],
            }
        ],
        "card_results": [],
        "_config_dir": str(tmp_path),
    }

    html = build_report_html(config)

    assert "data:image/png;base64," in html
    assert "OCR 辅助分析：未发现 localization 风险" in html
    assert '<span class="badge badge-pass">通过</span>' in html
```

- [ ] **步骤 3：运行 fixture 测试验证通过**

运行：

```bash
python -m pytest tests/unit/test_report_html.py::test_build_report_html_with_user_game_screenshot_fixture -q
```

预期：PASS。若 fixture 缺失，测试失败并提示需要放入用户提供截图。

- [ ] **步骤 4：运行 HTML 报告测试合集**

运行：

```bash
python -m pytest tests/unit/test_report_html.py -q
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add tests/fixtures/visual_qa/gawain-card-before.png tests/unit/test_report_html.py
git commit -m "test: add visual QA game screenshot fixture"
```

## 任务 6：补全文档和路线图状态

**文件：**
- 修改：`docs/beta-roadmap.md`
- 修改：`docs/user-manual.md`

- [ ] **步骤 1：更新路线图说明**

在 `docs/beta-roadmap.md` 的 B8 行中把 B8 更新为已完成 MVP，并补充范围：

```markdown
| B8 | Visual QA Engine | 已实现（MVP） | HTML 报告截图 OCR 辅助分析；不影响测试结果；OpenCV/VLM 后续扩展 |
```

- [ ] **步骤 2：更新用户手册**

在 `docs/user-manual.md` 的截图或报告章节追加：

```markdown
### OCR 辅助分析

HTML 测试报告会在截图旁展示 OCR 辅助分析。该分析用于提示 localization 裸 key、missing localization 占位和未替换 token 风险，不改变测试结果。

当 OCR provider 未配置或不可用时，报告会显示未执行或跳过，不影响 `test-report.html` 生成。
```

- [ ] **步骤 3：运行文档检查**

运行：

```bash
rg -n "Visual QA|OCR 辅助|B8" docs/beta-roadmap.md docs/user-manual.md
```

预期：能找到新增说明。

- [ ] **步骤 4：Commit**

```bash
git add docs/beta-roadmap.md docs/user-manual.md
git commit -m "docs: document visual QA OCR report assistance"
```

## 任务 7：最终验证

**文件：**
- 验证：前面任务创建或修改的源码、测试、fixture 和文档。

- [ ] **步骤 1：运行目标单元测试**

运行：

```bash
python -m pytest \
  tests/unit/test_visual_qa.py \
  tests/unit/test_report_html.py \
  tests/unit/test_agent_runner_visual_qa.py \
  -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行相关既有测试**

运行：

```bash
python -m pytest \
  tests/unit/test_common_evidence.py \
  tests/unit/test_run_test_agent_report.py \
  -q
```

预期：全部 PASS，证明新 Visual QA 模型未破坏既有 evidence/report 语义。

- [ ] **步骤 3：运行静态检查**

运行：

```bash
python -m mypy src/sts2_autotest
```

预期：成功，输出不含 error。

如果当前环境缺少 `mypy`，记录为环境缺失，不要改代码绕过。

- [ ] **步骤 4：运行导入层级检查**

运行：

```bash
python -m lint_imports
```

预期：成功。若命令不可用，记录为环境缺失。

- [ ] **步骤 5：生成一次 fixture HTML 报告**

运行：

```bash
python - <<'PY'
import json
import shutil
from pathlib import Path
from sts2_autotest.report_html import write_html_report

out = Path("tests/output/visual-qa-fixture")
(out / "screenshots").mkdir(parents=True, exist_ok=True)
shutil.copy2("tests/fixtures/visual_qa/gawain-card-before.png", out / "screenshots/gawain-card-before.png")
config = {
    "test_run_id": "visual-qa-fixture",
    "test_cases": [{
        "id": "Card Smoke Test",
        "name": "Card Smoke Test",
        "result": "通过",
        "steps": [],
        "card_results": [{
            "card_id": "GAWAINMOD-STRIKE_GAWAIN",
            "name": "打击",
            "result": "通过",
            "screenshot_before": "screenshots/gawain-card-before.png",
            "screenshot_before_ocr": {
                "status": "passed",
                "provider": "static",
                "findings": []
            }
        }]
    }],
    "card_results": [],
}
config_path = out / "test-results.json"
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
write_html_report(config_path, out / "test-report.html")
print(out / "test-report.html")
PY
```

预期：命令输出 `tests/output/visual-qa-fixture/test-report.html`，该文件存在，并包含 `OCR 辅助分析`。

- [ ] **步骤 6：最终提交验证记录**

如果任务 7 产生了文档或测试 fixture 之外的必要变更，提交它们。不要提交 `tests/output/visual-qa-fixture/` 运行产物，除非仓库已有规则要求保存该产物。

```bash
git status --short
```

预期：没有非预期改动；如有 `tests/output/` 产物，删除或确认已被 `.gitignore` 忽略。
