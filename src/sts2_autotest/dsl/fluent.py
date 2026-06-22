"""Fluent API for STS2-AUTOTEST game-semantic test authoring (FR13).

The FluentBuilder provides a chainable DSL for defining test cases.
Terminal method .assert_that() is synchronous (uses user-provided loop).
"""

from __future__ import annotations

__test__ = False

import asyncio
from dataclasses import dataclass
import inspect
import re
from typing import Callable

from sts2_autotest.common.state import GameScreen
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
        for handler in handlers:
            self._validate_handler(handler)
        self._error_handlers.extend(handlers)
        return self

    def assert_that(self, *assertions: AssertionFn) -> TestResult:
        """Execute all accumulated actions and run assertions. Terminal.

        Synchronous: uses the loop provided at construction time or creates a
        temporary loop when no usable event loop exists.
        """
        all_actions = self._setup_actions + self._execute_actions
        if not all_actions:
            return TestResult(case_id=self._case_id, status="pass")

        loop, owns_loop = self._resolve_loop()
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
        finally:
            if owns_loop:
                loop.close()

        loop, owns_loop = self._resolve_loop()
        try:
            final_state = loop.run_until_complete(
                self._orchestrator.adapter.get_state()
            )
        finally:
            if owns_loop:
                loop.close()

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
        available = loop.run_until_complete(
            self._orchestrator.adapter.get_available_actions()
        )
        requirements = _parse_start_state_requirements(self._start_state_text)
        failures: list[str] = []

        if (
            requirements.allowed_screens
            and state.screen not in requirements.allowed_screens
            and not _is_recoverable_reward_start(
                self._start_state_text,
                state.screen,
            )
            and not _is_already_finished_first_battle_allowed_start(
                self._start_state_text,
                requirements.allowed_screens,
                state.screen,
            )
            and not _is_pending_event_before_first_battle_start(
                self._start_state_text,
                requirements.allowed_screens,
                None,
                state.screen,
            )
        ):
            allowed = ", ".join(screen.value for screen in requirements.allowed_screens)
            failures.append(
                "start state is not satisfied: "
                f"current screen={state.screen.value}, "
                f"allowed screens=[{allowed}], "
                f"raw Start State: {self._start_state_text!r}"
            )
        elif (
            requirements.screen is not None
            and state.screen != requirements.screen
            and not _is_already_resolved_neow_start(
                self._start_state_text,
                requirements.screen,
                state.screen,
            )
            and not _is_already_finished_first_battle_start(
                self._start_state_text,
                requirements.screen,
                state.screen,
            )
            and not _is_pending_event_before_first_battle_start(
                self._start_state_text,
                (),
                requirements.screen,
                state.screen,
            )
        ):
            failures.append(
                "start state is not satisfied: "
                f"current screen={state.screen.value}, "
                f"required screen={requirements.screen.value}, "
                f"raw Start State: {self._start_state_text!r}"
            )

        if (
            requirements.needs_travelable_node
            and state.screen not in {GameScreen.COMBAT, GameScreen.CARD_REWARD}
            and not _is_pending_event_before_first_battle_start(
                self._start_state_text,
                requirements.allowed_screens,
                requirements.screen,
                state.screen,
            )
            and "choose_map_node" not in available
        ):
            failures.append(
                "start state is not satisfied: "
                "spec requires a reachable map node, "
                f"but available_actions={available}"
            )

        return failures

    def _validate_handler(self, handler: HandlerFn) -> None:
        if not callable(handler):
            raise TypeError("on_error handler must be callable")

        signature = inspect.signature(handler)
        params = tuple(signature.parameters.values())
        positional = [
            param for param in params
            if param.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        required = [
            param for param in positional
            if param.default is inspect.Parameter.empty
        ]
        has_varargs = any(
            param.kind is inspect.Parameter.VAR_POSITIONAL for param in params
        )

        if len(required) > 2 or (not has_varargs and len(positional) < 2):
            raise TypeError(
                "on_error handler must accept orchestrator and case_id"
            )

    def _resolve_loop(self) -> tuple[asyncio.AbstractEventLoop, bool]:
        if self._loop is not None:
            return self._loop, False

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.new_event_loop(), True

        if loop.is_closed():
            return asyncio.new_event_loop(), True

        return loop, False


_SCREEN_PATTERNS: list[tuple[re.Pattern[str], GameScreen]] = [
    (re.compile(r"MAIN_MENU|\u4e3b\u83dc\u5355"), GameScreen.MAIN_MENU),
    (
        re.compile(r"CHARACTER_SELECT|\u89d2\u8272\u9009\u62e9"),
        GameScreen.CHARACTER_SELECT,
    ),
    (re.compile(r"\bMAP\b|\u5730\u56fe"), GameScreen.MAP),
    (re.compile(r"\bCOMBAT\b|\u6218\u6597"), GameScreen.COMBAT),
    (re.compile(r"\bEVENT\b|\u4e8b\u4ef6"), GameScreen.EVENT),
    (
        re.compile(r"CARD_REWARD|\u5361\u724c\u5956\u52b1|\u5956\u52b1\u754c\u9762"),
        GameScreen.CARD_REWARD,
    ),
    (
        re.compile(r"RELIC_REWARD|\u9057\u7269\u5956\u52b1"),
        GameScreen.RELIC_REWARD,
    ),
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
        "\u8282\u70b9" in text
        and (
            "\u53ef\u8fbe" in text
            or "\u5230\u8fbe" in text
            or "travelable" in text.lower()
        )
    )
    return StartStateRequirements(
        screen=screen,
        allowed_screens=allowed_screens,
        needs_travelable_node=needs_travelable_node,
    )


def _is_already_resolved_neow_start(
    text: str,
    required_screen: GameScreen,
    current_screen: GameScreen,
) -> bool:
    return (
        required_screen == GameScreen.EVENT
        and current_screen in {GameScreen.MAP, GameScreen.COMBAT, GameScreen.CARD_REWARD}
        and "\u5f00\u5c40\u4e8b\u4ef6" in text
        and "\u5df2\u8fdb\u5165\u65b0 run" in text
    )


def _is_already_finished_first_battle_start(
    text: str,
    required_screen: GameScreen,
    current_screen: GameScreen,
) -> bool:
    return (
        required_screen == GameScreen.MAP
        and current_screen == GameScreen.CARD_REWARD
        and "\u5730\u56fe\u754c\u9762" in text
        and "\u666e\u901a\u6218\u6597\u8282\u70b9" in text
    )


def _is_recoverable_reward_start(text: str, current_screen: GameScreen) -> bool:
    return (
        current_screen == GameScreen.CARD_REWARD
        and "\u4efb\u610f\u53ef\u6062\u590d\u72b6\u6001" in text
    )


def _is_already_finished_first_battle_allowed_start(
    text: str,
    allowed_screens: tuple[GameScreen, ...],
    current_screen: GameScreen,
) -> bool:
    return (
        current_screen == GameScreen.CARD_REWARD
        and GameScreen.MAP in allowed_screens
        and GameScreen.COMBAT in allowed_screens
        and "\u666e\u901a\u6218\u6597\u8282\u70b9" in text
    )


def _is_pending_event_before_first_battle_start(
    text: str,
    allowed_screens: tuple[GameScreen, ...],
    required_screen: GameScreen | None,
    current_screen: GameScreen,
) -> bool:
    expects_first_battle_map = (
        "\u5730\u56fe\u754c\u9762" in text
        and "\u666e\u901a\u6218\u6597\u8282\u70b9" in text
        and (
            required_screen == GameScreen.MAP
            or (
                GameScreen.MAP in allowed_screens
                and GameScreen.COMBAT in allowed_screens
            )
        )
    )
    return current_screen == GameScreen.EVENT and expects_first_battle_map


def define(
    case_id: str,
    orchestrator: TestOrchestrator,
    loop: asyncio.AbstractEventLoop | None = None,
) -> FluentBuilder:
    """Create a new FluentBuilder. Main entry point for the Fluent API."""
    return FluentBuilder(case_id=case_id, orchestrator=orchestrator, loop=loop)


# Alias matching AC/PRD example: test("card damage").setup(...)
# Named `define` as primary to avoid shadowing pytest's `test` fixture.
test = define
