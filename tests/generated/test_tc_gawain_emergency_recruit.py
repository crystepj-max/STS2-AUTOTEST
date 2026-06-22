import json


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    no_crash_detected,
    play_card,
)

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