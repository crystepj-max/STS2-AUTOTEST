"""Tests for newly added DSL assertions needed by code generator."""
from __future__ import annotations

import pytest
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.dsl.assertions import (
    no_crash_detected, has_travelable_node,
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
        ok, msg = has_travelable_node()(state)
        assert ok

    def test_no_travelable_nodes(self) -> None:
        state = GameState(screen=GameScreen.MAP, travelable_nodes=[])
        ok, msg = has_travelable_node()(state)
        assert not ok

    def test_travelable_nodes_not_in_state(self) -> None:
        state = GameState(screen=GameScreen.MAP)
        ok, msg = has_travelable_node()(state)
        assert not ok
        assert "travelable_nodes" in msg
