import json
from pathlib import Path


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    choose_event,
    choose_map_node,
    embark,
    enter_combat,
    game_reached_state,
    no_crash_detected,
    play_card,
    select_character,
    start_new_run,
)
from sts2_autotest.common.state import GameScreen

def test_suite_gawain_smoke(autotest, _session_loop):
    """Gawain 冒烟测试"""
    # Goal: - 验证 Gawain Mod 完整链路：开始新局 → 选择 Gawain → embark → 涅奥祝福 → 导航首战 → 依次打出 5 张初始卡牌
    # Execution mode: sequential_shared_session
    # Suite assertion: 整条链路连续完成，无崩溃、无软锁
    # Suite assertion: 所有初始卡牌可正常打出且行为符合当前实现
    suite_results = []
    summary_path = Path(__file__).resolve().parent.parent / "output" / "suite-summaries" / "SUITE-GAWAIN-SMOKE.json"

    def _write_suite_summary():
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        first_failed = next((item for item in suite_results if not item['passed']), None)
        summary = {
            "suite_id": "SUITE-GAWAIN-SMOKE",
            "title": "Gawain 冒烟测试",
            "total": len(suite_results),
            "passed": sum(1 for item in suite_results if item['passed']),
            "failed": sum(1 for item in suite_results if not item['passed']),
            "first_failed_case_id": first_failed['case_id'] if first_failed else None,
            "cases": suite_results,
        }
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    # Given (TC-GAWAIN-PREPARE-NEW-RUN): Gawain Mod 已加载
    # Case: TC-GAWAIN-PREPARE-NEW-RUN - 准备新一局
    result_tc_gawain_prepare_new_run = (
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
    case_summary = {
        "case_id": "TC-GAWAIN-PREPARE-NEW-RUN",
        "title": "准备新一局",
        "start_state": "- 游戏已启动，位于主菜单",
        "end_state": "- 选择 Gawain 角色并 embark，进入 EVENT 界面（涅奥开局事件）",
        "steps": ["开始新局", "选择 Gawain", "开始冒险"],
        "passed": result_tc_gawain_prepare_new_run.passed,
        "failures": result_tc_gawain_prepare_new_run.failures,
        "detail": result_tc_gawain_prepare_new_run.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_prepare_new_run.passed, "TC-GAWAIN-PREPARE-NEW-RUN failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-CHOOSE-NEOW): 已完成角色选择和 embark（TC-GAWAIN-PREPARE-NEW-RUN）
    # Given (TC-GAWAIN-CHOOSE-NEOW): 处于涅奥开局事件
    # Case: TC-GAWAIN-CHOOSE-NEOW - 选择涅奥祝福
    result_tc_gawain_choose_neow = (
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
    case_summary = {
        "case_id": "TC-GAWAIN-CHOOSE-NEOW",
        "title": "选择涅奥祝福",
        "start_state": "- 位于 EVENT 界面（涅奥/Neow 开局事件）\n- 已选择 Gawain 角色\n- 事件选项已展示",
        "end_state": "- 离开事件界面，进入 MAP 界面",
        "steps": ["开局事件 第 0 个选项"],
        "passed": result_tc_gawain_choose_neow.passed,
        "failures": result_tc_gawain_choose_neow.failures,
        "detail": result_tc_gawain_choose_neow.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_choose_neow.passed, "TC-GAWAIN-CHOOSE-NEOW failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-NAVIGATE-TO-COMBAT): 已完成涅奥祝福选择（TC-GAWAIN-CHOOSE-NEOW）
    # Given (TC-GAWAIN-NAVIGATE-TO-COMBAT): 处于 Act 1 地图
    # Case: TC-GAWAIN-NAVIGATE-TO-COMBAT - 导航至首战
    result_tc_gawain_navigate_to_combat = (
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
    case_summary = {
        "case_id": "TC-GAWAIN-NAVIGATE-TO-COMBAT",
        "title": "导航至首战",
        "start_state": "- 位于 MAP 界面（已通过涅奥事件）",
        "end_state": "- 进入 COMBAT 界面",
        "steps": ["选择地图节点 (1, 0)", "进入首场战斗"],
        "passed": result_tc_gawain_navigate_to_combat.passed,
        "failures": result_tc_gawain_navigate_to_combat.failures,
        "detail": result_tc_gawain_navigate_to_combat.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_navigate_to_combat.passed, "TC-GAWAIN-NAVIGATE-TO-COMBAT failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-STRIKE): 处于战斗状态
    # Case: TC-GAWAIN-STRIKE - 打击（Gawain）
    result_tc_gawain_strike = (
        define("TC-GAWAIN-STRIKE", autotest, _session_loop)
        .require_start_state("""- 处于 COMBAT 界面
- 手牌包含 gawain:strike
- 能量 >= 1
- 场上存在敌方目标""")
        .setup(
        )
        .execute(
            play_card("gawain:strike"),
        )
        .assert_that(
            no_crash_detected(),
        )
    )
    case_summary = {
        "case_id": "TC-GAWAIN-STRIKE",
        "title": "打击（Gawain）",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:strike\n- 能量 >= 1\n- 场上存在敌方目标",
        "end_state": "- gawain:strike 已打出\n- 目标敌人受到 6 点伤害\n- 能量消耗 1 点",
        "steps": ["使用 gawain:strike"],
        "passed": result_tc_gawain_strike.passed,
        "failures": result_tc_gawain_strike.failures,
        "detail": result_tc_gawain_strike.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_strike.passed, "TC-GAWAIN-STRIKE failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-DEFEND): 处于战斗状态
    # Case: TC-GAWAIN-DEFEND - 防御（Gawain）
    result_tc_gawain_defend = (
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
    case_summary = {
        "case_id": "TC-GAWAIN-DEFEND",
        "title": "防御（Gawain）",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:defend\n- 能量 >= 1",
        "end_state": "- gawain:defend 已打出\n- 玩家获得 5 点格挡\n- 能量消耗 1 点",
        "steps": ["使用 gawain:defend"],
        "passed": result_tc_gawain_defend.passed,
        "failures": result_tc_gawain_defend.failures,
        "detail": result_tc_gawain_defend.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_defend.passed, "TC-GAWAIN-DEFEND failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-EMERGENCY-RECRUIT): 处于战斗状态
    # Case: TC-GAWAIN-EMERGENCY-RECRUIT - 紧急征召
    result_tc_gawain_emergency_recruit = (
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
    case_summary = {
        "case_id": "TC-GAWAIN-EMERGENCY-RECRUIT",
        "title": "紧急征召",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:emergency_recruit\n- 能量 >= 1",
        "end_state": "- gawain:emergency_recruit 已打出并消耗\n- 手牌数增加 1（抽牌效果）\n- 能量消耗 1 点",
        "steps": ["使用 gawain:emergency_recruit"],
        "passed": result_tc_gawain_emergency_recruit.passed,
        "failures": result_tc_gawain_emergency_recruit.failures,
        "detail": result_tc_gawain_emergency_recruit.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_emergency_recruit.passed, "TC-GAWAIN-EMERGENCY-RECRUIT failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-MAGIC-DRAW): 处于战斗状态
    # Case: TC-GAWAIN-MAGIC-DRAW - 魔力汲取
    result_tc_gawain_magic_draw = (
        define("TC-GAWAIN-MAGIC-DRAW", autotest, _session_loop)
        .require_start_state("""- 处于 COMBAT 界面
- 手牌包含 gawain:magic_draw""")
        .setup(
        )
        .execute(
            play_card("gawain:magic_draw"),
        )
        .assert_that(
            no_crash_detected(),
        )
    )
    case_summary = {
        "case_id": "TC-GAWAIN-MAGIC-DRAW",
        "title": "魔力汲取",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:magic_draw",
        "end_state": "- gawain:magic_draw 已打出\n- 牌正常消耗",
        "steps": ["使用 gawain:magic_draw"],
        "passed": result_tc_gawain_magic_draw.passed,
        "failures": result_tc_gawain_magic_draw.failures,
        "detail": result_tc_gawain_magic_draw.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_magic_draw.passed, "TC-GAWAIN-MAGIC-DRAW failed: " + json.dumps(case_summary, ensure_ascii=False)
    # Given (TC-GAWAIN-PORTABLE-TERMINAL): 处于战斗状态
    # Case: TC-GAWAIN-PORTABLE-TERMINAL - 便携魔导终端
    result_tc_gawain_portable_terminal = (
        define("TC-GAWAIN-PORTABLE-TERMINAL", autotest, _session_loop)
        .require_start_state("""- 处于 COMBAT 界面
- 手牌包含 gawain:portable_magic_terminal""")
        .setup(
        )
        .execute(
            play_card("gawain:portable_magic_terminal"),
        )
        .assert_that(
            no_crash_detected(),
        )
    )
    case_summary = {
        "case_id": "TC-GAWAIN-PORTABLE-TERMINAL",
        "title": "便携魔导终端",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:portable_magic_terminal",
        "end_state": "- gawain:portable_magic_terminal 已打出\n- 游戏不崩溃",
        "steps": ["使用 gawain:portable_magic_terminal"],
        "passed": result_tc_gawain_portable_terminal.passed,
        "failures": result_tc_gawain_portable_terminal.failures,
        "detail": result_tc_gawain_portable_terminal.detail,
    }
    suite_results.append(case_summary)
    _write_suite_summary()
    assert result_tc_gawain_portable_terminal.passed, "TC-GAWAIN-PORTABLE-TERMINAL failed: " + json.dumps(case_summary, ensure_ascii=False)