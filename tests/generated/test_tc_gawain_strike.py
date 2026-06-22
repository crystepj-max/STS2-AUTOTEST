import json


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    no_crash_detected,
    play_card,
)

# Given: 处于战斗状态
def test_tc_gawain_strike(autotest, _session_loop):
    """打击（Gawain）"""
    result = (
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
    failure_context = {
        "case_id": "TC-GAWAIN-STRIKE",
        "title": "打击（Gawain）",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:strike\n- 能量 >= 1\n- 场上存在敌方目标",
        "end_state": "- gawain:strike 已打出\n- 目标敌人受到 6 点伤害\n- 能量消耗 1 点",
        "steps": ["使用 gawain:strike"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)