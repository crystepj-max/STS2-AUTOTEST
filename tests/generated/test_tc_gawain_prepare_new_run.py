import json


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    embark,
    game_reached_state,
    no_crash_detected,
    select_character,
    start_new_run,
)
from sts2_autotest.common.state import GameScreen

# Given: Gawain Mod 已加载
def test_tc_gawain_prepare_new_run(autotest, _session_loop):
    """准备新一局"""
    result = (
        define("TC-GAWAIN-PREPARE-NEW-RUN", autotest, _session_loop)
        .require_start_state("""- 游戏已启动，位于主菜单""")
        .setup(
            start_new_run(),
            select_character("GAWAINMOD-GAWAIN"),
        )
        .execute(
            embark(),
        )
        .assert_that(
            game_reached_state(GameScreen.EVENT),
            no_crash_detected(),
        )
    )
    failure_context = {
        "case_id": "TC-GAWAIN-PREPARE-NEW-RUN",
        "title": "准备新一局",
        "start_state": "- 游戏已启动，位于主菜单",
        "end_state": "- 选择 Gawain 角色并 embark，进入 EVENT 界面（涅奥开局事件）",
        "steps": ["开始新局", "选择 Gawain", "开始冒险"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)