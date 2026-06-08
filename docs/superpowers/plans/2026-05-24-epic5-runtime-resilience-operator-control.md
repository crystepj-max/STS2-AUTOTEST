# Epic 5 运行健壮性与操作者控制实施计划

> **面向代理执行者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项执行本计划。步骤使用复选框（`- [ ]`）语法跟踪。
> 本计划只覆盖 Epic 5。执行时按 Story 独立提交，每个 Story 结束前运行单元测试、`mypy src/sts2_autotest --strict` 和 `lint-imports`。

**目标：** 完成 Epic 5 的 Beta 运行健壮性与操作者控制能力：弹窗处置、异步 ZIP、队列暂停继续、实时进度暂停继续、场景覆盖报告和四小时无人值守验证。

**架构：** 以现有 Epic 1-4 与 Epic 6 代码为基础，不重做已完成的恢复、进度、队列、证据和真实适配器能力。实现顺序先去除收尾阻塞，再补恢复阻塞点，再补操作者控制和覆盖报告，最后用四小时长跑做验收。所有新增共享模型优先留在所属模块内，只有被三个以上模块引用时才进入 `common/`。

**技术栈：** Python 3.11+、pytest、mypy strict、import-linter、argparse CLI、pydantic v2、stdlib `zipfile` / `threading` / `concurrent.futures`、Windows 本地 Steam / STS2-Cli-Mod 集成环境。

---

## Story 清单与状态

| Story | B 编号 | 状态 | 执行结论 |
|------|--------|------|----------|
| 5.1 崩溃三级恢复 | B1 | done | 已由 `core/recovery.py` 与 `core/orchestrator.py` 支撑，本计划只要求回归验证。 |
| 5.2 弹窗自动处置 | B2 | backlog | 在 5.7 后执行，消除恢复流程中的 Steam/游戏弹窗阻塞。 |
| 5.3 四小时无人值守运行验证 | B3 | backlog | 最后执行，作为 Epic 5 总验收。 |
| 5.4 本地测试队列暂停继续 | B4/B16 | backlog | 在弹窗处置后执行，补齐队列级暂停/继续语义。 |
| 5.5 实时进度暂停继续 | B5 | backlog | 依赖 5.4 的暂停语义和现有 `ProgressRecord`，紧随 5.4。 |
| 5.6 游戏场景覆盖率报告 | B6 | backlog | 依赖运行结果和指标事件，放在长跑前完成。 |
| 5.7 异步 Artifact ZIP 打包 | B18 | backlog | 首先执行，降低后续长跑的会话结束阻塞风险。 |

## 文件职责映射

- Modify: `src/sts2_autotest/evidence/packager.py`：增加异步 ZIP 导出任务状态、失败保留原始 pack、场景覆盖报告写入。
- Modify: `tests/unit/test_packager.py`：覆盖异步导出、失败不破坏 pack、覆盖报告生成。
- Create: `src/sts2_autotest/core/popup_disposal.py`：定义弹窗类型、处置策略和纯函数分类结果，不直接依赖截图实现。
- Create: `tests/unit/test_popup_disposal.py`：覆盖 Steam 更新、EULA、广告、崩溃弹窗的处置决策。
- Modify: `src/sts2_autotest/core/recovery.py`：在恢复执行前后接入可注入的 popup handler；崩溃弹窗只记录证据，不关闭。
- Modify: `tests/unit/test_recovery_strategy.py`：覆盖恢复过程中弹窗 handler 的调用和崩溃弹窗保留策略。
- Modify: `src/sts2_autotest/core/session_queue.py`：增加队列级暂停/继续状态，保证当前请求完成后才停止调度新请求。
- Modify: `tests/unit/test_session_queue.py`：覆盖暂停时不 dequeue、继续后恢复优先级 FIFO。
- Modify: `src/sts2_autotest/core/progress.py`：扩展进度快照字段，记录当前步骤、游戏状态、恢复状态和暂停状态。
- Modify: `tests/unit/test_progress.py`：覆盖新字段保存、加载、CRC 校验和旧格式兼容。
- Modify: `src/sts2_autotest/core/orchestrator.py`：在安全点响应暂停请求，保存进度并停止推进新动作/用例。
- Modify: `tests/unit/test_orchestrator.py`：覆盖当前步骤完成后暂停、恢复后从 pending cases 继续。
- Modify: `src/sts2_autotest/evidence/metrics.py`：记录场景访问事件并生成覆盖摘要。
- Modify: `tests/unit/test_metrics.py`：覆盖战斗、地图、商店、休息点、事件、角色选择等维度统计。
- Modify: `src/sts2_autotest/cli/main.py`：增加 `autotest queue pause/resume/status`、`autotest progress`、`autotest report --coverage` 的薄封装。
- Modify: `tests/unit/test_cli.py`：覆盖新 CLI 参数解析与输出语义。
- Create: `tests/integration/test_epic5_unattended.py`：用 mock adapter 做短时长无人值守验证；真实四小时命令单独运行。

## 执行顺序

1. Story 5.1 回归验证：确认已完成的 B1 仍是长跑前置。
2. Story 5.7 异步 Artifact ZIP 打包：先降低会话结束阻塞。
3. Story 5.2 弹窗自动处置：处理恢复过程阻塞点。
4. Story 5.4 本地测试队列暂停继续：补队列级控制。
5. Story 5.5 实时进度暂停继续：补运行中安全介入。
6. Story 5.6 游戏场景覆盖率报告：补 Beta 回归可见性。
7. Story 5.3 四小时无人值守运行验证：执行最终验收。

---

### Task 1: Story 5.1 回归验证

**Files:**
- Read: `src/sts2_autotest/core/recovery.py`
- Read: `src/sts2_autotest/core/orchestrator.py`
- Test: `tests/unit/test_recovery_strategy.py`
- Test: `tests/unit/test_orchestrator.py`

- [ ] **Step 1: 运行现有三级恢复单元测试**

Run:

```powershell
python -m pytest tests/unit/test_recovery_strategy.py tests/unit/test_orchestrator.py -q --tb=short
```

Expected: PASS。重点确认第一次 crash 返回 `GAME_RESTART`，第二次连续 crash 返回 `FULL_RESTART`，第三次连续 crash 进入 `deterministic_fail`。

- [ ] **Step 2: 若失败，先修复回归再继续后续 Story**

只允许修复 `core/recovery.py`、`core/orchestrator.py` 或对应测试中的 Epic 5.1 回归点。不得在后续 Story 中顺手重构恢复策略。

- [ ] **Step 3: 提交 Story 5.1 回归记录**

Run:

```powershell
python -m pytest tests/unit/test_recovery_strategy.py tests/unit/test_orchestrator.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add tests/unit/test_recovery_strategy.py tests/unit/test_orchestrator.py src/sts2_autotest/core/recovery.py src/sts2_autotest/core/orchestrator.py
git commit -m "test: verify epic5 crash recovery baseline"
```

### Task 2: Story 5.7 异步 Artifact ZIP 打包

**Files:**
- Modify: `src/sts2_autotest/evidence/packager.py`
- Test: `tests/unit/test_packager.py`

- [ ] **Step 1: 写失败测试，证明异步导出立即返回任务对象**

Add tests:

```python
def test_export_artifact_async_returns_job_before_zip_exists(tmp_path: Path) -> None:
    pkgr = EvidencePackager(tmp_path)
    pkgr.create_pack("run_async", run_result="passed")

    job = pkgr.export_artifact_async("run_async", result="passed")

    assert job.pack_id == "run_async"
    assert job.status in {"PENDING", "RUNNING", "DONE"}
    assert job.original_pack_dir == tmp_path / "run_async"
    assert (tmp_path / "run_async" / "summary.json").is_file()


def test_export_artifact_async_failure_preserves_original_pack(tmp_path: Path) -> None:
    pkgr = EvidencePackager(tmp_path)
    pkgr.create_pack("run_async_fail", run_result="failed")

    with patch("shutil.make_archive", side_effect=OSError("mock zip failure")):
        job = pkgr.export_artifact_async("run_async_fail", result="failed")
        result = job.wait(timeout=5.0)

    assert result is None
    assert job.status == "FAILED"
    assert job.error == "mock zip failure"
    assert (tmp_path / "run_async_fail" / "summary.json").is_file()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/unit/test_packager.py::TestArtifactExport -q --tb=short
```

Expected: FAIL，提示 `EvidencePackager` 没有 `export_artifact_async`。

- [ ] **Step 3: 实现最小异步导出 API**

In `src/sts2_autotest/evidence/packager.py`, add module-local dataclass and method:

```python
@dataclass
class ArtifactExportJob:
    pack_id: str
    original_pack_dir: Path
    _future: Future[Path | None]
    status: str = "PENDING"
    error: str | None = None

    def wait(self, timeout: float | None = None) -> Path | None:
        try:
            result = self._future.result(timeout=timeout)
        except Exception as exc:
            self.status = "FAILED"
            self.error = str(exc)
            return None
        self.status = "DONE" if result is not None else "FAILED"
        return result
```

Then add `EvidencePackager.export_artifact_async()` using a single `ThreadPoolExecutor(max_workers=1)` per packager instance and wrapping the existing `export_artifact()` call. The worker must catch `OSError` through the existing `export_artifact()` behavior and keep the original pack directory unchanged.

- [ ] **Step 4: 运行 focused tests**

Run:

```powershell
python -m pytest tests/unit/test_packager.py -q --tb=short
```

Expected: PASS。

- [ ] **Step 5: Story 5.7 质量门与提交**

Run:

```powershell
python -m pytest tests/unit/test_packager.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add src/sts2_autotest/evidence/packager.py tests/unit/test_packager.py
git commit -m "feat: export evidence artifacts asynchronously"
```

### Task 3: Story 5.2 弹窗自动处置

**Files:**
- Create: `src/sts2_autotest/core/popup_disposal.py`
- Create: `tests/unit/test_popup_disposal.py`
- Modify: `src/sts2_autotest/core/recovery.py`
- Test: `tests/unit/test_recovery_strategy.py`

- [ ] **Step 1: 写弹窗分类与处置策略测试**

Create `tests/unit/test_popup_disposal.py`:

```python
from sts2_autotest.core.popup_disposal import (
    PopupDisposition,
    PopupKind,
    classify_popup,
    decide_popup_disposition,
)


def test_classifies_steam_eula_popup() -> None:
    assert classify_popup(title="Steam", text="End User License Agreement") == PopupKind.STEAM_EULA


def test_classifies_steam_update_popup() -> None:
    assert classify_popup(title="Steam", text="Update required before launch") == PopupKind.STEAM_UPDATE


def test_crash_popup_is_preserved() -> None:
    kind = classify_popup(title="Slay the Spire 2", text="The application has crashed")
    assert kind == PopupKind.GAME_CRASH
    assert decide_popup_disposition(kind) == PopupDisposition.PRESERVE


def test_known_non_crash_popup_can_be_closed() -> None:
    assert decide_popup_disposition(PopupKind.STEAM_AD) == PopupDisposition.CLOSE
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/unit/test_popup_disposal.py -q --tb=short
```

Expected: FAIL，提示模块不存在。

- [ ] **Step 3: 实现 `core/popup_disposal.py`**

Implement module-local enums and pure functions:

```python
class PopupKind(StrEnum):
    NONE = "NONE"
    STEAM_EULA = "STEAM_EULA"
    STEAM_UPDATE = "STEAM_UPDATE"
    STEAM_AD = "STEAM_AD"
    GAME_CRASH = "GAME_CRASH"
    UNKNOWN = "UNKNOWN"


class PopupDisposition(StrEnum):
    IGNORE = "IGNORE"
    CLOSE = "CLOSE"
    PRESERVE = "PRESERVE"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"
```

`classify_popup(title, text)` uses case-insensitive keyword matching. `decide_popup_disposition()` returns `PRESERVE` for `GAME_CRASH`, `CLOSE` for `STEAM_EULA`/`STEAM_UPDATE`/`STEAM_AD`, `IGNORE` for `NONE`, and `MANUAL_INTERVENTION` for `UNKNOWN`.

- [ ] **Step 4: 将恢复策略接入可注入 popup handler**

Modify `DefaultRecoveryStrategy.__init__` to accept `popup_handler: Callable[[], PopupDisposition] | None = None` and call it before `GAME_RESTART` and `FULL_RESTART`. If the handler returns `PRESERVE`, log the condition and continue evidence preservation without closing the popup. If it returns `MANUAL_INTERVENTION`, return `(False, None)` so caller records failure.

- [ ] **Step 5: 增加恢复策略测试**

Add to `tests/unit/test_recovery_strategy.py`:

```python
def test_game_restart_stops_when_popup_requires_manual_intervention(self) -> None:
    mock_steam = MagicMock()
    mock_steam.restart_game = MagicMock(return_value=12345)
    old_adapter = self._make_healthy_adapter()
    strategy = DefaultRecoveryStrategy(
        adapter_factory=lambda: self._make_healthy_adapter(),
        steam_controller=mock_steam,
        popup_handler=lambda: PopupDisposition.MANUAL_INTERVENTION,
    )
    ok, new_adapter = _run(strategy.execute(RecoveryAction.GAME_RESTART, old_adapter))

    assert ok is False
    assert new_adapter is None
    mock_steam.restart_game.assert_not_called()
```

Place this test in the existing `TestGameRestart` class and import `PopupDisposition` from `sts2_autotest.core.popup_disposal`.

- [ ] **Step 6: Story 5.2 质量门与提交**

Run:

```powershell
python -m pytest tests/unit/test_popup_disposal.py tests/unit/test_recovery_strategy.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add src/sts2_autotest/core/popup_disposal.py src/sts2_autotest/core/recovery.py tests/unit/test_popup_disposal.py tests/unit/test_recovery_strategy.py
git commit -m "feat: classify and handle recovery popups"
```

### Task 4: Story 5.4 本地测试队列暂停继续

**Files:**
- Modify: `src/sts2_autotest/core/session_queue.py`
- Test: `tests/unit/test_session_queue.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: 写队列暂停/继续失败测试**

Add to `tests/unit/test_session_queue.py`:

```python
def test_pause_prevents_dequeue_without_dropping_items() -> None:
    q = SessionQueue()
    req = SessionRequest(session_id="s1")
    assert q.enqueue(req) is True
    q.pause()
    assert q.is_paused is True
    assert q.dequeue() is None
    assert q.queue_depth == 1


def test_resume_restores_priority_fifo_dequeue() -> None:
    q = SessionQueue()
    low = SessionRequest(session_id="low", priority=QueuePriority.LOW)
    high = SessionRequest(session_id="high", priority=QueuePriority.HIGH)
    q.enqueue(low)
    q.enqueue(high)
    q.pause()
    q.resume()
    assert q.is_paused is False
    assert q.dequeue() is high
    assert q.dequeue() is low
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/unit/test_session_queue.py -q --tb=short
```

Expected: FAIL，提示 `pause`/`resume`/`is_paused` 不存在。

- [ ] **Step 3: 实现队列暂停状态**

In `SessionQueue`, add `_paused: bool = False`, property `is_paused`, methods `pause()` and `resume()`. `dequeue()` must return `None` while paused and must not mutate `_heap` or `_pending` in that branch.

- [ ] **Step 4: 增加 CLI 薄封装测试**

Add parser tests to `tests/unit/test_cli.py`:

```python
def test_queue_pause_command_parses() -> None:
    parser = _create_parser()
    args = parser.parse_args(["queue", "pause"])
    assert args.command == "queue"
    assert args.queue_action == "pause"


def test_queue_resume_command_parses() -> None:
    parser = _create_parser()
    args = parser.parse_args(["queue", "resume"])
    assert args.queue_action == "resume"
```

- [ ] **Step 5: 实现 `autotest queue pause/resume/status` 参数解析**

In `_create_parser()`, add `queue` subparser with choices `pause`, `resume`, `status`. The handler prints structured local status and returns `0`; persistent cross-process control is represented by the Story 5.5 progress state.

- [ ] **Step 6: Story 5.4 质量门与提交**

Run:

```powershell
python -m pytest tests/unit/test_session_queue.py tests/unit/test_cli.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add src/sts2_autotest/core/session_queue.py src/sts2_autotest/cli/main.py tests/unit/test_session_queue.py tests/unit/test_cli.py
git commit -m "feat: pause and resume local session queue"
```

### Task 5: Story 5.5 实时进度暂停继续

**Files:**
- Modify: `src/sts2_autotest/core/progress.py`
- Test: `tests/unit/test_progress.py`
- Modify: `src/sts2_autotest/core/orchestrator.py`
- Test: `tests/unit/test_orchestrator.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: 写进度快照扩展字段测试**

Add to `tests/unit/test_progress.py`:

```python
def test_progress_round_trips_runtime_status_fields(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    record = ProgressRecord(
        session_id="sess-rt",
        completed_cases=["TC-1"],
        pending_cases=["TC-2"],
        current_case="TC-2",
        current_step="play-card",
        game_screen="COMBAT",
        recovery_status="FAST_PATH",
        paused=True,
    )
    assert save_progress(record, path) is True

    loaded = load_progress(path)
    assert loaded is not None
    assert loaded.current_step == "play-card"
    assert loaded.game_screen == "COMBAT"
    assert loaded.recovery_status == "FAST_PATH"
    assert loaded.paused is True
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/unit/test_progress.py -q --tb=short
```

Expected: FAIL，提示 `ProgressRecord` 不接受新字段。

- [ ] **Step 3: 扩展 `ProgressRecord`**

Add fields with backward-compatible defaults:

```python
current_step: str | None = None
game_screen: str | None = None
recovery_status: str | None = None
paused: bool = False
```

Update `to_dict()` and `from_dict()` so old progress files without those keys still load.

- [ ] **Step 4: 写 orchestrator 暂停安全点测试**

Add to `tests/unit/test_orchestrator.py`:

```python
def test_run_all_pauses_after_current_case_and_saves_progress(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.json"
    orch = TestOrchestrator(
        adapter=_make_mock_adapter(),
        progress_path=str(progress_path),
    )
    orch.request_pause()

    summary = _run(orch.run_all(["TC-1", "TC-2"]))

    assert summary.results[0].case_id == "TC-1"
    assert summary.results[1].status == "skip"
    saved = load_progress(progress_path)
    assert saved is not None
    assert saved.paused is True
    assert saved.pending_cases == ["TC-2"]
```

- [ ] **Step 5: 实现 `TestOrchestrator.request_pause()` 与安全点检查**

Add `_pause_requested: bool = False`, public `request_pause()`, and in `run_all()` after each completed case save a progress snapshot with `paused=True` and skip remaining cases with message `Paused by operator`.

- [ ] **Step 6: 增加 `autotest progress` 命令**

Add parser and command handler. The handler loads `_get_progress_path()` and prints current case, current step, game screen, recovery status and paused flag. Corrupted progress file returns exit code `1`.

- [ ] **Step 7: Story 5.5 质量门与提交**

Run:

```powershell
python -m pytest tests/unit/test_progress.py tests/unit/test_orchestrator.py tests/unit/test_cli.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add src/sts2_autotest/core/progress.py src/sts2_autotest/core/orchestrator.py src/sts2_autotest/cli/main.py tests/unit/test_progress.py tests/unit/test_orchestrator.py tests/unit/test_cli.py
git commit -m "feat: expose realtime progress pause and resume state"
```

### Task 6: Story 5.6 游戏场景覆盖率报告

**Files:**
- Modify: `src/sts2_autotest/evidence/metrics.py`
- Test: `tests/unit/test_metrics.py`
- Modify: `src/sts2_autotest/evidence/packager.py`
- Test: `tests/unit/test_packager.py`
- Modify: `src/sts2_autotest/cli/main.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: 写场景覆盖统计测试**

Add to `tests/unit/test_metrics.py`:

```python
def test_scene_coverage_summary_counts_known_scenes(tmp_path: Path) -> None:
    metrics = MetricsCollector(tmp_path)
    metrics.record_scene_visit("TC-1", "COMBAT")
    metrics.record_scene_visit("TC-1", "MAP")
    metrics.record_scene_visit("TC-2", "COMBAT")

    coverage = metrics.get_scene_coverage()

    assert coverage["COMBAT"]["visits"] == 2
    assert coverage["COMBAT"]["cases"] == ["TC-1", "TC-2"]
    assert coverage["MAP"]["visits"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/unit/test_metrics.py -q --tb=short
```

Expected: FAIL，提示缺少 `record_scene_visit` 或 `get_scene_coverage`。

- [ ] **Step 3: 实现场景覆盖计数**

In `MetricsCollector`, keep `_scene_cases: dict[str, set[str]]` and `_scene_visits: dict[str, int]`. `record_scene_visit(case_id, scene)` records a metric event and updates both structures. `get_scene_coverage()` returns JSON-serialisable dictionaries with sorted case lists.

- [ ] **Step 4: 写 packager 覆盖报告测试**

Add to `tests/unit/test_packager.py`:

```python
def test_write_scene_coverage_report_creates_json_and_markdown(tmp_path: Path) -> None:
    pkgr = EvidencePackager(tmp_path)
    pkgr.create_pack("run_cov")
    coverage = {
        "COMBAT": {"visits": 2, "cases": ["TC-1", "TC-2"]},
        "SHOP": {"visits": 0, "cases": []},
    }

    paths = pkgr.write_scene_coverage_report("run_cov", coverage)

    assert paths["json"].name == "scene-coverage.json"
    assert paths["markdown"].name == "scene-coverage.md"
    assert paths["json"].is_file()
    assert "COMBAT" in paths["markdown"].read_text(encoding="utf-8")
```

- [ ] **Step 5: 实现 `write_scene_coverage_report()`**

Write `reports/scene-coverage.json` and `reports/scene-coverage.md` with atomic temp files. JSON contains all scenes passed by caller. Markdown contains a table with scene, visits and cases.

- [ ] **Step 6: 增加 CLI `report --coverage`**

`autotest report <run_id> --coverage` reads `reports/scene-coverage.md` from the evidence pack and prints it. Missing coverage file returns exit code `1` with available report path guidance.

- [ ] **Step 7: Story 5.6 质量门与提交**

Run:

```powershell
python -m pytest tests/unit/test_metrics.py tests/unit/test_packager.py tests/unit/test_cli.py -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add src/sts2_autotest/evidence/metrics.py src/sts2_autotest/evidence/packager.py src/sts2_autotest/cli/main.py tests/unit/test_metrics.py tests/unit/test_packager.py tests/unit/test_cli.py
git commit -m "feat: report game scene coverage"
```

### Task 7: Story 5.3 四小时无人值守运行验证

**Files:**
- Create: `tests/integration/test_epic5_unattended.py`
- Modify: `_bmad-output/implementation-artifacts/sprint-status.yaml`
- Modify: `docs/beta-roadmap.md`

- [ ] **Step 1: 写短时 mock 长跑集成测试**

Create `tests/integration/test_epic5_unattended.py`:

```python
import asyncio
import itertools
import time
from typing import Any
from unittest.mock import MagicMock

from sts2_autotest.adapters.base import ActionResult, GameAdapterProtocol, HealthStatus
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.orchestrator import TestOrchestrator


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_mock_adapter() -> Any:
    states = itertools.cycle([
        GameState(screen=GameScreen.MAP),
        GameState(screen=GameScreen.COMBAT),
    ])
    mock = MagicMock(spec=GameAdapterProtocol)
    mock.health_check.return_value = HealthStatus(healthy=True)
    mock.get_state.side_effect = lambda: next(states)
    mock.get_available_actions.return_value = ["probe", "play_card", "end_turn"]
    mock.act.return_value = ActionResult(status="success", state_changed=True)
    mock.wait_until_actionable.return_value = True
    mock.capture_bug_snapshot.return_value = {}
    return mock


def test_unattended_mock_run_has_no_framework_crash() -> None:
    started = time.monotonic()
    orch = TestOrchestrator(adapter=_make_mock_adapter())
    summary = _run(orch.run_all([f"TC-{i:03d}" for i in range(20)]))
    elapsed = time.monotonic() - started

    assert summary.total == 20
    assert summary.crashed == 0
    assert elapsed < 60.0
```

- [ ] **Step 2: 运行 mock 长跑集成测试**

Run:

```powershell
python -m pytest tests/integration/test_epic5_unattended.py -q --tb=short
```

Expected: PASS。

- [ ] **Step 3: 执行 Epic 5 单元回归**

Run:

```powershell
python -m pytest tests/unit/test_recovery_strategy.py tests/unit/test_popup_disposal.py tests/unit/test_session_queue.py tests/unit/test_progress.py tests/unit/test_metrics.py tests/unit/test_packager.py tests/unit/test_cli.py -q --tb=short
```

Expected: PASS。

- [ ] **Step 4: 执行真实四小时验证**

Only run this on a stable local Windows environment with Steam logged in and STS2-Cli-Mod available:

```powershell
python -m pytest tests/integration/ -m requires_game --durations=20 -q --tb=short
```

Expected: PASS for all game-required tests that are valid for the current local install. If the real four-hour suite is driven through CLI rather than pytest, use:

```powershell
autotest run --all --timeout 14400 --no-resume
```

Expected: process exits `0`, evidence pack is created, progress file is cleared on normal completion, artifact export job reaches `DONE`, and no framework-level resource leak is observed.

- [ ] **Step 5: 更新状态文档**

Update `_bmad-output/implementation-artifacts/sprint-status.yaml` so Epic 5 stories are:

```yaml
  epic-5: done
  5-1-crash-three-level-recovery: done
  5-2-popup-auto-disposal: done
  5-3-four-hour-unattended-runtime-validation: done
  5-4-local-test-queue-pause-resume: done
  5-5-realtime-progress-pause-resume: done
  5-6-game-scene-coverage-report: done
  5-7-async-artifact-zip-packaging: done
```

Update `docs/beta-roadmap.md` with the exact verification command, date, duration and any skipped real-game checks.

- [ ] **Step 6: Epic 5 全局质量门与提交**

Run:

```powershell
python -m pytest tests/unit/ -q --tb=short
mypy src/sts2_autotest --strict
lint-imports
```

Expected: 三个命令均 PASS。

Commit:

```powershell
git add tests/integration/test_epic5_unattended.py _bmad-output/implementation-artifacts/sprint-status.yaml docs/beta-roadmap.md
git commit -m "test: validate epic5 unattended runtime"
```

## 最终验收清单

- [ ] Story 5.1 回归测试通过，恢复策略仍满足 B1。
- [ ] Story 5.7 异步 ZIP 失败时保留原始 evidence pack。
- [ ] Story 5.2 已识别 Steam/游戏弹窗可处置，崩溃弹窗保留现场。
- [ ] Story 5.4 队列暂停后不调度新请求，继续后保持优先级 FIFO。
- [ ] Story 5.5 实时进度包含当前用例、步骤、游戏状态、恢复状态和暂停状态。
- [ ] Story 5.6 场景覆盖报告生成 JSON 与 Markdown。
- [ ] Story 5.3 mock 长跑通过，真实四小时验证有运行记录或明确环境跳过说明。
- [ ] `python -m pytest tests/unit/ -q --tb=short` PASS。
- [ ] `mypy src/sts2_autotest --strict` PASS。
- [ ] `lint-imports` PASS。
