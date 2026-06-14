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
def test_tc_gawain_defend(autotest, _session_loop):
    """防御（Gawain）"""
    result = (
        define("TC-GAWAIN-DEFEND", autotest, _session_loop)
        .require_start_state("""- 处于 COMBAT 界面
- 手牌包含 gawain:defend
- 能量 >= 1""")
        .setup(
        )
        .execute(
            play_card("gawain:defend"),
        )
        .assert_that(
            no_crash_detected(),
        )
    )
    failure_context = {
        "case_id": "TC-GAWAIN-DEFEND",
        "title": "防御（Gawain）",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:defend\n- 能量 >= 1",
        "end_state": "- gawain:defend 已打出\n- 玩家获得 5 点格挡\n- 能量消耗 1 点",
        "steps": ["使用 gawain:defend"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)