"""Fluent API for STS2-AUTOTEST — game-semantic test authoring (FR13).

The FluentBuilder provides a chainable DSL for defining test cases.
Terminal method .assert_that() is synchronous (uses user-provided loop).
"""

from __future__ import annotations

__test__ = False

import asyncio
from dataclasses import dataclass
import re
from typing import Any, Callable

from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.dsl.assertions import AssertionFn

HandlerFn = Callable[[TestOrchestrator, str], None]


@dataclass(frozen=True)
class StartStateRequirements:
    screen: GameScreen | None = None
    allowed_screens: tuple[GameScreen, ...] = ()
    needs_travelable_node: bool = False


class FluentBuilder:
    """Chainable test case builder.

        define("card-damage", orch, loop)
            .setup(start_game(), enter_combat("JawWorm"))
            .execute(play_card("VoidSlash"))
            .on_error(log_state)
            .assert_that(enemy_hp_decreased_by(15))
    """

    def __init__(
        self,
        case_id: str,
        orchestrator: TestOrchestrator,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._case_id = case_id
        self._orchestrator = orchestrator
        self._loop = loop
        self._setup_actions: list[ActionDescriptor] = []
        self._execute_actions: list[ActionDescriptor] = []
        self._error_handlers: list[HandlerFn] = []
        self._start_state_text: str = ""

    def require_start_state(self, start_state: str) -> "FluentBuilder":
        self._start_state_text = start_state.strip()
        return self

    def setup(self, *actions: ActionDescriptor) -> "FluentBuilder":
        self._setup_actions.extend(actions)
        return self

    def execute(self, *actions: ActionDescriptor) -> "FluentBuilder":
        self._execute_actions.extend(actions)
        return self

    def on_error(self, *handlers: HandlerFn) -> "FluentBuilder":
        """Register error callback(s). Called if assert_that fails."""
        self._error_handlers.extend(handlers)
        return self

    def assert_that(self, *assertions: AssertionFn) -> TestResult:
        """Execute all accumulated actions and run assertions. Terminal.

        Synchronous — uses the loop provided at construction time.
        Returns TestResult with failures and state_snapshot populated.
        """
        loop = self._loop or asyncio.get_event_loop()

        all_actions = self._setup_actions + self._execute_actions
        if not all_actions:
            return TestResult(case_id=self._case_id, status="pass")

        try:
            start_failures = self._check_start_state(loop)
            if start_failures:
                self._run_handlers()
                return TestResult(
                    case_id=self._case_id,
                    status="fail",
                    failures=start_failures,
                )
            loop.run_until_complete(
                self._orchestrator.execute_action_sequence(all_actions)
            )
        except (SystemExit, KeyboardInterrupt, MemoryError):
            raise  # never swallow critical exceptions
        except Exception as exc:
            self._run_handlers()
            return TestResult(
                case_id=self._case_id, status="fail", failures=[str(exc)]
            )

        # Read final state
        final_state = loop.run_until_complete(
            self._orchestrator.adapter.get_state()
        )

        # Run assertions
        failures: list[str] = []
        for assertion in assertions:
            ok, msg = assertion(final_state)
            if not ok:
                failures.append(msg)

        if failures:
            self._run_handlers()
            return TestResult(
                case_id=self._case_id,
                status="fail",
                failures=failures,
                state_snapshot=final_state,
            )

        return TestResult(
            case_id=self._case_id, status="pass", state_snapshot=final_state
        )

    def _run_handlers(self) -> None:
        for handler in self._error_handlers:
            try:
                handler(self._orchestrator, self._case_id)
            except Exception:
                pass

    def _check_start_state(self, loop: asyncio.AbstractEventLoop) -> list[str]:
        if not self._start_state_text:
            return []

        state = loop.run_until_complete(self._orchestrator.adapter.get_state())
        available = loop.run_until_complete(self._orchestrator.adapter.get_available_actions())
        requirements = _parse_start_state_requirements(self._start_state_text)
        failures: list[str] = []

        if requirements.allowed_screens and state.screen not in requirements.allowed_screens:
            allowed = ", ".join(screen.value for screen in requirements.allowed_screens)
            failures.append(
                "起始状态不满足："
                f"当前 screen={state.screen.value}，"
                f"规格允许 screens=[{allowed}]。"
                f"原始 Start State: {self._start_state_text!r}"
            )
        elif requirements.screen is not None and state.screen != requirements.screen:
            failures.append(
                "起始状态不满足："
                f"当前 screen={state.screen.value}，"
                f"规格要求 screen={requirements.screen.value}。"
                f"原始 Start State: {self._start_state_text!r}"
            )

        if requirements.needs_travelable_node and "choose_map_node" not in available:
            failures.append(
                "起始状态不满足：规格要求存在可到达地图节点，"
                f"但当前 available_actions={available}"
            )

        return failures


_SCREEN_PATTERNS: list[tuple[re.Pattern[str], GameScreen]] = [
    (re.compile(r"MAIN_MENU|主菜单"), GameScreen.MAIN_MENU),
    (re.compile(r"CHARACTER_SELECT|角色选择"), GameScreen.CHARACTER_SELECT),
    (re.compile(r"\bMAP\b|地图"), GameScreen.MAP),
    (re.compile(r"\bCOMBAT\b|战斗"), GameScreen.COMBAT),
    (re.compile(r"\bEVENT\b|事件"), GameScreen.EVENT),
    (re.compile(r"CARD_REWARD|卡牌奖励|奖励界面"), GameScreen.CARD_REWARD),
    (re.compile(r"RELIC_REWARD|遗物奖励"), GameScreen.RELIC_REWARD),
    (re.compile(r"GAME_OVER"), GameScreen.GAME_OVER),
    (re.compile(r"VICTORY"), GameScreen.VICTORY),
    (re.compile(r"UNKNOWN"), GameScreen.UNKNOWN),
]


def _parse_start_state_requirements(text: str) -> StartStateRequirements:
    matched_screens = tuple(
        candidate for pattern, candidate in _SCREEN_PATTERNS if pattern.search(text)
    )
    uses_screen_list = "/" in text or len(matched_screens) > 1
    screen = None if uses_screen_list else next(iter(matched_screens), None)
    allowed_screens = matched_screens if uses_screen_list else ()

    needs_travelable_node = (
        "节点" in text and ("可达" in text or "到达" in text or "travelable" in text.lower())
    )
    return StartStateRequirements(
        screen=screen,
        allowed_screens=allowed_screens,
        needs_travelable_node=needs_travelable_node,
    )


def define(
    case_id: str,
    orchestrator: TestOrchestrator,
    loop: asyncio.AbstractEventLoop | None = None,
) -> FluentBuilder:
    """Create a new FluentBuilder. Main entry point for the Fluent API."""
    return FluentBuilder(case_id=case_id, orchestrator=orchestrator, loop=loop)


# Alias matching AC/PRD example: test("卡牌伤害").setup(...)
# Named `define` as primary to avoid shadowing pytest's `test` fixture.
test = define
