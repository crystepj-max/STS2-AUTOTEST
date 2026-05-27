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
    enter_combat,
    game_reached_state,
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
# Given: 当前事件为开局祝福事件
def test_tc_resolve_neow(autotest, _session_loop):
    """处理开局祝福事件"""
    result = (
        define("TC-RESOLVE-NEOW", autotest, _session_loop)
        .require_start_state("""- 已进入新 run
- 当前位于开局事件界面，且事件可交互""")
        .setup(
        )
        .execute(
            choose_event(0),
        )
        .assert_that(
            no_crash_detected(),
            game_reached_state(GameScreen.MAP),
            has_travelable_node(),
        )
    )
    failure_context = {
        "case_id": "TC-RESOLVE-NEOW",
        "title": "处理开局祝福事件",
        "start_state": "- 已进入新 run\n- 当前位于开局事件界面，且事件可交互",
        "end_state": "- 事件处理完成\n- 当前位于地图界面，且首个节点可选",
        "steps": ["选择开局事件的第 0 个选项"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)