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

# Given: 已安装并可连接 STS2-Cli-Mod
# Given: 首次战斗节点可被选择
def test_tc_finish_first_battle(autotest, _session_loop):
    """完成首次战斗"""
    result = (
        define("TC-FINISH-FIRST-BATTLE", autotest, _session_loop)
        .require_start_state("""- 当前位于地图界面
- 存在至少一个可到达的普通战斗节点""")
        .setup(
            choose_map_node(2, 1),
            enter_combat(),
            combat_basic_policy(),
        )
        .execute(
            skip_card_reward(),
        )
        .assert_that(
            no_crash_detected(),
            game_reached_state(GameScreen.MAP),
        )
    )
    failure_context = {
        "case_id": "TC-FINISH-FIRST-BATTLE",
        "title": "完成首次战斗",
        "start_state": "- 当前位于地图界面\n- 存在至少一个可到达的普通战斗节点",
        "end_state": "- 首次战斗结束\n- 当前位于奖励界面或地图界面",
        "steps": ["选择地图节点 (2, 1)", "进入首次战斗", "按基础策略完成战斗", "跳过卡牌奖励"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)