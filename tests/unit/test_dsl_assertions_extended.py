"""Tests for newly added DSL assertions needed by code generator."""
from __future__ import annotations

from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.dsl.assertions import (
    has_travelable_node,
    no_crash_detected,
    player_block_increased_by,
)


class TestNoCrashDetected:
    def test_no_crash(self) -> None:
        state = GameState(screen=GameScreen.MAP)
        ok, msg = no_crash_detected()(state)
        assert ok
        assert msg == ""

    def test_crashed_state(self) -> None:
        state = GameState(screen=GameScreen.CRASHED)
        ok, msg = no_crash_detected()(state)
        assert not ok
        assert "CRASHED" in msg


class TestHasTravelableNode:
    def test_has_travelable_nodes(self) -> None:
        state = GameState(screen=GameScreen.MAP, travelable_nodes=[1, 2, 3])
        ok, _ = has_travelable_node()(state)
        assert ok

    def test_no_travelable_nodes(self) -> None:
        state = GameState(screen=GameScreen.MAP, travelable_nodes=[])
        ok, _ = has_travelable_node()(state)
        assert not ok

    def test_travelable_nodes_not_in_state(self) -> None:
        state = GameState(screen=GameScreen.MAP)
        ok, msg = has_travelable_node()(state)
        assert not ok
        assert "travelable_nodes" in msg


class TestPlayerBlockIncreasedBy:
    def test_player_block_increased_by_detects_gain(self) -> None:
        state = GameState(screen=GameScreen.COMBAT, block=8, previous_block=3)
        ok, msg = player_block_increased_by(5)(state)
        assert ok, msg

    def test_player_block_increased_by_fails_without_previous(self) -> None:
        state = GameState(screen=GameScreen.COMBAT, block=8)
        ok, _ = player_block_increased_by(5)(state)
        assert not ok
