import json


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    no_crash_detected,
    play_card,
)

# Given: 处于战斗状态
def test_tc_gawain_magic_draw(autotest, _session_loop):
    """魔力汲取"""
    result = (
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
    failure_context = {
        "case_id": "TC-GAWAIN-MAGIC-DRAW",
        "title": "魔力汲取",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:magic_draw",
        "end_state": "- gawain:magic_draw 已打出\n- 牌正常消耗",
        "steps": ["使用 gawain:magic_draw"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)