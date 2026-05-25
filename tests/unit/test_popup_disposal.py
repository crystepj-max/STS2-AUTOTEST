"""Tests for core/popup_disposal.py popup classification and disposition."""

from sts2_autotest.core.popup_disposal import (
    PopupDisposition,
    PopupKind,
    classify_popup,
    decide_popup_disposition,
)


class TestClassifyPopup:
    def test_steam_eula(self) -> None:
        assert classify_popup("Steam", "End User License Agreement") == PopupKind.STEAM_EULA
        assert classify_popup("steam client", "Please accept the EULA") == PopupKind.STEAM_EULA

    def test_steam_update(self) -> None:
        assert classify_popup("Steam", "Update required") == PopupKind.STEAM_UPDATE
        assert classify_popup("Steam", "Update before launch") == PopupKind.STEAM_UPDATE

    def test_steam_ad(self) -> None:
        assert classify_popup("Steam", "Special Offer") == PopupKind.STEAM_AD
        assert classify_popup("Steam Sale", "Advertisement") == PopupKind.STEAM_AD

    def test_game_crash(self) -> None:
        assert classify_popup("Slay the Spire 2", "The game stopped") == PopupKind.GAME_CRASH
        assert classify_popup("Error", "Application has crashed") == PopupKind.GAME_CRASH

    def test_none_for_empty_title_and_text(self) -> None:
        assert classify_popup("", "") == PopupKind.NONE
        assert classify_popup("  ", "\t") == PopupKind.NONE

    def test_unknown_for_unrecognized_popup(self) -> None:
        assert classify_popup("Launcher", "Choose a profile") == PopupKind.UNKNOWN


class TestDecidePopupDisposition:
    def test_crash_popup_is_preserved(self) -> None:
        assert decide_popup_disposition(PopupKind.GAME_CRASH) == PopupDisposition.PRESERVE

    def test_known_non_crash_popups_are_closed(self) -> None:
        assert decide_popup_disposition(PopupKind.STEAM_EULA) == PopupDisposition.CLOSE
        assert decide_popup_disposition(PopupKind.STEAM_UPDATE) == PopupDisposition.CLOSE
        assert decide_popup_disposition(PopupKind.STEAM_AD) == PopupDisposition.CLOSE

    def test_none_is_ignored(self) -> None:
        assert decide_popup_disposition(PopupKind.NONE) == PopupDisposition.IGNORE

    def test_unknown_needs_manual_intervention(self) -> None:
        assert decide_popup_disposition(PopupKind.UNKNOWN) == PopupDisposition.MANUAL_INTERVENTION
