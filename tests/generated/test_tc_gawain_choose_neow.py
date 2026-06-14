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

# Given: 已完成角色选择和 embark（TC-GAWAIN-PREPARE-NEW-RUN）
# Given: 处于涅奥开局事件
def test_tc_gawain_choose_neow(autotest, _session_loop):
    """选择涅奥祝福"""
    result = (
        define("TC-GAWAIN-CHOOSE-NEOW", autotest, _session_loop)
        .require_start_state("""- 位于 EVENT 界面（涅奥/Neow 开局事件）
- 已选择 Gawain 角色
- 事件选项已展示""")
        .setup(
        )
        .execute(
            choose_event(0),
        )
        .assert_that(
            game_reached_state(GameScreen.MAP),
            no_crash_detected(),
        )
    )
    failure_context = {
        "case_id": "TC-GAWAIN-CHOOSE-NEOW",
        "title": "选择涅奥祝福",
        "start_state": "- 位于 EVENT 界面（涅奥/Neow 开局事件）\n- 已选择 Gawain 角色\n- 事件选项已展示",
        "end_state": "- 离开事件界面，进入 MAP 界面",
        "steps": ["开局事件 第 0 个选项"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)