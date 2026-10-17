---
name: test-report-html
description: 生成标准化 HTML 测试报告，包含测试案例、测试步骤、可点击展开的凭证（截图、状态 JSON、日志）、总体统计汇总。适用于完成冒烟测试或回归测试后产出测试报告的场景。
---

# 测试报告 HTML 生成

使用本技能自带的 `scripts/gen_report.py` 生成中文 HTML 测试报告。配置字段结构见脚本开头的 JSON 示例；顶层与测试案例内的 `card_results` 都会展示。截图、状态 JSON 和步骤日志的本地文件路径以配置 JSON 所在目录为相对根目录；缺少或无法读取的文件会明确标出，不会伪装成已找到凭证。`metadata.navigation_path` 只是流程说明，不计作测试案例或“通过”结论。标题、执行器和项目名称只有在配置中提供时才显示专用值；缺少步骤 `result` 时显示“未记录”，不会默认标为通过。若目标项目提供 `ARTIFACT_TEST_REPORT.md`，再按该项目模板补齐，不把未随技能分发的模板当作运行依赖。

## 报告结构

- 顶栏汇总：通过 / 失败 / 阻塞 / 总计
- 测试案例标签列表
- 每个案例可点击展开，包含：测试场景、断言条件、实际结果、测试步骤
- 每个步骤可点击展开凭证：截图、状态 JSON、API 日志

## 脚本使用

```bash
SKILL_DIR="<directory containing this SKILL.md>"
python3 "$SKILL_DIR/scripts/gen_report.py" --config test-results.json --output test-report.html
```

## 输出路径约定

当当前任务位于 STS2-GAWAIN 自动化测试项目时，沿用 `automation/autotest/output/{task-id}/test-report.html`。其他项目把报告写入用户指定位置，或当前工作区的 `artifacts/test-report.html`；先确认目标目录属于当前项目，不要写入固定的其他仓库路径。每个测试案例必须提供 `result`，且值只能是 `通过`、`失败` 或 `阻塞`；缺失或不支持的值会让 CLI 报错并停止生成本次报告。步骤级 `result` 可以缺省，届时显示中性的“未记录”。
