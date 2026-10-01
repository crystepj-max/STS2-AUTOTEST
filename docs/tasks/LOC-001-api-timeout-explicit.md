> 本任务属本地轨道，GitHub 恢复后需补建 issue；基线确认依据：用户在本轮指令中预先授权「未决产品事项为 0 的候选使用单任务工作流完成深化」。

# LOC-001 api_timeout 显式化

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-001 |
| 需求来源 | 会话录入（CONTEXT.md 债务标记） |
| 任务类型 | enhancement（架构深化） |
| 优先级 | sized-s |
| 当前状态 | 已交付（提交见 registry runs） |
| 需求基线 | V1 |
| 前置依赖 | 无 |
| 施工环境组 | 独立（feat/architecture-deepening worktree） |
| 施工环境角色 | 独立 |
| 无人值守许可 | 允许 |
| 任务规格 | .scratch/LOC-001-api-timeout-explicit/（本文件含三要素） |
| GitHub 同步 | pending |

## 任务目标
消除对 lifecycle 实例 `api_timeout` 属性的跨对象强写：环境预检的等待窗上限
改经 `ensure_environment_ready(api_timeout=...)` 显式传参，行为等价。

## 涉及范围
- `core/lifecycle.py`：`ensure_environment_ready` 增加 `api_timeout: float | None = None`
- `core/run_executor.py`：`_run_environment_precheck` 删除属性强写，改传参
- 不做：其他 api_timeout 使用点（wait_for_controllable 等已有正确模式）

## 验收标准
1. 仓库内不存在对 `lifecycle.api_timeout` 的赋值（grep 为零）
2. 预检路径仍以 min(实例值, 180.0) 作为等待窗（单测断言传参值）
3. 相关单测全过；mypy --strict / lint-imports / ruff 通过
