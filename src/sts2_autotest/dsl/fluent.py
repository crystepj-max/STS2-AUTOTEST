"""Fluent API for STS2-AUTOTEST — game-semantic test authoring (FR13).

The FluentBuilder provides a chainable DSL for defining test cases.
Terminal method .assert_that() is synchronous (uses user-provided loop).
"""

from __future__ import annotations

__test__ = False

import asyncio
import inspect
from typing import Any, Callable

from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.dsl.assertions import AssertionFn

HandlerFn = Callable[[TestOrchestrator, str], None]


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

        Synchronous — uses the loop provided at construction time.
        Returns TestResult with failures and state_snapshot populated.
        """
        all_actions = self._setup_actions + self._execute_actions
        if not all_actions:
            return TestResult(case_id=self._case_id, status="pass")

        loop, owns_loop = self._resolve_loop()
        try:
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
