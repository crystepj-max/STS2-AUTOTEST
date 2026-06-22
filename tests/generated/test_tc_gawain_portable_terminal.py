import json


from sts2_autotest.dsl.fluent import define
from sts2_autotest.dsl.assertions import (
    no_crash_detected,
    play_card,
)

# Given: 处于战斗状态
def test_tc_gawain_portable_terminal(autotest, _session_loop):
    """便携魔导终端"""
    result = (
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
    failure_context = {
        "case_id": "TC-GAWAIN-PORTABLE-TERMINAL",
        "title": "便携魔导终端",
        "start_state": "- 处于 COMBAT 界面\n- 手牌包含 gawain:portable_magic_terminal",
        "end_state": "- gawain:portable_magic_terminal 已打出\n- 游戏不崩溃",
        "steps": ["使用 gawain:portable_magic_terminal"],
        "failures": result.failures,
        "detail": result.detail,
    }
    assert result.passed, '规格执行失败: ' + json.dumps(failure_context, ensure_ascii=False)