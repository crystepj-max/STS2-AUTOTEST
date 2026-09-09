> 本任务属本地轨道，GitHub 恢复后需补建 issue；基线确认依据：用户在本轮指令中预先授权「未决产品事项为 0 的候选使用单任务工作流完成深化」。

# LOC-003 run-result 路径常量单源

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-003 |
| 需求来源 | 会话录入（CONTEXT.md 债务：run-result.json 副本） |
| 任务类型 | enhancement（架构深化） |
| 优先级 | sized-s |
| 当前状态 | 本地已定义 |
| 需求基线 | V1 |
| 前置依赖 | 无 |
| 施工环境组 / 角色 | 独立 |
| 无人值守许可 | 允许 |
| 任务规格 | .scratch/LOC-003-run-result-path/definition-check.md |
| GitHub 同步 | pending |

## 任务目标
`run-result.json` 文件名收敛为 `core/run_service.RUN_RESULT_FILENAME` 单源常量。

## 涉及范围
run_executor / run_service / mcp_tools / cli-main 中该文件名字面量全部改引常量；
不做：目录布局（{root}/{run_id}/reports 与 .runs 镜像）的结构化改造。

## 验收标准
1. `"run-result.json"` 字面量仅存在于常量定义处（docstring/注释除外）
2. 相关单测全过；三件套通过
