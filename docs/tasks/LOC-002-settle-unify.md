> 本任务属本地轨道，GitHub 恢复后需补建 issue；基线确认依据：用户在本轮指令中预先授权「未决产品事项为 0 的候选使用单任务工作流完成深化」。

# LOC-002 settle 稳定口径收敛（指纹单源）

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-002 |
| 需求来源 | 会话录入（CONTEXT.md 债务：settle 四口径） |
| 任务类型 | enhancement（架构深化） |
| 优先级 | sized-s |
| 当前状态 | 已交付 |
| 需求基线 | V1 |
| 前置依赖 | 无 |
| 施工环境组 / 角色 | 独立 |
| 无人值守许可 | 允许 |
| 任务规格 | .scratch/LOC-002-settle-unify/definition-check.md |
| GitHub 同步 | pending |

## 任务目标
状态指纹函数收敛为 `common/state.py` 的单一实现；navigation/journeys 的
私有副本删除，公开名改指向单源。

## 涉及范围
- `common/state.py`：新增 `state_fingerprint(state)`（逐字取自现实现）
- `core/journeys.py`：`_fingerprint`/`state_fingerprint` 改为引用 common
- `core/navigation.py`：`_state_fingerprint` 改为引用 common
- 不做：四种 settle 口径的行为合并（时序语义不同，判定为不做）

## 验收标准
1. 仓库内指纹比较逻辑仅剩 common/state.py 一处实现
2. 相关单测全过；三件套通过
