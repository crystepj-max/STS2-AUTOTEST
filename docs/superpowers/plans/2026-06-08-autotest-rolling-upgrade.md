# 统一滚动升级实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 STS2-AUTOTEST 落地统一滚动升级的最小实现，确保所有核心测试产物记录当前 autotest 版本，并把平台升级导致的旧项目不可运行与业务失败区分开。

**架构：** 版本信息统一从 `src/sts2_autotest/__init__.py` 的 `__version__` 读取，沿着 Evidence 数据模型、打包器和 PowerShell 测试报告三条主链路写入产物。兼容性问题先以标准化阻塞原因写入报告与摘要，暂不引入 lockfile、多版本运行或 launcher。

**技术栈：** Python、Pydantic、pytest、PowerShell

---

## 文件结构

- 修改：`src/sts2_autotest/common/evidence.py`
  - 为 `RunInfo` / `SummaryJson` / `EvidencePack` 增加 autotest 版本与兼容性阻塞字段，作为统一数据模型。
- 修改：`src/sts2_autotest/evidence/packager.py`
  - 在创建 `summary.json`、刷新 `summary.md`、读取 pack 时贯通新字段，并把版本与阻塞原因写进 Markdown 报告。
- 修改：`scripts/run-test-agent.ps1`
  - 在 Test Agent 输出的 `test-report.md` 中写入 autotest 版本，并为平台兼容性阻塞预留统一原因文案。
- 修改：`tests/unit/test_common_evidence.py`
  - 覆盖新字段默认值、序列化与 roundtrip 行为。
- 修改：`tests/unit/test_packager.py`
  - 覆盖 `summary.json` 和 `summary.md` 中的版本可观测性，以及兼容性阻塞信息渲染。
- 新增：`tests/unit/test_run_test_agent_report.py`
  - 验证 PowerShell 报告模板包含 autotest 版本和兼容性阻塞约定文本。
- 修改：`docs/user-manual.md`
  - 补充统一滚动升级下的版本可观测性与兼容性阻塞说明。

## 任务 1：扩展 Evidence 数据模型，承载版本与兼容性阻塞

**文件：**
- 修改：`src/sts2_autotest/common/evidence.py`
- 测试：`tests/unit/test_common_evidence.py`

- [x] **步骤 1：编写失败的测试**

```python
def test_summary_json_carries_autotest_version() -> None:
    summary = SummaryJson(
        pack_id="run_demo",
        test_run=RunInfo(
            run_id="run_demo",
            result="blocked",
            duration_ms=0,
            autotest_version="0.1.0",
        ),
        environment=EnvironmentInfo(
            framework="sts2-autotest",
            adapter="agent",
            game="Slay the Spire 2",
            os="macOS",
            python="3.11.0",
        ),
    )
    assert summary.test_run.autotest_version == "0.1.0"


def test_summary_json_roundtrip_preserves_compatibility_block_reason() -> None:
    summary = SummaryJson(
        pack_id="run_blocked",
        test_run=RunInfo(
            run_id="run_blocked",
            result="blocked",
            duration_ms=0,
            autotest_version="0.1.0",
        ),
        environment=EnvironmentInfo(
            framework="sts2-autotest",
            adapter="agent",
            game="Slay the Spire 2",
            os="macOS",
            python="3.11.0",
        ),
        compatibility_block_reason="autotest_compatibility_blocked",
    )
    restored = SummaryJson.model_validate(summary.model_dump(mode="json"))
    assert restored.compatibility_block_reason == "autotest_compatibility_blocked"
```

- [x] **步骤 2：运行测试验证失败**

运行：`pytest tests/unit/test_common_evidence.py -k "autotest_version or compatibility_block_reason" -v`
预期：FAIL，报错 `RunInfo` 或 `SummaryJson` 不接受 `autotest_version`、`compatibility_block_reason`

- [x] **步骤 3：编写最少实现代码**

```python
class RunInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    result: str
    duration_ms: int = Field(ge=0)
    autotest_version: str


class SummaryJson(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    pack_id: str
    test_run: RunInfo
    environment: EnvironmentInfo
    artifacts: ArtifactsInfo = ArtifactsInfo()
    failure: FailureInfo | None = None
    artifact_path: str | None = None
    repair_report: RepairReport | None = None
    compatibility_block_reason: str | None = None
```

- [x] **步骤 4：运行测试验证通过**

运行：`pytest tests/unit/test_common_evidence.py -k "autotest_version or compatibility_block_reason" -v`
预期：PASS

- [x] **步骤 5：Commit**

```bash
git add src/sts2_autotest/common/evidence.py tests/unit/test_common_evidence.py
git commit -m "feat: add autotest version to evidence models"
```

## 任务 2：把 autotest 版本写入 summary.json 和 summary.md

**文件：**
- 修改：`src/sts2_autotest/evidence/packager.py`
- 测试：`tests/unit/test_packager.py`

- [x] **步骤 1：编写失败的测试**

```python
def test_create_pack_writes_autotest_version_to_summary_json(tmp_path: Path) -> None:
    packager = EvidencePackager(tmp_path)
    pack_dir = packager.create_pack("run_versioned")
    data = json.loads((pack_dir / "summary.json").read_text(encoding="utf-8"))
    assert data["test_run"]["autotest_version"] == __version__


def test_generate_report_includes_autotest_version(tmp_path: Path) -> None:
    packager = EvidencePackager(tmp_path)
    packager.create_pack("run_versioned")
    content = (tmp_path / "run_versioned" / "summary.md").read_text(encoding="utf-8")
    assert f"- **Autotest Version:** {__version__}" in content
```

- [x] **步骤 2：运行测试验证失败**

运行：`pytest tests/unit/test_packager.py -k "autotest_version" -v`
预期：FAIL，`summary.json` 中缺少 `test_run.autotest_version`，`summary.md` 中缺少版本行

- [x] **步骤 3：编写最少实现代码**

```python
from sts2_autotest import __version__

summary = SummaryJson(
    pack_id=pack_id,
    test_run=RunInfo(
        run_id=pack_id,
        result=run_result,
        duration_ms=duration_ms,
        autotest_version=__version__,
    ),
    environment=EnvironmentInfo(
        framework=self._framework,
        adapter=self._adapter,
        game=self._game,
        os=platform.platform(),
        python=platform.python_version(),
    ),
    failure=failure,
)

lines.append(f"- **Autotest Version:** {run.autotest_version}")
```

- [x] **步骤 4：运行测试验证通过**

运行：`pytest tests/unit/test_packager.py -k "autotest_version" -v`
预期：PASS

- [x] **步骤 5：Commit**

```bash
git add src/sts2_autotest/evidence/packager.py tests/unit/test_packager.py
git commit -m "feat: record autotest version in evidence packs"
```

## 任务 3：在报告中区分平台兼容性阻塞与业务失败

**文件：**
- 修改：`src/sts2_autotest/evidence/packager.py`
- 修改：`tests/unit/test_packager.py`

- [x] **步骤 1：编写失败的测试**

```python
def test_generate_report_includes_compatibility_block_reason(tmp_path: Path) -> None:
    packager = EvidencePackager(tmp_path)
    pack_dir = packager.create_pack("run_blocked", run_result="blocked")
    summary_path = pack_dir / "summary.json"
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    data["compatibility_block_reason"] = "autotest_compatibility_blocked"
    summary_path.write_text(json.dumps(data), encoding="utf-8")

    packager.generate_report("run_blocked")
    content = (pack_dir / "summary.md").read_text(encoding="utf-8")
    assert "- **Compatibility Block Reason:** autotest_compatibility_blocked" in content
    assert "This run was blocked by STS2-AUTOTEST platform compatibility" in content
```

- [x] **步骤 2：运行测试验证失败**

运行：`pytest tests/unit/test_packager.py -k "compatibility_block_reason" -v`
预期：FAIL，`summary.md` 中没有兼容性阻塞原因与解释文案

- [x] **步骤 3：编写最少实现代码**

```python
if summary.compatibility_block_reason is not None:
    lines.append(f"- **Compatibility Block Reason:** {summary.compatibility_block_reason}")
    lines.append("")
    lines.append(
        "This run was blocked by STS2-AUTOTEST platform compatibility, "
        "not by the MOD project's business logic."
    )
    lines.append("")
```

- [x] **步骤 4：运行测试验证通过**

运行：`pytest tests/unit/test_packager.py -k "compatibility_block_reason" -v`
预期：PASS

- [x] **步骤 5：Commit**

```bash
git add src/sts2_autotest/evidence/packager.py tests/unit/test_packager.py
git commit -m "feat: surface platform compatibility blocks in reports"
```

## 任务 4：让 Test Agent Markdown 报告带上 autotest 版本与统一阻塞约定

**文件：**
- 修改：`scripts/run-test-agent.ps1`
- 新增：`tests/unit/test_run_test_agent_report.py`

- [x] **步骤 1：编写失败的测试**

```python
def test_run_test_agent_report_mentions_autotest_version() -> None:
    content = Path("scripts/run-test-agent.ps1").read_text(encoding="utf-8")
    assert "Autotest version:" in content


def test_run_test_agent_report_mentions_platform_compatibility_block() -> None:
    content = Path("scripts/run-test-agent.ps1").read_text(encoding="utf-8")
    assert "autotest_compatibility_blocked" in content
```

- [x] **步骤 2：运行测试验证失败**

运行：`pytest tests/unit/test_run_test_agent_report.py -v`
预期：FAIL，脚本模板中缺少 `Autotest version:` 和 `autotest_compatibility_blocked`

- [x] **步骤 3：编写最少实现代码**

```powershell
$autotestVersion = "0.1.0"

- Autotest version: $autotestVersion

- BLOCKED：若原因为 autotest_compatibility_blocked，优先交回 STS2-AUTOTEST 平台侧补兼容。
```

说明：实现时不要硬编码最终版本值。应从 Python 包版本或单一受控来源读取，保证与 `src/sts2_autotest/__init__.py` 一致。

- [x] **步骤 4：运行测试验证通过**

运行：`pytest tests/unit/test_run_test_agent_report.py -v`
预期：PASS

- [x] **步骤 5：Commit**

```bash
git add scripts/run-test-agent.ps1 tests/unit/test_run_test_agent_report.py
git commit -m "feat: add autotest version to test agent report"
```

## 任务 5：补充用户手册并完成回归验证

**文件：**
- 修改：`docs/user-manual.md`
- 测试：`tests/unit/test_common_evidence.py`
- 测试：`tests/unit/test_packager.py`
- 测试：`tests/unit/test_run_test_agent_report.py`

- [x] **步骤 1：补充文档变更**

```markdown
### 版本可观测性

所有核心测试产物都会记录当前 `autotest version`，用于区分平台升级影响与业务回归。

### 平台兼容性阻塞

若报告中出现 `autotest_compatibility_blocked`，表示运行被 STS2-AUTOTEST 平台升级兼容性阻塞，而非被测 MOD 业务逻辑失败。
```

- [x] **步骤 2：运行针对性测试**

运行：`pytest tests/unit/test_common_evidence.py tests/unit/test_packager.py tests/unit/test_run_test_agent_report.py -v`
预期：PASS

- [x] **步骤 3：运行更高层回归**

运行：`pytest tests/unit/test_mcp_tools.py tests/unit/test_mcp_server.py -v`
预期：PASS，证明 summary/report 新字段未破坏现有 MCP 报告读取路径

- [x] **步骤 4：检查工作区改动**

运行：`git status --short`
预期：仅看到本计划涉及的源码、测试与文档文件改动；不回退已有的 `.vscode/` 或其他用户改动

- [x] **步骤 5：Commit**

```bash
git add docs/user-manual.md tests/unit/test_common_evidence.py tests/unit/test_packager.py tests/unit/test_run_test_agent_report.py src/sts2_autotest/common/evidence.py src/sts2_autotest/evidence/packager.py scripts/run-test-agent.ps1
git commit -m "feat: add rolling upgrade observability"
```
