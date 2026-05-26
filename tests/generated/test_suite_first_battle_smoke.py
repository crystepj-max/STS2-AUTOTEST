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

def test_suite_first_battle_smoke(autotest, _session_loop):
    """首次战斗冒烟"""
    # Goal: - 验证从启动游戏到完成首次战斗的完整主链路可用
    # Execution mode: sequential_shared_session
    # Suite assertion: 整条链路应可连续完成
    # Suite assertion: 任一子用例失败时应给出失败位置
    suite_results = []
    summary_path = Path('tests/output/suite-summaries') / "SUITE-FIRST-BATTLE-SMOKE.json"

    def _write_suite_summary():
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        first_failed = next((item for item in suite_results if not item['passed']), None)
        summary = {
            "suite_id": "SUITE-FIRST-BATTLE-SMOKE",
            "title": "首次战斗冒烟",
            "total": len(suite_results),
            "passed": sum(1 for item in suite_results if item['passed']),
            "failed": sum(1 for item in suite_results if not item['passed']),
            "first_failed_case_id": first_failed['case_id'] if first_failed else None,
            "cases": suite_results,
        }
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    # Given (TC-PREPARE-NEW-RUN): 已安装并可连接 STS2-Cli-Mod
    # Given (TC-PREPARE-NEW-RUN): 游戏可被启动
    # Given (TC-PREPARE-NEW-RUN): 如存在旧 run，框架应负责回收并回到可重新开局状态
    # Case: TC-PREPARE-NEW-RUN - 进入新局地图
    result_tc_prepare_new_run = (
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
            # TODO: implement assertion for '最终应位于地图界面'
            # TODO: implement assertion for '应能识别至少一个可达节点'
        )
    )
    case_summary = {
        "case_id": "TC-PREPARE-NEW-RUN",
        "title": "进入新局地图",
        "start_state": "- 任意可恢复状态\n- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN",
        "end_state": "- 到达 Act 1 地图\n- 当前可选择首个可达节点",
        "steps": ["返回主菜单", "开始新 run", "选择 Ironclad", "开始冒险", "推进事件对话"],
        "passed": result_tc_prepare_new_run.passed,
        "failures": result_tc_prepare_new_run.failures,
        "detail": result_tc_prepare_new_run.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_prepare_new_run.passed, "TC-PREPARE-NEW-RUN failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-RESOLVE-NEOW): 已安装并可连接 STS2-Cli-Mod
    # Given (TC-RESOLVE-NEOW): 当前事件为开局祝福事件
    # Case: TC-RESOLVE-NEOW - 处理开局祝福事件
    result_tc_resolve_neow = (
        define("TC-RESOLVE-NEOW", autotest, _session_loop)
        .require_start_state("""- 已进入新 run
- 当前位于开局事件界面，且事件可交互""")
        .setup(
            choose_event(0),
        )
        .execute(
            advance_dialogue(),
        )
        .assert_that(
            no_crash_detected(),
            # TODO: implement assertion for '最终应位于地图界面'
            # TODO: implement assertion for '应能识别至少一个可达节点'
        )
    )
    case_summary = {
        "case_id": "TC-RESOLVE-NEOW",
        "title": "处理开局祝福事件",
        "start_state": "- 已进入新 run\n- 当前位于开局事件界面，且事件可交互",
        "end_state": "- 事件处理完成\n- 当前位于地图界面，且首个节点可选",
        "steps": ["选择开局事件的第 0 个选项", "推进事件对话"],
        "passed": result_tc_resolve_neow.passed,
        "failures": result_tc_resolve_neow.failures,
        "detail": result_tc_resolve_neow.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_resolve_neow.passed, "TC-RESOLVE-NEOW failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-FINISH-FIRST-BATTLE): 已安装并可连接 STS2-Cli-Mod
    # Given (TC-FINISH-FIRST-BATTLE): 首次战斗节点可被选择
    # Case: TC-FINISH-FIRST-BATTLE - 完成首次战斗
    result_tc_finish_first_battle = (
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
            # TODO: implement assertion for '战斗结束后应回到奖励界面或地图界面'
        )
    )
    case_summary = {
        "case_id": "TC-FINISH-FIRST-BATTLE",
        "title": "完成首次战斗",
        "start_state": "- 当前位于地图界面\n- 存在至少一个可到达的普通战斗节点",
        "end_state": "- 首次战斗结束\n- 当前位于奖励界面或地图界面",
        "steps": ["选择地图节点 (2, 1)", "进入首次战斗", "按基础策略完成战斗", "跳过卡牌奖励"],
        "passed": result_tc_finish_first_battle.passed,
        "failures": result_tc_finish_first_battle.failures,
        "detail": result_tc_finish_first_battle.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_finish_first_battle.passed, "TC-FINISH-FIRST-BATTLE failed: " + json.dumps(case_summary, ensure_ascii=False)