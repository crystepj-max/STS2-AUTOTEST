# B8 OCR + OpenCV 稳定版实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 B8 收口为稳定可用的离线 OCR + OpenCV 版本：Tesseract OCR 可验真实游戏截图，OpenCV 健康检查覆盖低方差、过暗、过亮、不可读图片，主 runner、单图 CLI、`visual-qa.json`、HTML 报告和文档保持一致。

**架构：** 继续沿用现有 `core/visual_qa.py` 作为 Visual QA 引擎核心，不引入 VLM。OpenCV 规则仍由 `ScreenshotHealthDetector` 产出非阻断 `VisualQaFinding`；配置通过 `FrameworkConfig` 注入 runner，单图 CLI 直接使用同一 detector 参数，报告和 `visual-qa.json` 复用既有 payload 结构。

**技术栈：** Python 3.11+、Pydantic v2、pytest、ruff、Tesseract CLI、`opencv-python-headless` 可选依赖、现有 argparse CLI。

---

## 范围

本计划包含：

1. OpenCV 健康规则补齐到稳定可用版本：`low_variance`、`too_dark`、`too_bright`、`unreadable`。
2. 亮度阈值默认值统一放入 `common/visual_qa.py`，避免 config、CLI、core 三处漂移。
3. runner 主链路、单图 CLI、配置 schema 使用同一组阈值语义。
4. 增加单图 CLI 真实 fixture 验收，证明用户提供的游戏截图可通过 OCR + OpenCV 分析。
5. 更新用户文档和路线图状态，把 B8 标为 OCR + OpenCV 稳定版，VLM 明确保留为后续扩展。

本计划不包含：

1. VLM provider。
2. 云端 OCR。
3. 把 Visual QA warning 升级为测试失败。
4. 批量目录扫描 CLI。
5. HTML 报告视觉重设计。

## 文件结构

- 修改：`src/sts2_autotest/common/visual_qa.py`
  - 职责：Visual QA 共享模型和默认阈值常量。
- 修改：`src/sts2_autotest/core/visual_qa.py`
  - 职责：OCR provider、localization detector、OpenCV 健康规则、payload summary。
- 修改：`src/sts2_autotest/config/schema.py`
  - 职责：框架配置字段和 Pydantic 校验。
- 修改：`src/sts2_autotest/core/test_agent_runner.py`
  - 职责：从 `FrameworkConfig` 读取 Visual QA 参数并生成报告产物。
- 修改：`src/sts2_autotest/cli/main.py`
  - 职责：`autotest visual-qa` 单图分析命令。
- 修改：`tests/unit/test_visual_qa.py`
  - 职责：OpenCV 健康规则、OCR parser、payload summary 的单元测试。
- 修改：`tests/integration/test_visual_qa_opencv.py`
  - 职责：真实 OpenCV 图像规则集成测试。
- 修改：`tests/integration/test_visual_qa_tesseract.py`
  - 职责：真实 Tesseract OCR 游戏截图集成测试。
- 修改：`tests/unit/test_config_schema.py`
  - 职责：Visual QA 配置默认值和字段校验测试。
- 修改：`tests/unit/test_config_loader.py`
  - 职责：环境变量覆盖 Visual QA 配置测试。
- 修改：`tests/unit/test_agent_runner_visual_qa.py`
  - 职责：runner 对 Visual QA 参数和产物的接线测试。
- 修改：`tests/unit/test_cli.py`
  - 职责：`autotest visual-qa` 参数解析、输出文件、JSON payload 测试。
- 修改：`docs/user-manual.md`
  - 职责：用户可执行命令、配置字段、报告产物说明。
- 修改：`docs/beta-roadmap.md`
  - 职责：B8 状态从 MVP 更新到 OCR + OpenCV 稳定版，VLM 标为后续扩展。

---

### 任务 1：统一 OpenCV 阈值默认值

**文件：**
- 修改：`src/sts2_autotest/common/visual_qa.py`
- 修改：`src/sts2_autotest/core/visual_qa.py`
- 修改：`src/sts2_autotest/config/schema.py`
- 修改：`src/sts2_autotest/cli/main.py`
- 测试：`tests/unit/test_config_schema.py`
- 测试：`tests/unit/test_cli.py`

- [ ] **步骤 1：编写失败的配置默认值测试**

在 `tests/unit/test_config_schema.py` 的 import 中加入：

```python
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_VARIANCE_THRESHOLD,
)
```

在 `TestFrameworkConfig.test_visual_qa_defaults` 中补充：

```python
assert cfg.visual_qa_low_brightness_threshold == DEFAULT_LOW_BRIGHTNESS_THRESHOLD
assert cfg.visual_qa_high_brightness_threshold == DEFAULT_HIGH_BRIGHTNESS_THRESHOLD
```

新增测试：

```python
def test_visual_qa_high_brightness_threshold_must_be_positive(self) -> None:
    with pytest.raises(ValidationError):
        FrameworkConfig(visual_qa_high_brightness_threshold=0)
```

- [ ] **步骤 2：编写失败的 CLI 默认值测试**

在 `tests/unit/test_cli.py::TestCLIParser.test_visual_qa_command_parses` 中补充：

```python
assert args.low_brightness_threshold == DEFAULT_LOW_BRIGHTNESS_THRESHOLD
assert args.high_brightness_threshold == DEFAULT_HIGH_BRIGHTNESS_THRESHOLD
```

并在文件 import 中加入：

```python
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
)
```

- [ ] **步骤 3：运行测试确认失败**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_config_schema.py::TestFrameworkConfig::test_visual_qa_defaults \
  tests/unit/test_config_schema.py::TestFrameworkConfig::test_visual_qa_high_brightness_threshold_must_be_positive \
  tests/unit/test_cli.py::TestCLIParser::test_visual_qa_command_parses \
  -q
```

预期：FAIL。失败信息包含 `ImportError`、`AttributeError` 或 argparse 缺少 `high_brightness_threshold`。

- [ ] **步骤 4：实现默认阈值常量和配置字段**

在 `src/sts2_autotest/common/visual_qa.py` 中加入：

```python
DEFAULT_LOW_BRIGHTNESS_THRESHOLD = 5.0
DEFAULT_HIGH_BRIGHTNESS_THRESHOLD = 250.0
```

在 `src/sts2_autotest/config/schema.py` 中改为：

```python
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_VARIANCE_THRESHOLD,
)
```

并在 `FrameworkConfig` 中加入：

```python
visual_qa_low_brightness_threshold: float = Field(
    default=DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    gt=0,
)
visual_qa_high_brightness_threshold: float = Field(
    default=DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    gt=0,
)
```

在 `src/sts2_autotest/core/visual_qa.py` 中从 common 引入两个新常量，并让 `ScreenshotHealthDetector.__init__` 默认值使用 common 常量：

```python
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD as _DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD as _DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_VARIANCE_THRESHOLD as _DEFAULT_LOW_VARIANCE_THRESHOLD,
    OcrTextBlock,
    ScreenshotOcrAnalysis,
    VisualQaFinding,
)

DEFAULT_LOW_BRIGHTNESS_THRESHOLD = _DEFAULT_LOW_BRIGHTNESS_THRESHOLD
DEFAULT_HIGH_BRIGHTNESS_THRESHOLD = _DEFAULT_HIGH_BRIGHTNESS_THRESHOLD
```

在 `ScreenshotHealthDetector.__init__` 中使用：

```python
low_brightness_threshold: float = DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
high_brightness_threshold: float = DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
```

在 `src/sts2_autotest/cli/main.py` 中导入常量：

```python
from sts2_autotest.common.visual_qa import (
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_BRIGHTNESS_THRESHOLD,
    DEFAULT_LOW_VARIANCE_THRESHOLD,
)
```

并把 CLI 默认值改为：

```python
default=DEFAULT_LOW_VARIANCE_THRESHOLD
default=DEFAULT_LOW_BRIGHTNESS_THRESHOLD
default=DEFAULT_HIGH_BRIGHTNESS_THRESHOLD
```

新增 CLI 参数：

```python
visual_qa_parser.add_argument(
    "--high-brightness-threshold",
    type=float,
    default=DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
    help="High brightness threshold for OpenCV health checks",
)
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_config_schema.py::TestFrameworkConfig::test_visual_qa_defaults \
  tests/unit/test_config_schema.py::TestFrameworkConfig::test_visual_qa_high_brightness_threshold_must_be_positive \
  tests/unit/test_cli.py::TestCLIParser::test_visual_qa_command_parses \
  -q
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add \
  src/sts2_autotest/common/visual_qa.py \
  src/sts2_autotest/core/visual_qa.py \
  src/sts2_autotest/config/schema.py \
  src/sts2_autotest/cli/main.py \
  tests/unit/test_config_schema.py \
  tests/unit/test_cli.py
git commit -m "feat(Visual QA): 统一 OpenCV 阈值默认值"
```

---

### 任务 2：补齐过亮截图规则

**文件：**
- 修改：`src/sts2_autotest/core/visual_qa.py`
- 修改：`src/sts2_autotest/config/schema.py`
- 修改：`src/sts2_autotest/core/test_agent_runner.py`
- 修改：`src/sts2_autotest/cli/main.py`
- 测试：`tests/unit/test_visual_qa.py`
- 测试：`tests/integration/test_visual_qa_opencv.py`
- 测试：`tests/unit/test_config_loader.py`
- 测试：`tests/unit/test_agent_runner_visual_qa.py`

- [ ] **步骤 1：编写失败的单元测试**

在 `tests/unit/test_visual_qa.py` 中新增：

```python
def test_screenshot_health_detector_flags_bright_image(tmp_path: Path) -> None:
    image = tmp_path / "bright.png"
    image.write_bytes(b"png")

    class FakeImage:
        def std(self) -> float:
            return 10.0

        def mean(self) -> float:
            return 252.5

    class FakeCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> FakeImage:
            return FakeImage()

    detector = ScreenshotHealthDetector(
        cv2_module=FakeCv2,
        low_variance_threshold=1.0,
        low_brightness_threshold=5.0,
        high_brightness_threshold=250.0,
    )

    findings = detector.analyze(image)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.too_bright"
    assert findings[0].severity == "warning"
    assert "too bright" in findings[0].message
    assert findings[0].text == image.name
```

- [ ] **步骤 2：编写失败的 OpenCV 集成测试**

在 `tests/integration/test_visual_qa_opencv.py` 中新增：

```python
def test_opencv_health_detector_flags_too_bright_png(tmp_path) -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")

    image_path = tmp_path / "bright.png"
    image = numpy.zeros((32, 32, 3), dtype=numpy.uint8)
    image[:, :16] = 251
    image[:, 16:] = 253
    assert cv2.imwrite(str(image_path), image)

    findings = ScreenshotHealthDetector(
        cv2_module=cv2,
        low_variance_threshold=0.1,
        low_brightness_threshold=5.0,
        high_brightness_threshold=250.0,
    ).analyze(image_path)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.too_bright"
```

- [ ] **步骤 3：编写失败的配置和 runner 接线测试**

在 `tests/unit/test_config_loader.py::TestEnvOverride.test_env_overrides_visual_qa_settings` 中加入：

```python
monkeypatch.setenv("STS2_FRAMEWORK__VISUAL_QA_HIGH_BRIGHTNESS_THRESHOLD", "248.5")
assert cfg.framework.visual_qa_high_brightness_threshold == 248.5
```

在 `tests/unit/test_agent_runner_visual_qa.py::test_get_visual_qa_engine_configures_health_detector` 中加入：

```python
visual_qa_high_brightness_threshold=248.0,
assert engine._health_detector._high_brightness_threshold == 248.0
```

- [ ] **步骤 4：运行测试确认失败**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_visual_qa.py::test_screenshot_health_detector_flags_bright_image \
  tests/integration/test_visual_qa_opencv.py::test_opencv_health_detector_flags_too_bright_png \
  tests/unit/test_config_loader.py::TestEnvOverride::test_env_overrides_visual_qa_settings \
  tests/unit/test_agent_runner_visual_qa.py::test_get_visual_qa_engine_configures_health_detector \
  -q
```

预期：FAIL。失败原因分别是 `high_brightness_threshold` 未接入或 `visual_health.too_bright` 未产出。

- [ ] **步骤 5：实现过亮规则**

在 `src/sts2_autotest/core/visual_qa.py` 的 `ScreenshotHealthDetector.__init__` 中保存新字段：

```python
self._high_brightness_threshold = high_brightness_threshold
```

在 `ScreenshotHealthDetector.analyze()` 的过暗判断之后加入：

```python
if mean > self._high_brightness_threshold:
    return [
        VisualQaFinding(
            rule_id="visual_health.too_bright",
            severity="warning",
            message=f"Screenshot appears too bright (mean={mean:.3f})",
            text=image_path.name,
            confidence=None,
            bbox=None,
        )
    ]
```

保持健康规则优先级为：

1. `visual_health.low_variance`
2. `visual_health.too_dark`
3. `visual_health.too_bright`

每张截图最多产出一个 OpenCV 健康 finding，避免报告被健康规则刷屏。

- [ ] **步骤 6：接入 config、runner 和 CLI**

在 `src/sts2_autotest/core/test_agent_runner.py` 的 `ScreenshotHealthDetector(...)` 构造中加入：

```python
high_brightness_threshold=getattr(
    framework_config,
    "visual_qa_high_brightness_threshold",
    DEFAULT_HIGH_BRIGHTNESS_THRESHOLD,
),
```

在 `src/sts2_autotest/cli/main.py::_build_visual_qa_engine` 中加入：

```python
high_brightness_threshold=args.high_brightness_threshold,
```

在 `src/sts2_autotest/config/schema.py` 中确认 `visual_qa_high_brightness_threshold` 已由任务 1 加入。

- [ ] **步骤 7：运行测试验证通过**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_visual_qa.py::test_screenshot_health_detector_flags_bright_image \
  tests/integration/test_visual_qa_opencv.py::test_opencv_health_detector_flags_too_bright_png \
  tests/unit/test_config_loader.py::TestEnvOverride::test_env_overrides_visual_qa_settings \
  tests/unit/test_agent_runner_visual_qa.py::test_get_visual_qa_engine_configures_health_detector \
  -q
```

预期：PASS。

- [ ] **步骤 8：Commit**

```bash
git add \
  src/sts2_autotest/core/visual_qa.py \
  src/sts2_autotest/config/schema.py \
  src/sts2_autotest/core/test_agent_runner.py \
  src/sts2_autotest/cli/main.py \
  tests/unit/test_visual_qa.py \
  tests/integration/test_visual_qa_opencv.py \
  tests/unit/test_config_loader.py \
  tests/unit/test_agent_runner_visual_qa.py
git commit -m "feat(Visual QA): 添加过亮截图健康规则"
```

---

### 任务 3：对不可读图片产出明确健康告警

**文件：**
- 修改：`src/sts2_autotest/core/visual_qa.py`
- 测试：`tests/unit/test_visual_qa.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/unit/test_visual_qa.py` 中新增：

```python
def test_screenshot_health_detector_flags_unreadable_image(tmp_path: Path) -> None:
    image = tmp_path / "corrupt.png"
    image.write_bytes(b"not an image")

    class FakeCv2:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path: str, flags: int) -> None:
            return None

    detector = ScreenshotHealthDetector(cv2_module=FakeCv2)

    findings = detector.analyze(image)

    assert len(findings) == 1
    assert findings[0].rule_id == "visual_health.unreadable"
    assert findings[0].severity == "warning"
    assert "not readable" in findings[0].message
    assert findings[0].text == image.name
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_visual_qa.py::test_screenshot_health_detector_flags_unreadable_image \
  -q
```

预期：FAIL，当前实现对 `imread() is None` 返回空列表。

- [ ] **步骤 3：实现 unreadable finding**

在 `src/sts2_autotest/core/visual_qa.py::ScreenshotHealthDetector.analyze` 中把：

```python
if image is None:
    return []
```

改为：

```python
if image is None:
    return [
        VisualQaFinding(
            rule_id="visual_health.unreadable",
            severity="warning",
            message="Screenshot is not readable by OpenCV",
            text=image_path.name,
            confidence=None,
            bbox=None,
        )
    ]
```

异常路径仍然返回空列表，保持“健康检查自身异常不影响 OCR”的既有约束。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_visual_qa.py::test_screenshot_health_detector_flags_unreadable_image \
  tests/unit/test_visual_qa.py::test_visual_qa_engine_keeps_ocr_when_health_detector_fails \
  tests/unit/test_visual_qa.py::test_visual_qa_engine_keeps_ocr_when_health_variance_fails \
  -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/sts2_autotest/core/visual_qa.py tests/unit/test_visual_qa.py
git commit -m "feat(Visual QA): 标记不可读截图健康告警"
```

---

### 任务 4：补齐单图 CLI 的 OCR + OpenCV 真实 fixture 验收

**文件：**
- 修改：`tests/integration/test_visual_qa_tesseract.py`
- 修改：`tests/unit/test_cli.py`
- 可选修改：`src/sts2_autotest/cli/main.py`

- [ ] **步骤 1：编写 CLI 输出文件集成测试**

在 `tests/integration/test_visual_qa_tesseract.py` 中新增：

```python
def test_visual_qa_cli_analyzes_user_screenshot_with_ocr_and_opencv(tmp_path) -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract command is not installed")

    cv2 = pytest.importorskip("cv2")
    assert cv2 is not None

    fixture = Path("tests/fixtures/visual_qa/gawain-card-user-screenshot.jpg")
    assert fixture.is_file()
    output = tmp_path / "visual-qa.json"

    from sts2_autotest.cli.main import _create_parser, visual_qa_cmd

    args = _create_parser().parse_args(
        [
            "visual-qa",
            "--image",
            str(fixture),
            "--ocr-provider",
            "tesseract",
            "--health-provider",
            "opencv",
            "--output",
            str(output),
        ]
    )

    assert visual_qa_cmd(args) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))

    screenshot = payload["screenshots"][str(fixture)]
    assert screenshot["provider"] == "tesseract"
    assert screenshot["status"] == "passed"
    assert screenshot["extracted_text"]
    assert any(block["bbox"] is not None for block in screenshot["extracted_text"])
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["findings_total"] == 0
```

在文件顶部加入：

```python
import json
```

- [ ] **步骤 2：运行测试确认当前行为**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_visual_qa_tesseract.py::test_visual_qa_cli_analyzes_user_screenshot_with_ocr_and_opencv \
  -q
```

预期：PASS。如果失败，失败应集中在 CLI 未写文件、OCR fixture 未产出文本、OpenCV 健康规则误报三类之一。

- [ ] **步骤 3：若测试失败，执行最小修复**

如果失败原因是 CLI 未写文件，确认 `src/sts2_autotest/cli/main.py::visual_qa_cmd` 包含：

```python
if args.output:
    _write_json_output(Path(args.output), payload)
```

如果失败原因是 OpenCV 对用户 fixture 误报，先打印 fixture 灰度指标：

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
import cv2
p = Path("tests/fixtures/visual_qa/gawain-card-user-screenshot.jpg")
img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
print(float(img.std()), float(img.mean()))
PY
```

只有当指标接近阈值时，调整默认阈值；否则修复规则判断。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_visual_qa_tesseract.py::test_visual_qa_cli_analyzes_user_screenshot_with_ocr_and_opencv \
  -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add tests/integration/test_visual_qa_tesseract.py src/sts2_autotest/cli/main.py
git commit -m "test(Visual QA): 验收单图 OCR OpenCV CLI"
```

如果 `src/sts2_autotest/cli/main.py` 未修改，则执行：

```bash
git add tests/integration/test_visual_qa_tesseract.py
git commit -m "test(Visual QA): 验收单图 OCR OpenCV CLI"
```

---

### 任务 5：文档化 OCR + OpenCV 稳定版

**文件：**
- 修改：`docs/user-manual.md`
- 修改：`docs/beta-roadmap.md`

- [ ] **步骤 1：编写用户手册内容**

在 `docs/user-manual.md` 的报告或配置章节附近加入：

```markdown
### Visual QA 截图辅助分析

Visual QA 用于对测试截图生成非阻断辅助结论。当前稳定版包含：

- Tesseract OCR：提取截图文本，并提示 localization key、missing localization 占位、未替换 token。
- OpenCV 健康检查：提示低方差、过暗、过亮、不可读截图。

Visual QA 的 warning 不改变测试结论。报告仍以测试步骤、断言和游戏状态为准。

单张截图分析：

```bash
PYTHONPATH=src .venv/bin/python -m sts2_autotest.cli.main visual-qa \
  --image tests/fixtures/visual_qa/gawain-card-user-screenshot.jpg \
  --ocr-provider tesseract \
  --health-provider opencv \
  --output /tmp/visual-qa.json
```

配置字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `framework.visual_qa_enabled` | `true` | 是否在 runner 报告阶段执行 Visual QA |
| `framework.visual_qa_ocr_provider` | `disabled` | OCR provider，可选 `disabled` / `tesseract` |
| `framework.visual_qa_tesseract_cmd` | `tesseract` | Tesseract 命令路径 |
| `framework.visual_qa_tesseract_lang` | `chi_sim+eng` | OCR 语言包 |
| `framework.visual_qa_timeout_seconds` | `10.0` | OCR 超时时间 |
| `framework.visual_qa_health_enabled` | `true` | 是否启用截图健康检查 |
| `framework.visual_qa_health_provider` | `disabled` | 健康检查 provider，可选 `disabled` / `opencv` |
| `framework.visual_qa_low_variance_threshold` | `1.0` | 低方差阈值 |
| `framework.visual_qa_low_brightness_threshold` | `5.0` | 过暗阈值 |
| `framework.visual_qa_high_brightness_threshold` | `250.0` | 过亮阈值 |
```
```

- [ ] **步骤 2：更新路线图 B8 状态**

在 `docs/beta-roadmap.md` 的 B8 行中把说明改为：

```markdown
| B8 | Visual QA Engine | 已实现（OCR + OpenCV 稳定版） | HTML 报告截图 OCR 辅助分析；`visual-qa.json` 独立产物；单图 CLI；OpenCV 低方差/过暗/过亮/不可读检查；不影响测试结果；VLM 后续扩展 |
```

在对比表“视觉”行中把 BETA 描述改为：

```markdown
| 视觉 | 语法级截图校验（纯色检测） | OCR + OpenCV 截图辅助审查已稳定；VLM 语义审查保留为后续扩展 |
```

- [ ] **步骤 3：运行文档检查**

运行：

```bash
rg -n "Visual QA|visual-qa|visual_qa_high_brightness_threshold|OCR \\+ OpenCV" docs/user-manual.md docs/beta-roadmap.md
```

预期：输出包含新增手册章节、B8 路线图状态和高亮阈值字段。

- [ ] **步骤 4：Commit**

```bash
git add docs/user-manual.md docs/beta-roadmap.md
git commit -m "docs(Visual QA): 记录 OCR OpenCV 稳定版用法"
```

---

### 任务 6：最终聚焦验证

**文件：**
- 不修改文件。

- [ ] **步骤 1：运行 Visual QA 单元和集成测试**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/test_visual_qa.py \
  tests/unit/test_agent_runner_visual_qa.py \
  tests/unit/test_config_schema.py \
  tests/unit/test_config_loader.py \
  tests/unit/test_cli.py -k "visual_qa or opencv_health_detector" \
  tests/integration/test_visual_qa_tesseract.py \
  tests/integration/test_visual_qa_opencv.py \
  -q
```

预期：PASS。允许保留既有 `PytestCollectionWarning`，不得有失败。

- [ ] **步骤 2：运行 ruff**

运行：

```bash
.venv/bin/python -m ruff check \
  src/sts2_autotest/common/visual_qa.py \
  src/sts2_autotest/core/visual_qa.py \
  src/sts2_autotest/config/schema.py \
  src/sts2_autotest/core/test_agent_runner.py \
  src/sts2_autotest/cli/main.py \
  tests/unit/test_visual_qa.py \
  tests/unit/test_agent_runner_visual_qa.py \
  tests/unit/test_config_schema.py \
  tests/unit/test_config_loader.py \
  tests/unit/test_cli.py \
  tests/integration/test_visual_qa_tesseract.py \
  tests/integration/test_visual_qa_opencv.py
```

预期：`All checks passed!`

- [ ] **步骤 3：运行真实单图 CLI smoke**

运行：

```bash
PYTHONPATH=src .venv/bin/python -m sts2_autotest.cli.main visual-qa \
  --image tests/fixtures/visual_qa/gawain-card-user-screenshot.jpg \
  --ocr-provider tesseract \
  --health-provider opencv \
  --output /tmp/sts2-visual-qa-smoke.json
```

预期：命令退出码为 `0`，stdout 是 JSON，`/tmp/sts2-visual-qa-smoke.json` 存在，JSON 中包含：

```json
{
  "summary": {
    "total": 1,
    "passed": 1,
    "findings_total": 0
  }
}
```

- [ ] **步骤 4：检查 git 状态**

运行：

```bash
git status --short --branch
```

预期：只显示当前分支，无未提交文件。

---

## 自检

规格覆盖度：

- OpenCV 规则补齐：任务 1、任务 2、任务 3 覆盖。
- 配置和 runner 接线：任务 1、任务 2 覆盖。
- 单图 CLI 稳定验收：任务 4 覆盖。
- 用户文档和路线图状态：任务 5 覆盖。
- 最终验证：任务 6 覆盖。

占位符扫描：

- 本计划不包含未落地的占位说明。
- 每个修改步骤都有具体文件、代码片段、命令和预期结果。

类型一致性：

- 规则 ID 统一为 `visual_health.low_variance`、`visual_health.too_dark`、`visual_health.too_bright`、`visual_health.unreadable`。
- 配置字段统一为 `visual_qa_low_variance_threshold`、`visual_qa_low_brightness_threshold`、`visual_qa_high_brightness_threshold`。
- CLI 参数统一为 `--low-variance-threshold`、`--low-brightness-threshold`、`--high-brightness-threshold`。
