"""Generate HTML test reports from structured AUTOTEST JSON results."""

from __future__ import annotations

import base64
import html
import json
import os
from pathlib import Path
from typing import Any

from sts2_autotest.common.visual_qa import ScreenshotOcrAnalysis


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _badge_kind(result: str) -> str:
    if result == "通过":
        return "pass"
    if result == "失败":
        return "fail"
    if result == "阻塞":
        return "block"
    return "skip"


def _b64img(path: Path) -> str:
    try:
        return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"
    except Exception:
        return ""


def _count_cases(cases: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "passed": sum(1 for case in cases if case.get("result") == "通过"),
        "failed": sum(1 for case in cases if case.get("result") == "失败"),
        "blocked": sum(1 for case in cases if case.get("result") == "阻塞"),
        "skipped": sum(1 for case in cases if case.get("result") == "跳过"),
        "total": len(cases),
    }


def build_report_html(config: dict[str, Any]) -> str:
    cases = config.get("test_cases", [])
    cards = config.get("card_results", [])
    meta = config.get("metadata", {})
    run_id = config.get("test_run_id", "unknown")
    cfg_dir = Path(config.get("_config_dir", "."))
    counts = _count_cases(cases)

    def step_html(test_case: dict[str, Any], step: dict[str, Any], index: int) -> str:
        result = str(step.get("result", "跳过"))
        badge = _badge_kind(result)
        detail = str(step.get("detail", ""))
        return f"""<div class="step"><div class="step-header" onclick="toggle(this)">
  <span class="step-name">{index + 1}. {_h(step.get('name', 'Execute'))}</span>
  <span class="badge badge-{badge}">{_h(result)}</span>
</div>
<div class="step-evidence">
  <p>{_h(detail)}</p>
  <pre class="log"># {_h(test_case.get('name', ''))} - 步骤 {index + 1}\n# 结果: {_h(result)}\n# {_h(detail)}</pre>
</div></div>"""

    def ocr_html(analysis: Any) -> str:
        if isinstance(analysis, ScreenshotOcrAnalysis):
            analysis = analysis.model_dump(mode="json")
        if not isinstance(analysis, dict):
            return ""
        status = str(analysis.get("status", "skipped"))
        provider = str(analysis.get("provider", "unknown"))
        message = str(analysis.get("message") or "")
        findings = analysis.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        if status == "passed":
            body = "视觉辅助分析：未发现风险"
        elif status == "warning":
            rows = []
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                severity = str(finding.get("severity", "warning"))
                text = str(finding.get("text", ""))
                finding_message = str(finding.get("message", ""))
                meta_parts = []
                confidence = finding.get("confidence")
                bbox = finding.get("bbox")
                if confidence is not None:
                    meta_parts.append(f"confidence={confidence}")
                if bbox is not None:
                    meta_parts.append(f"bbox={bbox}")
                meta_text = f" <span class=\"ocr-meta\">({_h(', '.join(meta_parts))})</span>" if meta_parts else ""
                rows.append(
                    f"<li>[{_h(severity)}] {_h(finding_message)}: <code>{_h(text)}</code>{meta_text}</li>"
                )
            joined = "".join(rows)
            body = f"视觉辅助分析：发现 {len(rows)} 条可疑项<ul>{joined}</ul>"
        else:
            suffix = f" - {_h(message)}" if message else ""
            body = f"视觉辅助分析：未执行{suffix}"

        return (
            '<div class="ocr-box">'
            f"<div>{body}</div>"
            f'<div class="ocr-provider">Provider: {_h(provider)}</div>'
            "</div>"
        )

    def card_html(card: dict[str, Any]) -> str:
        before_img = str(card.get("screenshot_before", ""))
        after_img = str(card.get("screenshot_after", ""))
        before_src = _b64img(cfg_dir / before_img) if before_img else ""
        after_src = _b64img(cfg_dir / after_img) if after_img else ""
        exp_items = card.get("exp", {})
        exp_str = "、".join(f"{_h(key)}={_h(value)}" for key, value in exp_items.items()) or "无直接数值校验"
        result = str(card.get("result", "跳过"))
        badge = _badge_kind(result)
        before_ocr = ocr_html(card.get("screenshot_before_ocr"))
        after_ocr = ocr_html(card.get("screenshot_after_ocr"))
        return f"""<div class="step"><div class="step-header" onclick="toggle(this)">
  <span class="step-name">{_h(card.get('name', ''))} ({_h(card.get('card_id', ''))})</span>
  <span class="badge badge-{badge}">{_h(result)}</span>
</div>
<div class="step-evidence">
  <p>期望: {exp_str}</p>
  <div class="img-row">
    <div class="img-box"><div class="img-label">打出前</div>{f'<img src="{before_src}">' if before_src else '<i>无截图</i>'}{before_ocr}</div>
    <div class="img-box"><div class="img-label">打出后</div>{f'<img src="{after_src}">' if after_src else '<i>无截图</i>'}{after_ocr}</div>
  </div>
</div></div>"""

    def card_section(card_entries: list[dict[str, Any]]) -> str:
        if not card_entries:
            return ""
        return f"""<div class="case-section"><div class="case-section-title">卡牌凭证（{len(card_entries)} 张）</div>
{''.join(card_html(card) for card in card_entries)}</div>"""

    def case_html(test_case: dict[str, Any]) -> str:
        assertions = "".join(f"<li>{_h(assertion)}</li>" for assertion in test_case.get("assertions", []))
        steps = "".join(step_html(test_case, step, index) for index, step in enumerate(test_case.get("steps", [])))
        result = str(test_case.get("result", "跳过"))
        badge = _badge_kind(result)
        return f"""<div class="case-card">
  <div class="case-header" onclick="toggleCase(this)">
    <div class="case-title"><span class="case-id">{_h(test_case.get('id', ''))}</span><span class="case-name">{_h(test_case.get('name', ''))}</span></div>
    <span class="badge badge-{badge}">{_h(result)}</span>
  </div>
  <div class="case-body">
    <div class="case-section"><div class="case-section-title">测试场景</div><p>{_h(test_case.get('scenario', ''))}</p></div>
    <div class="case-section"><div class="case-section-title">断言条件</div><ul>{assertions}</ul></div>
    <div class="case-section"><div class="case-section-title">实际结果</div><p>{_h(test_case.get('actual', ''))}</p></div>
    <div class="case-section"><div class="case-section-title">测试步骤（共 {len(test_case.get('steps', []))} 步）</div>{steps}</div>
    {card_section(test_case.get("card_results", []))}
  </div></div>"""

    badge_row = "".join(
        f'<span class="badge badge-{_badge_kind(str(case.get("result", "跳过")))}">{_h(case.get("id", ""))}: {_h(case.get("result", ""))}</span>'
        for case in cases
    )
    all_cases = "".join(case_html(case) for case in cases)

    standalone_cards = ""
    if cards and not any(case.get("card_results") for case in cases):
        standalone_cards = f"""
<h2 style="font-size:16px;margin:24px 0 12px;color:#999">卡牌凭证</h2>
<div class="case-card"><div class="case-body open">{card_section(cards)}</div></div>"""

    nav_path = str(meta.get("navigation_path", ""))
    nav_html = ""
    if nav_path:
        nav_html = f"""
<h2 style="font-size:16px;margin:24px 0 12px;color:#999">导航流程</h2>
<div class="case-card"><div class="case-header" onclick="toggleCase(this)">
  <div class="case-title"><span class="case-id">流程</span><span class="case-name">测试导航路径</span></div>
  <span class="badge badge-pass">通过</span>
</div><div class="case-body"><pre class="log" style="margin-top:8px">{_h(nav_path)}</pre></div></div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>STS2 测试报告</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",Roboto,sans-serif;background:#0a0a0f;color:#d4d4d4;padding:20px}}
.container{{max-width:1200px;margin:0 auto}}
h1{{font-size:24px;display:flex;align-items:center;gap:12px;margin-bottom:4px}}
.subtitle{{color:#666;font-size:13px;margin-bottom:24px}}
.summary{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
.summary-card{{flex:1;min-width:140px;padding:14px;border-radius:8px;text-align:center;font-size:13px;border:1px solid #2a2a30}}
.summary-card .num{{font-size:28px;font-weight:700}}
.summary-card.pass{{background:#0a1a0f;border-color:#166534;color:#4ade80}}
.summary-card.fail{{background:#1a0a0f;border-color:#991b1b;color:#f87171}}
.summary-card.block{{background:#1a1a0a;border-color:#854d0e;color:#facc15}}
.summary-card.skip{{background:#14141a;border-color:#3f3f46;color:#a1a1aa}}
.summary-card.total{{background:#1a1a22;border-color:#2a2a30;color:#999}}
.badge{{display:inline-block;padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap}}
.badge-pass{{background:#0a2a0f;color:#4ade80;border:1px solid #166534}}
.badge-fail{{background:#2a0a0f;color:#f87171;border:1px solid #991b1b}}
.badge-block{{background:#2a220a;color:#facc15;border:1px solid #a16207}}
.badge-skip{{background:#1f1f28;color:#a1a1aa;border:1px solid #52525b}}
.case-card{{background:#14141a;border-radius:8px;margin-bottom:12px;border:1px solid #2a2a30;overflow:hidden}}
.case-header{{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;cursor:pointer;transition:background .15s}}
.case-header:hover{{background:#1a1a22}}
.case-title{{display:flex;align-items:center;gap:10px}}
.case-id{{font-size:11px;color:#666;background:#1a1a22;padding:2px 8px;border-radius:4px;font-family:monospace}}
.case-name{{font-size:15px;font-weight:500}}
.case-body{{padding:0 16px;display:none}}
.case-body.open{{display:block;padding:16px}}
.case-section{{margin-bottom:16px}}
.case-section-title{{font-size:12px;color:#666;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600}}
.case-section p,.case-section li{{font-size:13px;color:#999;line-height:1.5}}
.case-section ul{{padding-left:18px}}
.step{{margin-bottom:6px;border:1px solid #22222a;border-radius:6px;overflow:hidden}}
.step-header{{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;cursor:pointer;font-size:13px;transition:background .15s}}
.step-header:hover{{background:#1a1a22}}
.step-name{{color:#aaa}}
.step-evidence{{display:none;padding:10px 12px;background:#0f0f15;border-top:1px solid #22222a}}
.step-evidence p{{font-size:12px;color:#888;margin-bottom:6px}}
pre.log{{background:#050510;color:#6ee;padding:8px;border-radius:4px;font-size:11px;overflow-x:auto;font-family:"SF Mono",Menlo,monospace}}
.img-row{{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}}
.img-row img{{max-width:100%;height:auto;border-radius:4px;border:1px solid #22222a}}
.img-box{{flex:1;min-width:150px}}
.img-label{{font-size:11px;color:#666;margin-bottom:4px}}
.ocr-box{{margin-top:6px;padding:8px;border-radius:4px;background:#111827;border:1px solid #374151;font-size:12px;color:#cbd5e1}}
.ocr-box ul{{margin:6px 0 0 18px}}
.ocr-box code{{color:#facc15;font-family:"SF Mono",Menlo,monospace}}
.ocr-meta{{color:#64748b;font-size:11px}}
.ocr-provider{{margin-top:4px;color:#64748b;font-size:11px}}
</style>
</head><body>
<div class="container">
<h1>STS2 测试报告</h1>
<div class="subtitle">{_h(run_id)} — 由 STS2-AUTOTEST 自动化执行</div>
<div class="summary">
  <div class="summary-card pass"><div class="num">{counts['passed']}</div>通过</div>
  <div class="summary-card fail"><div class="num">{counts['failed']}</div>失败</div>
  <div class="summary-card block"><div class="num">{counts['blocked']}</div>阻塞</div>
  <div class="summary-card skip"><div class="num">{counts['skipped']}</div>跳过</div>
  <div class="summary-card total"><div class="num">{counts['total']}</div>总计</div>
</div>
<div style="display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap">{badge_row}</div>
<h2 style="font-size:16px;margin-bottom:12px;color:#999">测试案例</h2>
{all_cases}
{standalone_cards}
{nav_html}
<script>
function toggle(e){{var ev=e.parentElement.querySelector('.step-evidence');ev.style.display=ev.style.display==='block'?'none':'block'}}
function toggleCase(e){{var body=e.parentElement.querySelector('.case-body');body.classList.toggle('open')}}
</script>
</div></body></html>"""


def write_html_report(config_path: str | Path, output_path: str | Path) -> None:
    config_file = Path(config_path)
    output_file = Path(output_path)
    config = json.loads(config_file.read_text(encoding="utf-8"))
    config["_config_dir"] = str(config_file.resolve().parent)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = output_file.with_name(f".{output_file.name}.tmp")
    tmp_file.write_text(build_report_html(config), encoding="utf-8")
    os.replace(tmp_file, output_file)
