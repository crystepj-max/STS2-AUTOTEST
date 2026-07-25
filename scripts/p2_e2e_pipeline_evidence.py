#!/usr/bin/env python3
"""P2 端到端标准证据驱动：项目规格执行 → 标准证据包 → 报告登记。

经公共执行入口 run_tests_in_dir 执行 Gawain 自包含套件
（含 give_card/set_seed 的项目配置链路），产出：
- 真实 JUnit XML 与 run-result.json；
- 套件汇总（suite-summaries）与逐案行为日志（case-traces）；
- 标准证据压缩包（tests/output/artifacts/）。

用法：
    python scripts/p2_e2e_pipeline_evidence.py
退出码：0 = 套件通过且证据包完整；1 = 任一不满足。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sts2_autotest.cli.mcp_tools import run_tests_in_dir  # noqa: E402

GAWAIN_DIR = PROJECT_ROOT.parent / "STS2-GAWAIN"
SUITE_FILE = (
    GAWAIN_DIR / "automation/autotest/generated/test_suite_gawain_m2_multi_trigger.py"
)
RUN_ID = "e2e-m2-multi-trigger-20260724-01"


def main() -> int:
    os.environ.setdefault("STS2_ADAPTER__AGENT__ENABLED", "true")
    os.environ.setdefault("STS2_ADAPTER__AGENT__DEBUG_ACTIONS", "true")
    output_dir = PROJECT_ROOT / "tests/output" / RUN_ID

    result = run_tests_in_dir(
        GAWAIN_DIR / "automation/autotest/generated",
        timeout=600,
        targets=[SUITE_FILE],
        output_dir=output_dir,
        run_id=RUN_ID,
        project_dir=GAWAIN_DIR,
    )
    print(json.dumps({k: result.get(k) for k in ("run_id", "status", "passed", "failed")}, ensure_ascii=False))

    pack_dir = Path(result.get("evidence_dir") or "")
    if not pack_dir.is_dir():
        print(f"[e2e] evidence pack missing: {pack_dir}")
        return 1

    # 把套件汇总与逐案行为日志补入标准证据包后重新导出压缩包。
    # 套件汇总由生成代码按套件文件位置写入项目仓库的 output/suite-summaries；
    # 逐案行为日志按 pytest 进程 cwd 写入 automation/autotest/output/case-traces。
    suite_summary = (
        GAWAIN_DIR / "automation/autotest/output/suite-summaries/SUITE-GAWAIN-M2-MULTI-TRIGGER.json"
    )
    trace_dir = PROJECT_ROOT / "automation/autotest/output/case-traces"
    copied: list[str] = []
    if suite_summary.is_file():
        dst = pack_dir / "reports" / suite_summary.name
        shutil.copy2(suite_summary, dst)
        copied.append(dst.name)
    matching_traces = sorted(trace_dir.glob("test_suite_gawain_m2_multi_trigger-*/**/*.log")) if trace_dir.is_dir() else []
    if matching_traces:
        traces_root = pack_dir / "reports" / "case-traces"
        for trace in matching_traces:
            dst = traces_root / trace.parent.parent.name / trace.parent.name / trace.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(trace, dst)
        copied.append(f"case-traces×{len(matching_traces)}")

    from sts2_autotest.evidence.packager import EvidencePackager

    packager = EvidencePackager(PROJECT_ROOT / "tests/output")
    artifact = packager.export_artifact(RUN_ID, result="passed" if result.get("status") == "OK" else "failed")

    summary = {
        "run_id": RUN_ID,
        "status": result.get("status"),
        "junit": str(output_dir / "junit.xml"),
        "pack_dir": str(pack_dir),
        "artifact": str(artifact),
        "evidence_added": copied,
    }
    (output_dir / "e2e-evidence-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = result.get("status") == "OK" and Path(str(artifact)).is_file()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
