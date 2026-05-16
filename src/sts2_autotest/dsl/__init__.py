"""DSL package — fluent API for authoring test cases."""
from sts2_autotest.dsl.fluent import FluentBuilder, HandlerFn, define
from sts2_autotest.dsl.assertions import (
    end_turn,
    enemy_hp_decreased_by,
    enter_combat,
    game_reached_state,
    give_card,
    has_travelable_node,
    no_crash_detected,
    play_card,
    player_energy_decreased_by,
    player_hp_changed_by,
    set_hp,
    set_seed,
    start_game,
)
from sts2_autotest.dsl.fixtures import FixtureLoader
from sts2_autotest.dsl.handlers import capture_screenshot, log_state

__all__ = [
    "FluentBuilder",
    "FixtureLoader",
    "HandlerFn",
    "capture_screenshot",
    "define",
    "end_turn",
    "enemy_hp_decreased_by",
    "enter_combat",
    "game_reached_state",
    "give_card",
    "has_travelable_node",
    "log_state",
    "no_crash_detected",
    "play_card",
    "player_energy_decreased_by",
    "player_hp_changed_by",
    "set_hp",
    "set_seed",
    "start_game",
]
