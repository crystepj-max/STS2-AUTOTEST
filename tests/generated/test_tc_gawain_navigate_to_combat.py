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

# Given: 已完成涅奥祝福选择（TC-GAWAIN-CHOOSE-NEOW）
# Given: 处于 Act 1 地图
def test_tc_gawain_navigate_to_combat(autotest, _session_loop):
    """导航至首战"""
    result = (
        define("TC-GAWAIN-NAVIGATE-TO-COMBAT", autotest, _session_loop)
        .require_start_state("""- 位于 MAP 界面（已通过涅奥事件）""")
        .setup(
            choose_map_node(1, 0),
        )
        .execute(
            enter_combat(),
        )
        .assert_that(
            game_reached_state(GameScreen.COMBAT),
            no_crash_detected(),
        )
    )
    failure_context = {
        "case_id": "TC-GAWAIN-NAVIGATE-TO-COMBAT",
        "title": "导航至首战",
        "start_state": "- 位于 MAP 界面（已通过涅奥事件）",
        "end_state": "- 进入 COMBAT 界面",
        "steps": ["选择地图节点 (1, 0)", "进入首场战斗"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)