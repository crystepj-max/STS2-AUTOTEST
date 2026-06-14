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

# Given: 处于战斗状态
def test_tc_gawain_emergency_recruit(autotest, _session_loop):
    """紧急征召"""
    result = (
        define("TC-GAWAIN-EMERGENCY-RECRUIT", autotest, _session_loop)
        .require_start_state("""- 处于 COMBAT 界面
- 手牌包含 gawain:emergency_recruit
- 能量 >= 1""")
        .setup(
        )
        .execute(
            play_card("gawain:emergency_recruit"),
        )
        .assert_that(
            no_crash_detected(),
        )
    )
    failure_context = {
        "case_id": "TC-GAWAIN-EMERGENCY-RECRUIT",
        "title": "紧急征召",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:emergency_recruit\n- 能量 >= 1",
        "end_state": "- gawain:emergency_recruit 已打出并消耗\n- 手牌数增加 1（抽牌效果）\n- 能量消耗 1 点",
        "steps": ["使用 gawain:emergency_recruit"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)