"""Tests for core/main_menu_state.py — 干净主菜单判定核心（纯函数契约）。

判定口径在 LOC-005（决策 B）中统一：run_executor（受控重启）、
journeys.reset_to_main_menu（旅程内导航）、orchestrator._auto_reset_to_main_menu
（用例失败恢复）共享同一份三态内省判定。
"""

from __future__ import annotations

from sts2_autotest.core.main_menu_state import (
    frame_clean,
    frame_dirty,
    menu_has_run_save_field,
    state_view,
)


class TestMenuHasRunSaveField:
    def test_true_false_none_three_states(self) -> None:
        assert menu_has_run_save_field({"has_run_save": True}) is True
        assert menu_has_run_save_field({"has_run_save": False}) is False
        assert menu_has_run_save_field({"has_run_save": "yes"}) is None
        assert menu_has_run_save_field({}) is None

    def test_nested_menu_block(self) -> None:
        assert menu_has_run_save_field({"menu": {"has_run_save": True}}) is True

    def test_non_bool_is_undecidable(self) -> None:
        assert menu_has_run_save_field({"menu": {"has_run_save": 1}}) is None


class TestFrameDirty:
    def test_introspection_field_wins(self) -> None:
        # 内省字段优先：False 压过动作列表里的陈旧 continue_run（V11 伪影）。
        assert frame_dirty({"has_run_save": False}, ["continue_run"]) is False
        assert frame_dirty({"has_run_save": True}, []) is True

    def test_falls_back_to_actions(self) -> None:
        assert frame_dirty({}, ["continue_run"]) is True
        assert frame_dirty({}, ["start_new_run"]) is False


class TestFrameClean:
    def test_clean_with_explicit_no_save(self) -> None:
        view = {"screen": "MAIN_MENU", "has_run_save": False}
        assert frame_clean(view, ["start_new_run", "continue_run"]) is True

    def test_dirty_with_save(self) -> None:
        view = {"screen": "MAIN_MENU", "has_run_save": True}
        assert frame_clean(view, ["start_new_run"]) is False

    def test_undecidable_requires_no_stale_continue(self) -> None:
        view = {"screen": "MAIN_MENU"}
        assert frame_clean(view, ["start_new_run"]) is True
        assert frame_clean(view, ["start_new_run", "continue_run"]) is False

    def test_requires_new_run_capability(self) -> None:
        view = {"screen": "MAIN_MENU", "has_run_save": False}
        assert frame_clean(view, ["probe"]) is False

    def test_non_main_menu_never_clean(self) -> None:
        assert frame_clean({"screen": "MAP", "has_run_save": False}, ["start_new_run"]) is False


class TestStateView:
    def test_passthrough_dict(self) -> None:
        assert state_view({"screen": "MAP"}) == {"screen": "MAP"}

    def test_model_dump_object(self) -> None:
        class _State:
            def model_dump(self) -> dict:
                return {"screen": "MAIN_MENU", "has_run_save": False}

        assert state_view(_State()) == {"screen": "MAIN_MENU", "has_run_save": False}

    def test_attr_fallback(self) -> None:
        class _State:
            screen = "MAIN_MENU"

        assert state_view(_State()) == {"screen": "MAIN_MENU"}
