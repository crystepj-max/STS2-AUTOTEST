"""Tests for adapters/semantics.py — 适配器语义层的共享策略契约。"""

from __future__ import annotations

import pytest

from sts2_autotest.adapters.semantics import (
    CARD_REWARD_SKIP_ACTIONS,
    EVENT_CHOICE_ACTIONS,
    RETURN_TO_MENU_ACTIONS,
    SCREEN_NAME_TO_GAME_SCREEN,
    check_version_compatibility,
    classify_action_error,
    filter_state_extra,
    map_screen_name,
)
from sts2_autotest.common.errors import AdapterErrorSubType, ErrorCategory, STS2Error
from sts2_autotest.common.state import GameScreen


class TestScreenMapping:
    """屏幕名归一：超集必须同时覆盖两个传输方言的历史键。"""

    def test_superset_covers_cli_and_agent_specific_names(self) -> None:
        assert SCREEN_NAME_TO_GAME_SCREEN["SINGLEPLAYER_SUBMENU"] == GameScreen.MAIN_MENU
        assert SCREEN_NAME_TO_GAME_SCREEN["TRI_SELECT"] == GameScreen.TRI_SELECT
        assert SCREEN_NAME_TO_GAME_SCREEN["MODAL"] == GameScreen.MAIN_MENU
        assert SCREEN_NAME_TO_GAME_SCREEN["CRASHED"] == GameScreen.CRASHED

    def test_unknown_falls_back_to_unknown(self) -> None:
        assert map_screen_name("SOME_FUTURE_SCREEN") == GameScreen.UNKNOWN
        assert map_screen_name("MAP") == GameScreen.MAP

    def test_no_conflicting_values_for_shared_keys(self) -> None:
        """同名字段在两个方言里必须归到同一屏幕（历史漂移的回归防线）。"""
        shared = {
            "MENU", "CHARACTER_SELECT", "MAP", "COMBAT", "SHOP", "REST",
            "REST_SITE", "EVENT", "TREASURE", "CHEST", "BUNDLE_SELECTION",
            "CONFIRM_BUNDLE", "BOSS_REWARD", "REWARD", "CARD_REWARD",
            "CARD_SELECTION", "RELIC_REWARD", "GAME_OVER", "VICTORY",
        }
        assert shared <= set(SCREEN_NAME_TO_GAME_SCREEN)


class TestClassifyActionError:
    def _timeout_error(self) -> STS2Error:
        return STS2Error(
            category=ErrorCategory.TIMEOUT_ERROR,
            message="timed out",
            detail={"subtype": AdapterErrorSubType.TIMEOUT},
        )

    def _plain_error(self) -> STS2Error:
        return STS2Error(category=ErrorCategory.GAME_ERROR, message="boom", detail={})

    def test_timeout_maps_to_timeout_status(self) -> None:
        result = classify_action_error(self._timeout_error(), state_changed=True)
        assert result.status == "timeout"
        assert result.state_changed is True

    def test_other_errors_map_to_failure(self) -> None:
        result = classify_action_error(self._plain_error())
        assert result.status == "failure"
        assert result.state_changed is False

    def test_treat_as_success_predicate_downgrades(self) -> None:
        def _play_phase_removed(exc: STS2Error) -> bool:
            return "get_isplayphase" in str(exc.message).lower()

        result = classify_action_error(
            STS2Error(
                category=ErrorCategory.GAME_ERROR,
                message="Method not found: get_IsPlayPhase()",
                detail={},
            ),
            treat_as_success=_play_phase_removed,
        )
        assert result.status == "success"
        assert result.state_changed is True


class TestVersionCompatibility:
    def test_matching_major_passes(self) -> None:
        check_version_compatibility(
            "0.7.2", supported_major=0, command_hint="x", upgrade_target="X"
        )

    def test_unparseable_version_raises(self) -> None:
        with pytest.raises(STS2Error) as exc_info:
            check_version_compatibility(
                "nonsense", supported_major=0, command_hint="sts2 --version",
                upgrade_target="STS2-Cli-Mod",
            )
        assert exc_info.value.detail.get("subtype") == AdapterErrorSubType.JSON_PARSE_FAILURE

    def test_major_mismatch_raises(self) -> None:
        with pytest.raises(STS2Error) as exc_info:
            check_version_compatibility(
                "1.0.0", supported_major=0, command_hint="x", upgrade_target="X"
            )
        assert exc_info.value.detail.get("subtype") == AdapterErrorSubType.VERSION_MISMATCH


class TestFilterStateExtra:
    def test_skips_screen_and_error_keys(self) -> None:
        data = {"screen": "MAP", "error": None, "floor": 2}
        assert filter_state_extra(data) == {"floor": 2}


class TestActionVocabulary:
    """方言词表常量：内容即导航层历史行为，改动必须显式。"""

    def test_event_choice_covers_both_dialects(self) -> None:
        assert EVENT_CHOICE_ACTIONS == ("choose_event", "choose_event_option")

    def test_return_to_menu_covers_both_dialects(self) -> None:
        assert RETURN_TO_MENU_ACTIONS == ("return_to_menu", "return_to_main_menu")

    def test_card_reward_skip_order(self) -> None:
        assert CARD_REWARD_SKIP_ACTIONS == ("skip_reward_cards", "reward_skip_card")


class TestConvergenceVocabulary:
    """LOC-004：推进收敛判定词表——四份推进实现的共享判定条件。"""

    def test_converged_screens(self) -> None:
        from sts2_autotest.adapters.semantics import CONVERGED_SCREENS

        assert CONVERGED_SCREENS == frozenset({GameScreen.MAP, GameScreen.COMBAT})

    def test_interstitial_screens(self) -> None:
        from sts2_autotest.adapters.semantics import INTERSTITIAL_SCREENS

        assert INTERSTITIAL_SCREENS == frozenset({
            GameScreen.EVENT,
            GameScreen.CARD_REWARD,
            GameScreen.TRI_SELECT,
            GameScreen.BUNDLE_SELECTION,
        })

    def test_vocabularies_disjoint(self) -> None:
        """收敛屏与中间屏不相交：同一屏幕不能同时是目标与中间态。"""
        from sts2_autotest.adapters.semantics import (
            CONVERGED_SCREENS,
            INTERSTITIAL_SCREENS,
        )

        assert not (CONVERGED_SCREENS & INTERSTITIAL_SCREENS)
