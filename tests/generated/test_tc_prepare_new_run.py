import json
from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    advance_dialogue,
    embark,
    game_reached_state,
    no_crash_detected,
    has_travelable_node,
    return_to_menu,
    select_character,
    start_new_run,
)
from sts2_autotest.common.state import GameScreen

# Given: 已安装并可连接 STS2-Cli-Mod
# Given: 游戏可被启动
# Given: 如存在旧 run，框架应负责回收并回到可重新开局状态
def test_tc_prepare_new_run(autotest, _session_loop):
    """进入新局地图"""
    result = (
        define("TC-PREPARE-NEW-RUN", autotest, _session_loop)
        .require_start_state("""- 任意可恢复状态
- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN""")
        .setup(
            return_to_menu(),
            start_new_run(),
            select_character("IRONCLAD"),
            embark(),
        )
        .execute(
            advance_dialogue(),
        )
        .assert_that(
            no_crash_detected(),
            game_reached_state(GameScreen.MAP),
            has_travelable_node(),
        )
    )
    failure_context = {
        "case_id": "TC-PREPARE-NEW-RUN",
        "title": "进入新局地图",
        "start_state": "- 任意可恢复状态\n- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN",
        "end_state": "- 到达 Act 1 地图\n- 当前可选择首个可达节点",
        "steps": ["返回主菜单", "开始新 run", "选择 Ironclad", "开始冒险", "推进事件对话"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)