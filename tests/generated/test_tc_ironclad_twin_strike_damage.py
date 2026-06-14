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
# Given: 游戏可被启动并加载到主菜单
# Given: 使用原游戏角色 Ironclad（战士）
# Given: 双重打击的原版卡牌 ID 为 TWIN_STRIKE
# Given: 双重打击的原版基线为 damage=5、hit_count=2
def test_tc_ironclad_twin_strike_damage(autotest, _session_loop):
    """战士双重打击伤害验证"""
    result = (
        define("TC-IRONCLAD-TWIN-STRIKE-DAMAGE", autotest, _session_loop)
        .require_start_state("""- 任意可恢复状态
- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN""")
        .setup(
            return_to_menu(),
            start_new_run(),
            select_character("IRONCLAD"),
            embark(),
            choose_event(0),
            choose_map_node(2, 1),
            enter_combat(),
            give_card("TWIN_STRIKE"),
        )
        .execute(
            play_card("TWIN_STRIKE"),
        )
        .assert_that(
            no_crash_detected(),
            game_reached_state(GameScreen.COMBAT),
            enemy_took_exact_hits(5, 2),
        )
    )
    failure_context = {
        "case_id": "TC-IRONCLAD-TWIN-STRIKE-DAMAGE",
        "title": "战士双重打击伤害验证",
        "start_state": "- 任意可恢复状态\n- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN",
        "end_state": "- 当前位于战斗界面\n- 已尝试打出 TWIN_STRIKE\n- 伤害事件应记录为 5 点伤害 2 次",
        "steps": ["返回主菜单", "开始新 run", "选择战士", "开始冒险", "选择开局事件的第 0 个选项", "选择地图节点 (2, 1)", "进入首次战斗", "添加 TWIN_STRIKE 到手牌", "使用 TWIN_STRIKE"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)