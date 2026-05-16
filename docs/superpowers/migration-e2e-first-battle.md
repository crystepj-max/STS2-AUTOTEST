# e2e_first_battle.py 迁移计划

## 目标

将 `tests/e2e_first_battle.py` 从旁路脚本迁移为框架主链路的规格+生成测试。

## 现状

`tests/e2e_first_battle.py` 是直接操作 adapter 的旁路脚本，不走框架主链路。

## 迁移步骤

1. **拆为 Markdown 规格**：将脚本中的业务逻辑拆为 3 个最小测试用例规格：
   - `TC-PREPARE-NEW-RUN` — 进入新局地图
   - `TC-RESOLVE-NEOW` — 处理开局事件
   - `TC-FINISH-FIRST-BATTLE` — 完成首场战斗

2. **补充 DSL 动作原语**：在 `dsl/assertions.py` 中添加规格步骤所需的 DSL 函数（如 `advance_until_map`、`combat_loop` 等）

3. **生成 pytest 测试**：使用 `autotest compile` 从规格生成 pytest 测试文件

4. **验证**：运行生成的测试，确认行为与原始脚本一致

5. **删除旁路脚本**：确认新链路正常工作后删除 `tests/e2e_first_battle.py`

## 状态

- [ ] Markdown 规格已编写
- [ ] DSL 原语已补充
- [ ] 测试已生成并验证
- [ ] 旁路脚本已删除
