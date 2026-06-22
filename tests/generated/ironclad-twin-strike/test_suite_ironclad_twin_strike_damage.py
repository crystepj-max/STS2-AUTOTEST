import json
from pathlib import Path


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    choose_event,
    choose_map_node,
    embark,
    enemy_took_exact_hits,
    enter_combat,
    game_reached_state,
    give_card,
    no_crash_detected,
    play_card,
    return_to_menu,
    select_character,
    start_new_run,
)
from sts2_autotest.common.state import GameScreen

def test_suite_ironclad_twin_strike_damage(autotest, _session_loop):
    """战士双重打击真实流程验证"""
    # Goal: - 验证从启动游戏、进入战士首战、添加双重打击到手牌、打出卡牌，到校验 5 点伤害 2 次的完整自动化链路。
    # Execution mode: sequential_shared_session
    # Suite assertion: 测试规格应可被 review 和 compile
    # Suite assertion: 真实运行应给出通过、失败或运行时阻塞的明确证据
    suite_results = []
    summary_path = Path(__file__).resolve().parent.parent / "output" / "suite-summaries" / "SUITE-IRONCLAD-TWIN-STRIKE-DAMAGE.json"

    def _write_suite_summary():
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        first_failed = next((item for item in suite_results if not item['passed']), None)
        summary = {
            "suite_id": "SUITE-IRONCLAD-TWIN-STRIKE-DAMAGE",
            "title": "战士双重打击真实流程验证",
            "total": len(suite_results),
            "passed": sum(1 for item in suite_results if item['passed']),
            "failed": sum(1 for item in suite_results if not item['passed']),
            "first_failed_case_id": first_failed['case_id'] if first_failed else None,
            "cases": suite_results,
        }
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    # Given (TC-IRONCLAD-TWIN-STRIKE-DAMAGE): 已安装并可连接 STS2-Cli-Mod
    # Given (TC-IRONCLAD-TWIN-STRIKE-DAMAGE): 游戏可被启动并加载到主菜单
    # Given (TC-IRONCLAD-TWIN-STRIKE-DAMAGE): 使用原游戏角色 Ironclad（战士）
    # Given (TC-IRONCLAD-TWIN-STRIKE-DAMAGE): 双重打击的原版卡牌 ID 为 TWIN_STRIKE
    # Given (TC-IRONCLAD-TWIN-STRIKE-DAMAGE): 双重打击的原版基线为 damage=5、hit_count=2
    # Case: TC-IRONCLAD-TWIN-STRIKE-DAMAGE - 战士双重打击伤害验证
    result_tc_ironclad_twin_strike_damage = (
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
    case_summary = {
        "case_id": "TC-IRONCLAD-TWIN-STRIKE-DAMAGE",
        "title": "战士双重打击伤害验证",
        "start_state": "- 任意可恢复状态\n- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN",
        "end_state": "- 当前位于战斗界面\n- 已尝试打出 TWIN_STRIKE\n- 伤害事件应记录为 5 点伤害 2 次",
        "steps": ["返回主菜单", "开始新 run", "选择战士", "开始冒险", "选择开局事件的第 0 个选项", "选择地图节点 (2, 1)", "进入首次战斗", "添加 TWIN_STRIKE 到手牌", "使用 TWIN_STRIKE"],
        "passed": result_tc_ironclad_twin_strike_damage.passed,
        "failures": result_tc_ironclad_twin_strike_damage.failures,
        "detail": result_tc_ironclad_twin_strike_damage.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_ironclad_twin_strike_damage.passed, "TC-IRONCLAD-TWIN-STRIKE-DAMAGE failed: " + json.dumps(case_summary, ensure_ascii=False)