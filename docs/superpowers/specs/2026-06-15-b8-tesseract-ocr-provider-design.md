# B8 Visual QA Engine - Tesseract OCR Provider 设计

> 类型：设计文档（Design Spec）
> 日期：2026-06-15
> 范围：B8 MVP 第二阶段，在现有 HTML 报告 OCR 辅助分析基础上补齐真实 OCR provider

## 1. 背景

当前 B8 已完成 MVP 第一步：

1. `common/visual_qa.py` 定义截图 OCR 分析模型。
2. `core/visual_qa.py` 提供 `disabled` / `static` provider、localization detector 和 `VisualQaEngine`。
3. `TestAgentRunner` 能把截图级 OCR 结果写入 `test-results.json`。
4. `report_html.py` 能在 HTML 报告截图旁渲染 OCR 辅助分析块。

现阶段 OCR 结果主要来自 `StaticOcrProvider` 或 `DisabledOcrProvider`，缺少真实 OCR provider，导致 HTML 报告虽有展示链路，但默认无法从真实截图提取文本。

本阶段目标是补齐一个可选的、离线优先的真实 OCR provider：Tesseract。

## 2. 目标与非目标

### 2.1 目标

1. 新增 `TesseractOcrProvider`，可对真实截图执行 OCR 文本提取。
2. 通过配置开关控制 provider 选择，默认保持 `disabled`，避免对现有环境和 CI 增加硬依赖。
3. 在未安装 Tesseract、执行超时或执行失败时，OCR 结果继续以 `skipped` 返回，不影响测试结果和 HTML 报告生成。
4. 保持现有 `StaticOcrProvider` 注入方式，单元测试仍不依赖本机 OCR 安装。
5. 增加一个“仅本机有 Tesseract 时才运行”的真实 OCR 集成测试，用用户提供的游戏截图 fixture 验证端到端行为。

### 2.2 非目标

1. 不在本阶段引入 EasyOCR、PaddleOCR 或云端 OCR。
2. 不在本阶段实现 VLM 审查。
3. 不在本阶段实现 OpenCV 规则。
4. 不把 OCR warning 升级为测试失败。
5. 不在本阶段解析坐标框或字符级位置信息。

## 3. 方案选择

### 方案 A：系统命令调用 Tesseract（推荐）

通过 `subprocess.run()` 调用系统 `tesseract` 命令，读取 `stdout` 文本并按行转换为 `OcrTextBlock`。

优点：

1. 依赖简单，安装成本低。
2. 不需要新增 Python OCR 包。
3. 能维持“未安装也不阻断”的运行模型。
4. 最符合当前 MVP 第二阶段“把静态链路推进为真实 OCR 可用”的目标。

缺点：

1. 依赖用户本机安装 Tesseract 和语言包。
2. 输出精度受系统安装版本和语言包影响。
3. 当前阶段只能稳定使用文本行，不适合直接做 bbox 相关功能。

### 方案 B：引入 Python OCR 包

例如 `pytesseract` 或其他 OCR Python 包。

不推荐原因：

1. 额外 Python 依赖更重。
2. 最终仍常常依赖本机 Tesseract。
3. 对当前仓库的 CI、mypy 和依赖管理带来更大变化，但收益有限。

### 方案 C：直接跳到 VLM

不推荐原因：

1. 依赖更复杂。
2. 与当前“先补齐真实 OCR provider”的目标不匹配。
3. 风险和成本都高于当前阶段应承受范围。

## 4. 设计概览

数据流保持不变，只替换 OCR provider 的真实实现来源：

```text
截图路径
  -> VisualQaEngine
  -> TesseractOcrProvider.extract_text()
  -> OCR 文本行
  -> LocalizationTextDetector.analyze()
  -> ScreenshotOcrAnalysis
  -> TestAgentRunner 写入 test-results.json
  -> report_html.py 渲染 HTML 辅助分析块
```

`report_html.py` 和 `LocalizationTextDetector` 本阶段不需要改行为；主要变更点在 provider 实现、配置建模和 runner provider 选择逻辑。

## 5. 模块设计

### 5.1 `core/visual_qa.py`

新增：

```python
class TesseractOcrProvider:
    name = "tesseract"

    def __init__(
        self,
        command: str = "tesseract",
        lang: str = "chi_sim+eng",
        timeout_seconds: float = 10.0,
    ) -> None: ...

    def extract_text(self, image_path: Path) -> list[OcrTextBlock]: ...
```

行为约束：

1. 命令格式固定为：
   `tesseract <image_path> stdout -l <lang>`
2. 使用 `subprocess.run(..., capture_output=True, text=True, timeout=...)`。
3. 只解析 `stdout` 文本，按行 `strip()` 后过滤空行。
4. 每一行输出一个 `OcrTextBlock(text=<line>, confidence=None, bbox=None)`。
5. 出现以下情况时抛出 `RuntimeError`，由 `VisualQaEngine` 统一转为 `skipped`：
   - `FileNotFoundError`：命令不存在
   - `subprocess.TimeoutExpired`：执行超时
   - 非零退出码：执行失败

说明：

本阶段不解析 `tsv`，因为 bbox 和 confidence 会明显扩大实现和测试面。现有 localization 风险检测只依赖文本，行级输出已足够支撑第二阶段目标。

### 5.2 `config/schema.py`

在 `FrameworkConfig` 中新增：

```python
visual_qa_enabled: bool = True
visual_qa_ocr_provider: Literal["disabled", "tesseract"] = "disabled"
visual_qa_tesseract_cmd: str = "tesseract"
visual_qa_tesseract_lang: str = "chi_sim+eng"
visual_qa_timeout_seconds: float = Field(default=10.0, gt=0)
```

说明：

1. 保持放在 `FrameworkConfig`，不额外拆 `VisualQaConfig`，避免当前阶段改动过大。
2. `visual_qa_enabled=True` 表示功能逻辑默认开启，但 provider 默认仍是 `disabled`，所以现有行为不变。
3. 环境变量将遵循现有规则，例如：
   - `STS2_FRAMEWORK__VISUAL_QA_OCR_PROVIDER=tesseract`
   - `STS2_FRAMEWORK__VISUAL_QA_TESSERACT_LANG=chi_sim+eng`

### 5.3 `core/test_agent_runner.py`

扩展 `_get_visual_qa_engine()`：

1. 优先复用测试中显式注入的 `_visual_qa_engine`。
2. 未注入时，读取配置选择 provider：
   - `disabled` -> `DisabledOcrProvider`
   - `tesseract` -> `TesseractOcrProvider`
3. 再用选定 provider 构造 `VisualQaEngine`。

说明：

为了保持改动收敛，本阶段不要求把 Visual QA 配置贯穿到所有 CLI 创建路径。优先采用“如果 runner 已有配置对象则读取；否则回退默认 disabled”的保守策略。

## 6. 错误处理

以下情况都不得让测试执行失败：

1. 用户未安装 Tesseract。
2. 未安装对应语言包。
3. OCR 命令超时。
4. OCR 命令返回非 0。
5. OCR 输出为空。

统一处理原则：

1. provider 抛出 `RuntimeError`。
2. `VisualQaEngine.analyze_screenshot()` 捕获异常并返回：
   - `status="skipped"`
   - `provider="tesseract"`
   - `message="<异常类型>: <异常信息>"`
3. HTML 报告仍正常生成，并显示 `OCR 辅助分析：未执行 - ...`

## 7. 测试策略

### 7.1 单元测试

扩展 `tests/unit/test_visual_qa.py`：

1. mock `subprocess.run()`，验证 stdout 多行被转换为多个 `OcrTextBlock`。
2. `FileNotFoundError` -> engine 返回 `skipped`。
3. `TimeoutExpired` -> engine 返回 `skipped`。
4. `returncode != 0` -> engine 返回 `skipped`。

### 7.2 配置测试

扩展 `tests/unit/test_config_schema.py` 和/或 `tests/unit/test_config_loader.py`：

1. `FrameworkConfig` 默认值正确。
2. 环境变量可覆盖 `visual_qa_ocr_provider` / `visual_qa_tesseract_lang` / `visual_qa_timeout_seconds`。

### 7.3 Runner 测试

扩展 `tests/unit/test_agent_runner_visual_qa.py`：

1. runner 在未注入 engine 时，默认仍走 `disabled`。
2. runner 在提供配置 `visual_qa_ocr_provider=tesseract` 时，会构造 `TesseractOcrProvider`。

### 7.4 本机可选集成测试

新增一个默认跳过的真实 OCR 测试，使用现有 fixture：

`tests/fixtures/visual_qa/gawain-card-before.png`

跳过条件：

1. `shutil.which("tesseract") is None`

验证内容：

1. 能得到至少 1 条 OCR 文本。
2. provider 为 `tesseract`。
3. 返回状态为 `passed` 或 `warning`，但不是 `skipped`。

说明：

不要求对具体中文识别结果做强断言，因为不同本机 OCR 版本、语言包和图像质量会导致文本略有差异。

## 8. 文档更新

更新 `docs/user-manual.md`：

1. 增加 Visual QA OCR provider 配置说明。
2. 增加示例环境变量。
3. 说明未安装 Tesseract 时的行为是 `skipped`，不影响报告生成。

如有必要，可在 `docs/beta-roadmap.md` 保持 B8 为“已实现（MVP）”，无需再改状态，只补充“真实 OCR provider 可选支持”。

## 9. 验收标准

1. 在未安装 Tesseract 的环境中，现有 HTML 报告链路和单元测试保持通过。
2. 配置 `visual_qa_ocr_provider=tesseract` 后，runner 能创建真实 OCR provider。
3. Tesseract 缺失、超时、执行失败时，报告显示 `skipped`，且不改变测试结果。
4. 本机安装 Tesseract 后，用户提供的游戏截图 fixture 可以产生真实 OCR 文本。
5. 本阶段不新增 CI 强依赖，不要求 CI 安装 Tesseract。

## 10. 后续阶段

完成本阶段后，B8 的下一步优先级建议如下：

1. OCR `tsv` 输出解析，补充 bbox / confidence。
2. OpenCV 截图健康规则，如黑屏、纯色、关键区域缺失。
3. VLM 语义审查。
4. 将 OCR / OpenCV / VLM 结果汇总到独立 `visual_qa.json` 产物。
