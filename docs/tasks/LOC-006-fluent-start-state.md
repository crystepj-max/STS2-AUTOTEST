> 本任务属本地轨道，GitHub 恢复后需补建 issue；基线确认依据：用户在本轮指令中预先授权「未决产品事项为 0 的候选使用单任务工作流完成深化」。

# LOC-006 fluent 魔法子串清除

| 字段 | 值 |
|---|---|
| 任务标识 | LOC-006 |
| 需求来源 | 会话录入（CONTEXT.md 债务：fluent 魔法子串正则） |
| 任务类型 | enhancement（架构深化） |
| 优先级 | sized-m |
| 当前状态 | 已交付 |
| 需求基线 | V1 |
| 前置依赖 | 无 |
| 施工环境组 / 角色 | 独立 |
| 无人值守许可 | 允许 |
| 任务规格 | .scratch/LOC-006-fluent-start-state/definition-check.md |
| GitHub 同步 | pending |

## 任务目标
规格 Given 的启动校验从「运行时中文正则」迁移为「生成期结构化要求」，
消除生成测试对魔法子串的隐式依赖；规格作者写作体验不变。

## 涉及范围
code_generator（生成期解析 + 结构化字面量产出）、fluent（消费结构化参数，
正则保留为手工测试回退）、tests/generated（经 autotest compile 重生成）。
不做：正则谓词的语义变更、Given 文本格式改版。

## 验收标准
1. 新生成的测试以结构化要求驱动 setup 校验，正则仅回退使用
2. docs/process/specs 全部规格旧/新解析等价对比通过
3. 重生成后的 tests/generated 全量单测通过；三件套通过
