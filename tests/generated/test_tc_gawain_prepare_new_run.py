import json
from pathlib import Path

import pytest

from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    advance_dialogue,
    choose_event,
    choose_game_mode,
    choose_map_node,
    combat_basic_policy,
    embark,
    end_turn,
    enemy_took_exact_hits,
    enter_combat,
    game_reached_state,
    give_card,
    no_crash_detected,
    has_travelable_node,
    play_card,
    return_to_menu,
    select_character,
    skip_card_reward,
    start_new_run,
)
from sts2_autotest.common.state import GameScreen
from sts2_autotest.core.action_model import ActionDescriptor

# Given: Gawain Mod 已加载
def test_tc_gawain_prepare_new_run(autotest, _session_loop):
    """准备新一局"""
    result = (
        define("TC-GAWAIN-PREPARE-NEW-RUN", autotest, _session_loop)
        .require_start_state("""- 游戏已启动，位于主菜单""")
        .setup(
            start_new_run(),
            select_character("gawain"),
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