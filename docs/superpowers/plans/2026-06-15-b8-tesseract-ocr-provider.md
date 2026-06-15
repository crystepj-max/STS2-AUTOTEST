# B8 Tesseract OCR Provider 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 B8 Visual QA Engine 增加可选的 Tesseract OCR provider，让 HTML 报告能在本机安装 Tesseract 时分析真实截图文本。

**架构：** 在 `core/visual_qa.py` 中新增 `TesseractOcrProvider`，通过系统 `tesseract` 命令输出文本行。`FrameworkConfig` 增加 Visual QA OCR 配置，`TestAgentRunner` 根据配置选择 `disabled` 或 `tesseract` provider，并保持测试注入的 `_visual_qa_engine` 优先。

**技术栈：** Python 3.11+、subprocess、Pydantic v2、pytest、mock、import-linter；不新增 Python OCR 依赖。

---

## 文件结构

- 修改：`src/sts2_autotest/core/visual_qa.py`
  - 新增 `TesseractOcrProvider`。
- 修改：`src/sts2_autotest/config/schema.py`
  - 在 `FrameworkConfig` 增加 Visual QA OCR 配置字段。
- 修改：`src/sts2_autotest/core/test_agent_runner.py`
  - `_get_visual_qa_engine()` 按配置构造 provider。
- 修改：`tests/unit/test_visual_qa.py`
  - 覆盖 Tesseract provider 正常输出、命令缺失、超时、非零退出。
- 修改：`tests/unit/test_config_schema.py`
  - 覆盖配置默认值和校验。
- 修改：`tests/unit/test_config_loader.py`
  - 覆盖环境变量覆盖 OCR 配置。
- 修改：`tests/unit/test_agent_runner_visual_qa.py`
  - 覆盖 runner provider 选择。
- 创建：`tests/integration/test_visual_qa_tesseract.py`
  - 本机存在 tesseract 时执行真实 OCR fixture；否则 skip。
- 修改：`docs/user-manual.md`
  - 增加配置说明。

## 任务 1：新增 Tesseract OCR provider

**文件：**
- 修改：`tests/unit/test_visual_qa.py`
- 修改：`src/sts2_autotest/core/visual_qa.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/unit/test_visual_qa.py` 增加：

```python
import subprocess
from unittest.mock import patch

from sts2_autotest.core.visual_qa import TesseractOcrProvider


def test_tesseract_provider_extracts_stdout_lines(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")

    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=0,
        stdout="便携魔导终端\\n消耗 1 储能\\n\\n",
        stderr="",
    )
    with patch("sts2_autotest.core.visual_qa.subprocess.run", return_value=completed) as run:
        blocks = TesseractOcrProvider(
            command="tesseract",
            lang="chi_sim+eng",
            timeout_seconds=3.0,
        ).extract_text(image)

    assert [block.text for block in blocks] == ["便携魔导终端", "消耗 1 储能"]
    run.assert_called_once_with(
        ["tesseract", str(image), "stdout", "-l", "chi_sim+eng"],
        capture_output=True,
        text=True,
        timeout=3.0,
        check=False,
    )
```

- [ ] **步骤 2：运行测试验证失败**

```bash
.venv/bin/python -m pytest tests/unit/test_visual_qa.py::test_tesseract_provider_extracts_stdout_lines -q
```

预期：FAIL，`ImportError` 或 `NameError` 指向 `TesseractOcrProvider` 未实现。

- [ ] **步骤 3：实现 provider**

在 `src/sts2_autotest/core/visual_qa.py` 中：

```python
import subprocess


class TesseractOcrProvider:
    """OCR provider backed by the local tesseract command."""

    name = "tesseract"

    def __init__(
        self,
        command: str = "tesseract",
        lang: str = "chi_sim+eng",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._command = command
        self._lang = lang
        self._timeout_seconds = timeout_seconds

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]:
        try:
            completed = subprocess.run(
                [self._command, str(image_path), "stdout", "-l", self._lang],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"tesseract command not found: {self._command}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"tesseract timed out after {self._timeout_seconds} seconds"
            ) from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise RuntimeError(f"tesseract failed: {message}")
        return [
            OcrTextBlock(text=line)
            for line in (line.strip() for line in completed.stdout.splitlines())
            if line
        ]
```

- [ ] **步骤 4：增加错误路径测试**

在 `tests/unit/test_visual_qa.py` 增加：

```python
def test_tesseract_provider_missing_command_becomes_skipped(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    engine = VisualQaEngine(TesseractOcrProvider(command="missing-tesseract"))

    with patch(
        "sts2_autotest.core.visual_qa.subprocess.run",
        side_effect=FileNotFoundError("missing-tesseract"),
    ):
        analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert analysis.provider == "tesseract"
    assert "command not found" in str(analysis.message)


def test_tesseract_provider_timeout_becomes_skipped(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    engine = VisualQaEngine(TesseractOcrProvider(timeout_seconds=0.1))

    with patch(
        "sts2_autotest.core.visual_qa.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=0.1),
    ):
        analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert "timed out" in str(analysis.message)


def test_tesseract_provider_nonzero_exit_becomes_skipped(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    completed = subprocess.CompletedProcess(
        args=["tesseract"],
        returncode=1,
        stdout="",
        stderr="Error opening data file",
    )
    engine = VisualQaEngine(TesseractOcrProvider())

    with patch("sts2_autotest.core.visual_qa.subprocess.run", return_value=completed):
        analysis = engine.analyze_screenshot(image)

    assert analysis.status == "skipped"
    assert "tesseract failed" in str(analysis.message)
```

- [ ] **步骤 5：运行 provider 测试**

```bash
.venv/bin/python -m pytest tests/unit/test_visual_qa.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：Commit**

```bash
git add src/sts2_autotest/core/visual_qa.py tests/unit/test_visual_qa.py
git commit -m "feat: add tesseract OCR provider"
```

## 任务 2：增加配置字段与 env 覆盖

**文件：**
- 修改：`src/sts2_autotest/config/schema.py`
- 修改：`tests/unit/test_config_schema.py`
- 修改：`tests/unit/test_config_loader.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/unit/test_config_schema.py` 的 `TestFrameworkConfig` 中增加：

```python
    def test_visual_qa_defaults(self) -> None:
        cfg = FrameworkConfig()
        assert cfg.visual_qa_enabled is True
        assert cfg.visual_qa_ocr_provider == "disabled"
        assert cfg.visual_qa_tesseract_cmd == "tesseract"
        assert cfg.visual_qa_tesseract_lang == "chi_sim+eng"
        assert cfg.visual_qa_timeout_seconds == 10.0

    def test_visual_qa_provider_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            FrameworkConfig(visual_qa_ocr_provider="easyocr")
```

在 `tests/unit/test_config_loader.py` 的 `TestEnvOverride` 中增加：

```python
    def test_env_overrides_visual_qa_settings(
        self,
        config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STS2_FRAMEWORK__VISUAL_QA_OCR_PROVIDER", "tesseract")
        monkeypatch.setenv("STS2_FRAMEWORK__VISUAL_QA_TESSERACT_LANG", "eng")
        monkeypatch.setenv("STS2_FRAMEWORK__VISUAL_QA_TIMEOUT_SECONDS", "3.5")

        cfg = load_config(project_dir=config_dir)

        assert cfg.framework.visual_qa_ocr_provider == "tesseract"
        assert cfg.framework.visual_qa_tesseract_lang == "eng"
        assert cfg.framework.visual_qa_timeout_seconds == 3.5
```

- [ ] **步骤 2：运行配置测试验证失败**

```bash
.venv/bin/python -m pytest tests/unit/test_config_schema.py::TestFrameworkConfig::test_visual_qa_defaults tests/unit/test_config_loader.py::TestEnvOverride::test_env_overrides_visual_qa_settings -q
```

预期：FAIL，字段不存在。

- [ ] **步骤 3：实现配置字段**

在 `FrameworkConfig` 增加：

```python
    visual_qa_enabled: bool = True
    visual_qa_ocr_provider: Literal["disabled", "tesseract"] = "disabled"
    visual_qa_tesseract_cmd: str = "tesseract"
    visual_qa_tesseract_lang: str = "chi_sim+eng"
    visual_qa_timeout_seconds: float = Field(default=10.0, gt=0)
```

- [ ] **步骤 4：运行配置测试**

```bash
.venv/bin/python -m pytest tests/unit/test_config_schema.py tests/unit/test_config_loader.py -q
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/sts2_autotest/config/schema.py tests/unit/test_config_schema.py tests/unit/test_config_loader.py
git commit -m "feat: configure visual QA OCR provider"
```

## 任务 3：Runner 按配置选择 provider

**文件：**
- 修改：`src/sts2_autotest/core/test_agent_runner.py`
- 修改：`tests/unit/test_agent_runner_visual_qa.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/unit/test_agent_runner_visual_qa.py` 增加：

```python
from sts2_autotest.config.schema import FrameworkConfig
from sts2_autotest.core.visual_qa import DisabledOcrProvider, TesseractOcrProvider


def test_get_visual_qa_engine_defaults_to_disabled_provider(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)

    engine = runner._get_visual_qa_engine()

    assert isinstance(engine._provider, DisabledOcrProvider)


def test_get_visual_qa_engine_uses_tesseract_provider_from_config(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    runner._framework_config = FrameworkConfig(
        visual_qa_ocr_provider="tesseract",
        visual_qa_tesseract_cmd="custom-tesseract",
        visual_qa_tesseract_lang="eng",
        visual_qa_timeout_seconds=2.0,
    )

    engine = runner._get_visual_qa_engine()

    assert isinstance(engine._provider, TesseractOcrProvider)
```

- [ ] **步骤 2：运行 runner 测试验证失败**

```bash
.venv/bin/python -m pytest tests/unit/test_agent_runner_visual_qa.py::test_get_visual_qa_engine_uses_tesseract_provider_from_config -q
```

预期：FAIL，provider 仍是 disabled 或无法导入。

- [ ] **步骤 3：实现 runner provider 选择**

在 `test_agent_runner.py` 导入：

```python
from sts2_autotest.config.schema import FrameworkConfig
from sts2_autotest.core.visual_qa import (
    DisabledOcrProvider,
    TesseractOcrProvider,
    VisualQaEngine,
)
```

更新 `_get_visual_qa_engine()`：

```python
    def _get_visual_qa_engine(self) -> VisualQaEngine:
        engine = getattr(self, "_visual_qa_engine", None)
        if isinstance(engine, VisualQaEngine):
            return engine

        framework_config = getattr(self, "_framework_config", FrameworkConfig())
        provider_name = getattr(framework_config, "visual_qa_ocr_provider", "disabled")
        if not getattr(framework_config, "visual_qa_enabled", True):
            provider = DisabledOcrProvider()
        elif provider_name == "tesseract":
            provider = TesseractOcrProvider(
                command=framework_config.visual_qa_tesseract_cmd,
                lang=framework_config.visual_qa_tesseract_lang,
                timeout_seconds=framework_config.visual_qa_timeout_seconds,
            )
        else:
            provider = DisabledOcrProvider()

        engine = VisualQaEngine(provider)
        self._visual_qa_engine = engine
        return engine
```

- [ ] **步骤 4：运行 runner 测试**

```bash
.venv/bin/python -m pytest tests/unit/test_agent_runner_visual_qa.py tests/unit/test_smoke_card_validation.py -q
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add src/sts2_autotest/core/test_agent_runner.py tests/unit/test_agent_runner_visual_qa.py
git commit -m "feat: select visual QA OCR provider from config"
```

## 任务 4：增加本机可选集成测试

**文件：**
- 创建：`tests/integration/test_visual_qa_tesseract.py`

- [ ] **步骤 1：编写集成测试**

创建文件：

```python
"""Optional integration tests for the local Tesseract OCR provider."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sts2_autotest.core.visual_qa import TesseractOcrProvider, VisualQaEngine


def test_tesseract_provider_reads_game_screenshot_fixture() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract command is not installed")

    fixture = Path("tests/fixtures/visual_qa/gawain-card-before.png")
    assert fixture.is_file()

    analysis = VisualQaEngine(TesseractOcrProvider()).analyze_screenshot(fixture)

    assert analysis.provider == "tesseract"
    assert analysis.status in {"passed", "warning"}
    assert analysis.extracted_text
```

- [ ] **步骤 2：运行集成测试**

```bash
.venv/bin/python -m pytest tests/integration/test_visual_qa_tesseract.py -q
```

预期：本机有 tesseract 时 PASS；没有时 `1 skipped`。

- [ ] **步骤 3：Commit**

```bash
git add tests/integration/test_visual_qa_tesseract.py
git commit -m "test: add optional tesseract OCR integration test"
```

## 任务 5：文档更新

**文件：**
- 修改：`docs/user-manual.md`

- [ ] **步骤 1：更新用户手册**

在 OCR 辅助分析段落后追加：

```markdown
可通过配置启用本机 Tesseract OCR：

```dotenv
STS2_FRAMEWORK__VISUAL_QA_OCR_PROVIDER=tesseract
STS2_FRAMEWORK__VISUAL_QA_TESSERACT_CMD=tesseract
STS2_FRAMEWORK__VISUAL_QA_TESSERACT_LANG=chi_sim+eng
STS2_FRAMEWORK__VISUAL_QA_TIMEOUT_SECONDS=10
```

Tesseract 和语言包不是 STS2-AUTOTEST 的硬依赖。未安装命令、缺少语言包或执行超时时，OCR 分析会显示为未执行，不改变测试结果。
```

- [ ] **步骤 2：运行文档检查**

```bash
rg -n "Tesseract|VISUAL_QA|OCR 辅助" docs/user-manual.md
```

预期：能找到新增说明。

- [ ] **步骤 3：Commit**

```bash
git add docs/user-manual.md
git commit -m "docs: document tesseract visual QA OCR configuration"
```

## 任务 6：最终验证

**文件：**
- 验证全部相关改动。

- [ ] **步骤 1：运行目标单元测试**

```bash
.venv/bin/python -m pytest \
  tests/unit/test_visual_qa.py \
  tests/unit/test_report_html.py \
  tests/unit/test_agent_runner_visual_qa.py \
  tests/unit/test_config_schema.py \
  tests/unit/test_config_loader.py \
  -q
```

预期：全部 PASS。

- [ ] **步骤 2：运行相关回归**

```bash
.venv/bin/python -m pytest \
  tests/unit/test_smoke_card_validation.py \
  tests/unit/test_common_evidence.py \
  tests/unit/test_run_test_agent_report.py \
  -q
```

预期：全部 PASS。

- [ ] **步骤 3：运行可选集成测试**

```bash
.venv/bin/python -m pytest tests/integration/test_visual_qa_tesseract.py -q
```

预期：PASS 或 `1 skipped`。

- [ ] **步骤 4：运行静态检查**

```bash
.venv/bin/python -m mypy \
  src/sts2_autotest/common/visual_qa.py \
  src/sts2_autotest/core/visual_qa.py \
  src/sts2_autotest/report_html.py \
  --follow-imports=silent
.venv/bin/lint-imports
```

预期：两者通过。全仓 mypy 当前存在既有错误，可单独记录，不在本任务内修复。

- [ ] **步骤 5：确认工作区状态**

```bash
git status --short --branch
```

预期：没有非预期改动。
