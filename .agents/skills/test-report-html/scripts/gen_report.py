#!/usr/bin/env python3
"""Generate standardized HTML test report from a JSON config.

Usage:
  python3 gen_report.py --config test-results.json --output report.html

Config JSON format:
{
  "report_title": "回归测试报告",
  "test_run_id": "build-smoke-v5",
  "test_cases": [
    {
      "id": "TC-BUILD",
      "name": "构建项目",
      "scenario": "运行项目构建。",
      "assertions": ["依赖恢复成功", "项目构建成功"],
      "actual": "构建成功。",
      "result": "通过",
      "steps": [
        {"name": "依赖恢复", "result": "通过", "detail": "退出码 0", "evidence_type": "log", "evidence_path": "logs/restore.log"},
        {"name": "构建", "result": "通过", "detail": "退出码 0", "evidence_type": "log", "evidence_path": "logs/build.log"}
      ],
      "card_results": [
        {"card_id": "CASE-CARD-1", "name": "案例内状态检查", "result": "通过", "state_before": "state/case-before.json", "state_after": "state/case-after.json"}
      ]
    }
  ],
  "card_results": [
    {
      "card_id": "STRIKE", "name": "打击", "type": "Attack", "cost": 1,
      "exp": {"伤害": 6}, "result": "通过",
      "screenshot_before": "screenshots/card-STRIKE-before.png",
      "screenshot_after": "screenshots/card-STRIKE-after.png",
      "state_before": "state/card-STRIKE-before.json",
      "state_after": "state/card-STRIKE-after.json"
    }
  ],
  "metadata": {
    "report_title": "构建回归报告",
    "project": "示例项目",
    "runner": "pytest",
    "navigation_path": "准备数据 -> 执行构建 -> 检查产物"
  }
}

Top-level and per-case card_results are both rendered. Screenshot, state, and step evidence paths are resolved relative to the config file's directory unless absolute. Screenshots are embedded; state JSON and text logs are shown when expanded. Missing or unreadable files are identified in the report. navigation_path is descriptive metadata only; it is not counted or marked as a passing test.
Every test case must provide result exactly as 通过, 失败, or 阻塞. Missing or unsupported case results stop generation with a CLI validation error.
"""
import argparse, base64, html, json, os, sys
from pathlib import Path

def status_class(value):
    if value is None or str(value).strip() in {"", "未提供", "未记录", "未执行"}:
        return "unrecorded"
    if value == "通过":
        return "pass"
    if value == "阻塞":
        return "block"
    return "fail"

def gen(config):
    cases = config.get("test_cases", [])
    valid_case_results = {"通过", "失败", "阻塞"}
    for index, case in enumerate(cases, start=1):
        result = case.get("result")
        if not isinstance(result, str) or result not in valid_case_results:
            case_id = case.get("id", f"第 {index} 项")
            if result is None or not str(result).strip():
                issue = "缺少必需的 result"
            else:
                issue = f"result 值无效：{result}"
            raise ValueError(
                f"测试案例 {case_id} {issue}；请明确填写“通过”“失败”或“阻塞”。未生成本次报告。"
            )
    cards = config.get("card_results", [])
    meta = config.get("metadata", {})
    cfg_dir = Path(config.get("_config_dir", ".")).resolve()

    def esc(value):
        return html.escape(str(value), quote=True)

    def resolve_evidence_path(raw_path):
        if not raw_path:
            return None
        candidate = Path(str(raw_path)).expanduser()
        return candidate if candidate.is_absolute() else cfg_dir / candidate

    def read_text_evidence(raw_path, json_file=False):
        target = resolve_evidence_path(raw_path)
        if target is None:
            return None, "未提供证据文件路径"
        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            return None, f"证据文件不存在或不可读：{raw_path}"
        except UnicodeDecodeError:
            return None, f"证据文件不是可读取的 UTF-8 文本：{raw_path}"
        if json_file:
            try:
                content = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                content = "JSON 解析失败，以下为原始文件内容：\n" + content
        return content, None

    def image_data(raw_path):
        target = resolve_evidence_path(raw_path)
        if target is None:
            return None, "未提供截图文件路径"
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(target.suffix.lower())
        if mime is None:
            return None, f"不支持的截图格式：{raw_path}"
        try:
            data = target.read_bytes()
        except OSError:
            return None, f"截图文件不存在或不可读：{raw_path}"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", None

    passed = sum(1 for c in cases if status_class(c.get("result", "")) == "pass")
    blocked = sum(1 for c in cases if status_class(c.get("result", "")) == "block")
    total = len(cases)
    failed = total - passed - blocked

    def text_file_box(label, raw_path, json_file=False):
        content, error = read_text_evidence(raw_path, json_file)
        if error:
            return f'<div class="img-box"><div class="img-label">{esc(label)}</div><div class="evidence-missing">{esc(error)}</div></div>'
        return f'<div class="img-box"><div class="img-label">{esc(label)} · {esc(raw_path)}</div><pre class="log">{esc(content)}</pre></div>'

    def image_box(label, raw_path):
        if not raw_path:
            return f'<div class="img-box"><div class="img-label">{esc(label)}</div><div class="evidence-missing">未提供截图文件路径</div></div>'
        data_url, error = image_data(raw_path)
        if error:
            return f'<div class="img-box"><div class="img-label">{esc(label)} · {esc(raw_path)}</div><div class="evidence-missing">{esc(error)}</div></div>'
        return f'<div class="img-box"><div class="img-label">{esc(label)} · {esc(raw_path)}</div><img alt="{esc(label)}" src="{data_url}"></div>'

    def card_html(card):
        cid = card.get("card_id", "未命名卡片")
        result = card.get("result")
        if result is None or not str(result).strip():
            result = "未记录"
        exp = card.get("exp", {})
        exp_str = "、".join(f"{esc(k)}={esc(v)}" for k, v in exp.items()) if isinstance(exp, dict) else esc(exp)
        if not exp_str:
            exp_str = "无直接数值"
        return f"""<div class="step"><div class="step-header" onclick="toggle(this)">
  <span class="step-name">{esc(card.get('name', '未命名卡片'))} ({esc(cid)})</span>
  <span class="badge badge-{status_class(result)}">{esc(result)}</span>
</div>
<div class="step-evidence">
  <p>能量={esc(card.get('cost', '?'))} | {exp_str}</p>
  <div class="img-row">
    {image_box('打出前截图', card.get('screenshot_before'))}
    {image_box('打出后截图', card.get('screenshot_after'))}
  </div>
  <div class="img-row">
    {text_file_box('打出前状态 JSON', card.get('state_before'), json_file=True)}
    {text_file_box('打出后状态 JSON', card.get('state_after'), json_file=True)}
  </div>
</div></div>"""

    def card_section(items, title):
        if not items:
            return ""
        return f"""<div class="case-section"><div class="case-section-title">{esc(title)}（{len(items)} 项）</div>
{''.join(card_html(card) for card in items)}</div>"""

    def step_html(step, case_name, index):
        result = step.get("result")
        if result is None or not str(result).strip():
            result = "未记录"
        evidence_path = step.get("evidence_path")
        evidence_type = str(step.get("evidence_type", "log")).lower()
        attachment = ""
        if evidence_path:
            if evidence_type in {"screenshot", "image"}:
                attachment = f'<div class="img-row">{image_box("步骤截图", evidence_path)}</div>'
            else:
                attachment = f'<div class="img-row">{text_file_box("步骤凭证", evidence_path, json_file=evidence_type in {"json", "state", "state_json"})}</div>'
        elif step.get("detail"):
            attachment = f'<pre class="log">{esc(step.get("detail", ""))}</pre>'
        else:
            attachment = '<div class="evidence-missing">未提供步骤凭证</div>'
        detail = step.get("detail", "")
        return f"""<div class="step"><div class="step-header" onclick="toggle(this)">
  <span class="step-name">{index+1}. {esc(step.get('name', '未命名步骤'))}</span>
  <span class="badge badge-{status_class(result)}">{esc(result)}</span>
</div>
<div class="step-evidence">
  <p>{esc(detail)}</p>
  <pre class="log"># {esc(case_name)} - 步骤 {index+1}
# 结果: {esc(result)}
# {esc(detail)}</pre>
  {attachment}
</div></div>"""

    def case_html(case):
        result = case.get("result", "未提供")
        steps = "".join(step_html(step, case.get("name", ""), i) for i, step in enumerate(case.get("steps", [])))
        assertions = "".join(f"<li>{esc(assertion)}</li>" for assertion in case.get("assertions", []))
        nested_cards = card_section(case.get("card_results", []), "本案例卡牌验证")
        case_id = case.get("id", "未命名案例")
        case_name = case.get("name", "未命名案例")
        step_count = len(case.get("steps", []))
        return f"""<div class="case-card">
  <div class="case-header" onclick="toggleCase(this)">
    <div class="case-title"><span class="case-id">{esc(case_id)}</span><span class="case-name">{esc(case_name)}</span></div>
    <span class="badge badge-{status_class(result)}">{esc(result)}</span>
  </div>
  <div class="case-body">
    <div class="case-section"><div class="case-section-title">测试场景</div><p>{esc(case.get('scenario', ''))}</p></div>
    <div class="case-section"><div class="case-section-title">断言条件</div><ul>{assertions}</ul></div>
    <div class="case-section"><div class="case-section-title">实际结果</div><p>{esc(case.get('actual', ''))}</p></div>
    <div class="case-section"><div class="case-section-title">测试步骤（共 {step_count} 步）</div>{steps}</div>
    {nested_cards}
  </div>
</div>"""

    badge_row = "".join(
        f'<span class="badge badge-{status_class(case.get("result", ""))}">{esc(case.get("id", "未命名案例"))}: {esc(case.get("result", "未提供"))}</span>'
        for case in cases
    )
    all_cases = "".join(case_html(case) for case in cases)
    top_cards = card_section(cards, "顶层卡牌验证")

    nav_path = meta.get("navigation_path", "")
    nav_html = ""
    if nav_path:
        nav_html = f"""<div class="case-card">
<div class="case-header" onclick="toggleCase(this)">
  <div class="case-title"><span class="case-id">流程信息</span><span class="case-name">导航路径（说明，不作为验证结论）</span></div>
</div>
<div class="case-body"><pre class="log" style="margin-top:8px">{esc(nav_path)}</pre></div>
</div>"""

    report_title = config.get("report_title") or meta.get("report_title") or "测试报告"
    run_id = config.get("test_run_id") or "未命名测试"
    subtitle_parts = [str(run_id)]
    runner = meta.get("runner")
    if runner:
        subtitle_parts.append(f"执行器：{runner}")
    project = meta.get("project")
    if project:
        subtitle_parts.append(f"项目：{project}")
    subtitle = " · ".join(subtitle_parts)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{esc(report_title)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",Roboto,sans-serif;background:#0a0a0f;color:#d4d4d4;padding:20px}}
.container{{max-width:1200px;margin:0 auto}}
h1{{font-size:24px;display:flex;align-items:center;gap:12px;margin-bottom:4px}}
.subtitle{{color:#666;font-size:13px;margin-bottom:24px}}
.summary{{display:flex;gap:16px;margin-bottom:24px}}
.summary-card{{flex:1;padding:14px;border-radius:8px;text-align:center;font-size:13px;border:1px solid #2a2a30}}
.summary-card .num{{font-size:28px;font-weight:700}}
.summary-card.pass{{background:#0a1a0f;border-color:#166534;color:#4ade80}}
.summary-card.fail{{background:#1a0a0f;border-color:#991b1b;color:#f87171}}
.summary-card.block{{background:#1a1a0a;border-color:#854d0e;color:#facc15}}
.summary-card.total{{background:#1a1a22;border-color:#2a2a30;color:#999}}
.badge{{display:inline-block;padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap}}
.badge-pass{{background:#0a2a0f;color:#4ade80;border:1px solid #166534}}
.badge-fail{{background:#2a0a0f;color:#f87171;border:1px solid #991b1b}}
.badge-block{{background:#1a1a0a;color:#facc15;border:1px solid #854d0e}}
.badge-unrecorded{{background:#1a1a22;color:#a1a1aa;border:1px solid #52525b}}
.case-card{{background:#14141a;border-radius:8px;margin-bottom:12px;border:1px solid #2a2a30;overflow:hidden}}
.case-header{{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;cursor:pointer;transition:background .15s}}
.case-header:hover{{background:#1a1a22}}
.case-title{{display:flex;align-items:center;gap:10px}}
.case-id{{font-size:11px;color:#666;background:#1a1a22;padding:2px 8px;border-radius:4px;font-family:monospace}}
.case-name{{font-size:15px;font-weight:500}}
.case-body{{padding:0 16px;display:none}}
.case-body.open{{display:block;padding:16px}}
.case-section{{margin-bottom:16px}}
.case-section-title{{font-size:12px;color:#666;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600}}
.case-section p,.case-section li{{font-size:13px;color:#999;line-height:1.5}}
.case-section ul{{padding-left:18px}}
.step{{margin-bottom:6px;border:1px solid #22222a;border-radius:6px;overflow:hidden}}
.step-header{{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;cursor:pointer;font-size:13px;transition:background .15s}}
.step-header:hover{{background:#1a1a22}}
.step-name{{color:#aaa}}
.step-evidence{{display:none;padding:10px 12px;background:#0f0f15;border-top:1px solid #22222a}}
.step-evidence p{{font-size:12px;color:#888;margin-bottom:6px}}
pre.log{{background:#050510;color:#6ee;padding:8px;border-radius:4px;font-size:11px;overflow-x:auto;font-family:"SF Mono",Menlo,monospace;white-space:pre-wrap}}
.img-row{{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}}
.img-row img{{max-width:100%;height:auto;border-radius:4px;border:1px solid #22222a}}
.img-box{{flex:1;min-width:150px}}
.img-label{{font-size:11px;color:#666;margin-bottom:4px}}
.evidence-missing{{font-size:12px;color:#facc15;white-space:pre-wrap}}
</style>
</head><body>
<div class="container">
<h1>{esc(report_title)}</h1>
<div class="subtitle">{esc(subtitle)}</div>
<div class="summary">
  <div class="summary-card pass"><div class="num">{passed}</div>通过</div>
  <div class="summary-card fail"><div class="num">{failed}</div>失败</div>
  <div class="summary-card block"><div class="num">{blocked}</div>阻塞</div>
  <div class="summary-card total"><div class="num">{total}</div>总计</div>
</div>
<div style="display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap">{badge_row}</div>
{top_cards}
<h2 style="font-size:16px;margin-bottom:12px;color:#999">测试案例</h2>
{all_cases}
{nav_html}
<script>
function toggle(e){{var ev=e.parentElement.querySelector('.step-evidence');if(ev)ev.style.display=ev.style.display==='block'?'none':'block'}}
function toggleCase(e){{var body=e.parentElement.querySelector('.case-body');if(body)body.classList.toggle('open')}}
</script>
</div></body></html>"""

def main():
    p = argparse.ArgumentParser(description="Generate HTML test report")
    p.add_argument("--config", required=True, help="JSON config file")
    p.add_argument("--output", required=True, help="Output HTML file path")
    args = p.parse_args()
    
    with open(args.config) as f:
        config = json.load(f)
    config["_config_dir"] = os.path.dirname(os.path.abspath(args.config))
    
    try:
        report_html = gen(config)
    except ValueError as exc:
        p.error(str(exc))
    with open(args.output, "w") as f:
        f.write(report_html)
    print(f"报告已生成: {args.output}")

if __name__ == "__main__":
    main()
