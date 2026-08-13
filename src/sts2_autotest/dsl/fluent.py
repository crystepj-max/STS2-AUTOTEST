"""Fluent API for STS2-AUTOTEST game-semantic test authoring (FR13).

The FluentBuilder provides a chainable DSL for defining test cases.
Terminal method .assert_that() is synchronous (uses user-provided loop).
"""

from __future__ import annotations

__test__ = False

import asyncio
import inspect
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.common.state import GameScreen, GameState
from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator
from sts2_autotest.dsl.assertions import (
    AssertionFn,
    _resolve_enemy_field,
    _resolve_player_field,
)

HandlerFn = Callable[[TestOrchestrator, str], None]

# (previous_* attr name, resource field name, read from first enemy instead of player)
_TRACKED_PREVIOUS_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("previous_hp", "hp", False),
    ("previous_energy", "energy", False),
    ("previous_block", "block", False),
    ("previous_hand_count", "hand_count", False),
    ("previous_enemy_hp", "hp", True),
)

_STABLE_TRANSITION_ACTIONS = frozenset({
    "embark",
    "choose_event",
    "choose_event_option",
    "choose_map_node",
    "choose_map_node_by_type",
    "collect_rewards_and_proceed",
    "skip_card_reward",
    "end_turn",
})


def _merge_previous_snapshot(before: GameState, after: GameState) -> GameState:
    """Attach previous_* resource fields (read from `before`) onto `after`.

    Lets resource-delta assertions (player_hp_changed_by, player_block_increased_by,
    enemy_hp_decreased_by, ...) compare against the state captured right before the
    tested action ran, instead of needing the adapter to track history itself.
    """
    updates: dict[str, Any] = {}
    for previous_name, field_name, is_enemy in _TRACKED_PREVIOUS_FIELDS:
        value = (
            _resolve_enemy_field(before, field_name)
            if is_enemy
            else _resolve_player_field(before, field_name)
        )
        if value is not None:
            updates[previous_name] = value
    if not updates:
        return after
    return after.model_copy(update=updates)


@dataclass(frozen=True)
class StartStateRequirements:
    screen: GameScreen | None = None
    allowed_screens: tuple[GameScreen, ...] = ()
    needs_travelable_node: bool = False


@dataclass(frozen=True)
class _TraceEntry:
    step_index: int
    action: ActionDescriptor
    pre_state: GameState
    post_state: GameState
    result: ActionResult


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
        settle_timeout: float = 5.0,
        settle_poll_interval: float = 0.5,
    ) -> None:
        self._case_id = case_id
        self._orchestrator = orchestrator
        self._loop = loop
        self._setup_actions: list[ActionDescriptor] = []
        self._execute_actions: list[ActionDescriptor] = []
        self._error_handlers: list[HandlerFn] = []
        self._start_state_text: str = ""
        self._settle_timeout = settle_timeout
        self._settle_poll_interval = settle_poll_interval

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
            case_log_path = _build_case_trace_path(self._case_id)
            self._write_trace_files(case_log_path, [])
            return TestResult(
                case_id=self._case_id,
                status="pass",
                detail=_build_detail_message(case_log_path, 0),
            )

        before_state: GameState | None = None
        trace_entries: list[_TraceEntry] = []
        case_log_path = _build_case_trace_path(self._case_id)

        def _capture_before(state: GameState) -> None:
            nonlocal before_state
            before_state = state

        def _record_trace(
            action: ActionDescriptor,
            pre_state: GameState,
            post_state: GameState,
            result: ActionResult,
        ) -> None:
            trace_entries.append(
                _TraceEntry(
                    step_index=len(trace_entries) + 1,
                    action=action,
                    pre_state=pre_state,
                    post_state=post_state,
                    result=result,
                )
            )

        loop, owns_loop = self._resolve_loop()
        previous_hook = self._orchestrator._action_trace_hook
        try:
            start_failures = self._check_start_state(loop)
            if start_failures:
                self._write_trace_files(case_log_path, trace_entries, failure_text="\n".join(start_failures))
                self._run_handlers()
                return TestResult(
                    case_id=self._case_id,
                    status="fail",
                    failures=start_failures,
                    detail=_build_detail_message(case_log_path, len(trace_entries)),
                )
            # Setup and execute run as two phases (not one combined sequence) so the
            # first execute action's natural pre-state read can double as the
            # "before" snapshot for resource-delta assertions — no extra get_state call.
            self._orchestrator.set_action_trace_hook(_record_trace)
            loop.run_until_complete(
                self._orchestrator.execute_action_sequence(self._setup_actions)
            )
            loop.run_until_complete(
                self._orchestrator.execute_action_sequence(
                    self._execute_actions, on_first_pre_state=_capture_before
                )
            )
        except (SystemExit, KeyboardInterrupt, MemoryError):
            raise  # never swallow critical exceptions
        except Exception as exc:
            self._write_trace_files(case_log_path, trace_entries, failure_text=str(exc))
            self._run_handlers()
            return TestResult(
                case_id=self._case_id,
                status="fail",
                failures=[str(exc)],
                detail=_build_detail_message(case_log_path, len(trace_entries)),
            )
        finally:
            self._orchestrator.set_action_trace_hook(previous_hook)
            if owns_loop:
                loop.close()

        loop, owns_loop = self._resolve_loop()
        try:
            final_action = self._execute_actions[-1].action_type if self._execute_actions else None
            final_state = loop.run_until_complete(self._settle_and_get_state(final_action, before_state))
        finally:
            if owns_loop:
                loop.close()

        if before_state is not None:
            final_state = _merge_previous_snapshot(before_state, final_state)

        failures: list[str] = []
        for assertion in assertions:
            ok, msg = assertion(final_state)
            if not ok:
                failures.append(msg)

        if failures:
            self._write_trace_files(case_log_path, trace_entries, failure_text="\n".join(failures))
            self._run_handlers()
            return TestResult(
                case_id=self._case_id,
                status="fail",
                detail=_build_detail_message(case_log_path, len(trace_entries)),
                failures=failures,
                state_snapshot=final_state,
            )

        self._write_trace_files(case_log_path, trace_entries)
        return TestResult(
            case_id=self._case_id,
            status="pass",
            detail=_build_detail_message(case_log_path, len(trace_entries)),
            state_snapshot=final_state,
        )

    def _write_trace_files(
        self,
        case_log_path: Path,
        trace_entries: list[_TraceEntry],
        failure_text: str | None = None,
    ) -> None:
        case_log_path.parent.mkdir(parents=True, exist_ok=True)
        sections: list[str] = []
        for entry in trace_entries:
            section = _render_trace_entry(entry)
            sections.append(section)
            step_path = case_log_path.parent / f"step-{entry.step_index:02d}.log"
            step_path.write_text(section + "\n", encoding="utf-8")

        if not trace_entries:
            sections.append(
                "步骤记录：0\n"
                f"结果：{'failed' if failure_text else 'passed'}\n"
                "说明：本用例未产生可追踪的原子操作。"
            )
        if failure_text:
            sections.append(f"执行结果：失败\n原因：{failure_text}")

        case_log_path.write_text(
            ("\n\n" + ("-" * 60) + "\n\n").join(sections).strip() + "\n",
            encoding="utf-8",
        )

    async def _settle_and_get_state(
        self,
        final_action: str | None = None,
        before_state: GameState | None = None,
    ) -> GameState:
        """Get a settled post-action state snapshot for assertions.

        The raw state returned immediately after an action can be a transition frame:
        UNKNOWN during screen loads, or COMBAT snapshots mid-turn where the hand is
        briefly empty and delayed triggers have not fully resolved yet.  When the
        first post-action state looks transitional, keep polling until we observe
        two identical settled snapshots in a row, or until settle_timeout expires.
        """
        state = await self._get_state_or_unknown()
        requires_stable_repeat = (
            final_action in _STABLE_TRANSITION_ACTIONS
            and state.screen != GameScreen.UNKNOWN
        )
        if self._settle_timeout <= 0 or (
            not self._needs_settle(state, final_action, before_state) and not requires_stable_repeat
        ):
            return state

        deadline = time.monotonic() + self._settle_timeout
        last_state = state
        last_settled_signature: str | None = (
            self._state_signature(state)
            if requires_stable_repeat and not self._needs_settle(state, final_action, before_state)
            else None
        )
        requires_stable_repeat = requires_stable_repeat or state.screen != GameScreen.UNKNOWN
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self._settle_poll_interval, remaining))
            candidate = await self._get_state_or_unknown()
            last_state = candidate
            if self._needs_settle(candidate, final_action, before_state):
                last_settled_signature = None
                continue

            signature = self._state_signature(candidate)
            if not requires_stable_repeat:
                return candidate
            if signature == last_settled_signature:
                return candidate
            last_settled_signature = signature
        return last_state

    async def _get_state_or_unknown(self) -> GameState:
        """Get state, returning UNKNOWN on any error instead of crashing.

        The STS2-Agent's /state endpoint can hang for extended periods during game
        loading transitions (e.g. after embark).  We let the adapter's native HTTP
        timeout (30s) handle per-request deadlines — cancelling early with
        asyncio.wait_for would miss the moment the game finishes loading (an in-flight
        request that started before loading completed may return EVENT a few seconds
        after loading finishes).
        """
        try:
            return await self._orchestrator.adapter.get_state()
        except Exception:
            return GameState(screen=GameScreen.UNKNOWN)

    def _needs_settle(
        self,
        state: GameState,
        final_action: str | None = None,
        before_state: GameState | None = None,
    ) -> bool:
        if state.screen == GameScreen.UNKNOWN:
            return True
        if state.screen.is_terminal:
            return False

        if final_action == "end_turn" and self._is_end_turn_draw_incomplete(before_state, state):
            return True

        combat = getattr(state, "combat", None)
        hand = combat.get("hand") if isinstance(combat, dict) else None
        if state.screen == GameScreen.COMBAT and isinstance(hand, list) and len(hand) == 0:
            return True

        available = self._extract_available_actions(state)
        if available is None:
            return False
        return len(available) == 0

    def _extract_available_actions(self, state: GameState) -> list[str] | None:
        direct = getattr(state, "available_actions", None)
        if isinstance(direct, list):
            return [str(action) for action in direct]

        agent_view = getattr(state, "agent_view", None)
        if isinstance(agent_view, dict):
            nested = agent_view.get("available_actions")
            if isinstance(nested, list):
                return [str(action) for action in nested]
        return None

    def _is_end_turn_draw_incomplete(
        self,
        before_state: GameState | None,
        current_state: GameState,
    ) -> bool:
        if before_state is None:
            return False
        if before_state.screen != GameScreen.COMBAT or current_state.screen != GameScreen.COMBAT:
            return False

        before_combat = getattr(before_state, "combat", None)
        current_combat = getattr(current_state, "combat", None)
        if not isinstance(before_combat, dict) or not isinstance(current_combat, dict):
            return False

        before_hand = before_combat.get("hand")
        current_hand = current_combat.get("hand")
        if not isinstance(before_hand, list) or not isinstance(current_hand, list):
            return False

        return len(current_hand) < len(before_hand)

    def _state_signature(self, state: GameState) -> str:
        return json.dumps(
            state.model_dump(mode="python"),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
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


def _build_detail_message(case_log_path: Path, step_count: int) -> str:
    return (
        f"行为日志：{case_log_path}"
        f"；记录步骤：{step_count}"
    )


def _build_case_trace_path(case_id: str) -> Path:
    return _build_case_trace_dir(case_id) / "case.log"


def _build_case_trace_dir(case_id: str) -> Path:
    root_raw = os.environ.get("STS2_CASE_TRACE_ROOT", "").strip()
    if root_raw:
        root = Path(root_raw)
    else:
        cwd = Path.cwd()
        automation_output = cwd / "automation" / "autotest" / "output"
        root = (
            automation_output / "case-traces"
            if automation_output.is_dir()
            else Path(os.environ.get("STS2_FRAMEWORK__EVIDENCE_DIR", "tests/output")) / "case-traces"
        )
    return root / _current_suite_slug() / _slug(case_id)


def _current_suite_slug() -> str:
    current_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    node_id = current_test.split(" ", 1)[0].strip()
    if "::" in node_id:
        file_part, func_part = node_id.split("::", 1)
        return _slug(f"{Path(file_part).stem}-{func_part}")
    if node_id:
        return _slug(Path(node_id).stem)
    return "adhoc-suite"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "item"


def _render_trace_entry(entry: _TraceEntry) -> str:
    action_text = _describe_action(entry.action, entry.pre_state, entry.post_state)
    screen_change = f"{entry.pre_state.screen.value} -> {entry.post_state.screen.value}"
    lines = [
        f"步骤 {entry.step_index:02d}",
        f"动作：{action_text}",
        f"界面：{screen_change}",
        f"结果：{entry.result.status}",
    ]

    lines.extend(_build_delta_lines(entry.pre_state, entry.post_state))
    return "\n".join(lines).strip()


def _describe_action(
    action: ActionDescriptor,
    pre_state: GameState,
    post_state: GameState,
) -> str:
    params = action.params or {}
    if action.action_type == "play_card":
        card_id = str(params.get("card_id", ""))
        card = _find_hand_card(pre_state, card_id)
        card_name = _card_name(card, card_id)
        return f"打出卡牌 {card_name}（{_card_identity(card, card_id)}）"
    if action.action_type == "give_card":
        card_id = str(params.get("card_id", ""))
        card = _find_hand_card(post_state, card_id)
        return f"加入手牌 {_card_identity(card, card_id)}"
    if action.action_type == "set_seed":
        return f"设置固定种子 {params.get('seed')}"
    if action.action_type == "end_turn":
        return "结束回合"
    if action.action_type == "choose_event":
        option = _find_event_option(pre_state, params.get("index"))
        return f"选择事件选项 {params.get('index')}：{option}"
    if action.action_type == "choose_neow_blessing":
        return "选择涅奥祝福"
    if action.action_type == "choose_map_node":
        return f"选择地图节点 index={params.get('index', params)}"
    if action.action_type == "choose_map_node_by_type":
        return f"选择地图节点类型 {params.get('node_type')}"
    if action.action_type == "select_character":
        return f"选择角色 {params.get('character_id')}"
    return action.action_type


def _find_hand_card(state: GameState, card_id: str) -> dict[str, Any] | None:
    combat = getattr(state, "combat", None)
    hand = combat.get("hand") if isinstance(combat, dict) else None
    if isinstance(hand, list):
        target = card_id.split(":")[-1].upper()
        for card in hand:
            if not isinstance(card, dict):
                continue
            candidate_id = str(card.get("card_id") or card.get("id") or "")
            normalized = candidate_id.split("-")[-1].split(":")[-1].upper()
            if (
                normalized == target
                or candidate_id == card_id
                or target in normalized
                or normalized in target
            ):
                return card
    return None


def _card_name(card: dict[str, Any] | None, fallback: str) -> str:
    if card is None:
        return fallback
    return str(card.get("card_name") or card.get("name") or fallback)


def _card_identity(card: dict[str, Any] | None, requested_id: str) -> str:
    if card is None:
        return requested_id
    runtime_id = str(card.get("card_id") or card.get("id") or "")
    if runtime_id and runtime_id != requested_id:
        return f"{requested_id} → {runtime_id}"
    return requested_id


def _find_event_option(state: GameState, index: Any) -> str:
    event = getattr(state, "event", None)
    if not isinstance(event, dict):
        return "未知选项"
    options = event.get("options")
    if not isinstance(options, list):
        return "未知选项"
    for fallback_index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        option_index = option.get("index", fallback_index)
        if option_index == index:
            return str(option.get("title") or option.get("description") or option_index)
    return "未知选项"


def _build_delta_lines(before: GameState, after: GameState) -> list[str]:
    lines: list[str] = []

    player_before = _extract_player_snapshot(before)
    player_after = _extract_player_snapshot(after)
    player_line = _format_snapshot_delta("玩家", player_before, player_after, ("hp", "block", "energy", "hand"))
    if player_line:
        lines.append(player_line)

    enemy_before = _extract_enemy_snapshot(before)
    enemy_after = _extract_enemy_snapshot(after)
    enemy_line = _format_snapshot_delta("敌方", enemy_before, enemy_after, ("hp", "block"))
    if enemy_line:
        lines.append(enemy_line)

    intent_before = enemy_before.get("intent")
    intent_after = enemy_after.get("intent")
    if intent_before or intent_after:
        lines.append(f"敌方意图：{intent_before or '无'} -> {intent_after or '无'}")

    queue_before = _extract_minion_queue(before)
    queue_after = _extract_minion_queue(after)
    if queue_before or queue_after:
        lines.append(f"仆从队列：{queue_before} -> {queue_after}")

    effect_line = _infer_effect_line(player_before, player_after, enemy_before, enemy_after, intent_before)
    if effect_line:
        lines.append(effect_line)

    return lines


def _extract_player_snapshot(state: GameState) -> dict[str, int | None]:
    combat = getattr(state, "combat", None)
    player = combat.get("player") if isinstance(combat, dict) else {}
    hand = combat.get("hand") if isinstance(combat, dict) else None
    return {
        "hp": _int_or_none((player or {}).get("current_hp", (player or {}).get("hp"))),
        "block": _int_or_none((player or {}).get("block")),
        "energy": _int_or_none((player or {}).get("energy")),
        "hand": len(hand) if isinstance(hand, list) else None,
    }


def _extract_enemy_snapshot(state: GameState) -> dict[str, Any]:
    combat = getattr(state, "combat", None)
    enemies = combat.get("enemies") if isinstance(combat, dict) else None
    if not isinstance(enemies, list) or not enemies:
        return {"hp": None, "block": None, "intent": None}

    alive = [enemy for enemy in enemies if isinstance(enemy, dict) and enemy.get("is_alive", True)]
    pool = alive or [enemy for enemy in enemies if isinstance(enemy, dict)]
    total_hp = 0
    has_hp = False
    total_block = 0
    has_block = False
    first = pool[0] if pool else {}
    for enemy in pool:
        hp = _int_or_none(enemy.get("current_hp", enemy.get("hp")))
        if hp is not None:
            total_hp += hp
            has_hp = True
        block = _int_or_none(enemy.get("block"))
        if block is not None:
            total_block += block
            has_block = True
    return {
        "hp": total_hp if has_hp else None,
        "block": total_block if has_block else None,
        "intent": _describe_enemy_intent(first if isinstance(first, dict) else {}),
    }


def _extract_minion_queue(state: GameState) -> list[str]:
    combat = getattr(state, "combat", None)
    if isinstance(combat, dict):
        queue = combat.get("minion_queue")
        if isinstance(queue, list):
            return [
                str(item.get("id") or item.get("name") or item.get("card_id") or "")
                for item in queue
                if isinstance(item, dict)
            ]

    agent_view = getattr(state, "agent_view", None)
    if isinstance(agent_view, dict):
        agent_combat = agent_view.get("combat")
        if isinstance(agent_combat, dict):
            queue = agent_combat.get("minion_queue")
            if isinstance(queue, list):
                return [
                    str(item.get("id") or item.get("name") or item.get("card_id") or "")
                    for item in queue
                    if isinstance(item, dict)
                ]
    return []


def _describe_enemy_intent(enemy: dict[str, Any]) -> str | None:
    if not enemy:
        return None
    intent = enemy.get("intent") or enemy.get("intent_id") or enemy.get("intent_type")
    amount = enemy.get("intent_damage") or enemy.get("damage")
    hits = enemy.get("intent_hits") or enemy.get("hits")
    if intent is None:
        return None
    suffix = ""
    if isinstance(amount, int) and isinstance(hits, int) and hits > 1:
        suffix = f"({amount}x{hits})"
    elif isinstance(amount, int):
        suffix = f"({amount})"
    return f"{intent}{suffix}"


def _format_snapshot_delta(
    label: str,
    before: dict[str, Any],
    after: dict[str, Any],
    keys: tuple[str, ...],
) -> str:
    labels = {
        "hp": "生命",
        "block": "格挡",
        "energy": "能量",
        "hand": "手牌",
    }
    parts: list[str] = []
    for key in keys:
        old = before.get(key)
        new = after.get(key)
        if old is None and new is None:
            continue
        delta = (
            f" ({new - old:+d})"
            if isinstance(old, int) and isinstance(new, int) and new != old
            else ""
        )
        parts.append(f"{labels[key]} {old} -> {new}{delta}")
    if not parts:
        return ""
    return f"{label}：{'，'.join(parts)}"


def _infer_effect_line(
    player_before: dict[str, Any],
    player_after: dict[str, Any],
    enemy_before: dict[str, Any],
    enemy_after: dict[str, Any],
    intent_before: Any,
) -> str:
    hp_delta = _delta(player_before.get("hp"), player_after.get("hp"))
    block_delta = _delta(player_before.get("block"), player_after.get("block"))
    enemy_hp_delta = _delta(enemy_before.get("hp"), enemy_after.get("hp"))
    if enemy_hp_delta < 0:
        return f"结果解读：敌方总生命减少 {-enemy_hp_delta}。"
    if hp_delta < 0 or block_delta < 0:
        return (
            "结果解读：敌方回合已结算"
            f"（先前意图：{intent_before or '未知'}），"
            f"玩家生命 {hp_delta:+d}，格挡 {block_delta:+d}。"
        )
    if block_delta > 0:
        return f"结果解读：玩家获得 {block_delta} 点格挡。"
    if hp_delta > 0:
        return f"结果解读：玩家回复 {hp_delta} 点生命。"
    return ""


def _delta(before: Any, after: Any) -> int:
    if isinstance(before, int) and isinstance(after, int):
        return after - before
    return 0


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


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
    settle_timeout: float = 5.0,
    settle_poll_interval: float = 0.5,
) -> FluentBuilder:
    """Create a new FluentBuilder. Main entry point for the Fluent API."""
    return FluentBuilder(
        case_id=case_id,
        orchestrator=orchestrator,
        loop=loop,
        settle_timeout=settle_timeout,
        settle_poll_interval=settle_poll_interval,
    )


# Alias matching AC/PRD example: test("card damage").setup(...)
# Named `define` as primary to avoid shadowing pytest's `test` fixture.
test = define
