"""Fluent API — game-semantic test authoring for STS2-AUTOTEST."""

from sts2_autotest.dsl.assertions import (
    end_turn,
    enemy_hp_decreased_by,
    enter_combat,
    game_reached_state,
    give_card,
    play_card,
    player_energy_decreased_by,
    player_hp_changed_by,
    set_hp,
    set_seed,
    start_game,
)
from sts2_autotest.dsl.fixtures import FixtureLoader
from sts2_autotest.dsl.fluent import FluentBuilder, HandlerFn, define, test
from sts2_autotest.dsl.handlers import capture_screenshot, log_state

__all__ = [
    "FluentBuilder",
    "FixtureLoader",
    "HandlerFn",
    "define",
    "test",
    # assertion functions
    "game_reached_state",
    "enemy_hp_decreased_by",
    "player_energy_decreased_by",
    "player_hp_changed_by",
    # setup/execute functions
    "start_game",
    "enter_combat",
    "play_card",
    "end_turn",
    "set_seed",
    "give_card",
    "set_hp",
    # error handlers
    "capture_screenshot",
    "log_state",
]
