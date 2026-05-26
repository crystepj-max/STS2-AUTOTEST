"""端到端冒烟测试：从开局跑到第一场战斗结束。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sts2_autotest.adapters.base import ActionResult
from sts2_autotest.adapters.cli_mod import CliModAdapter
from sts2_autotest.adapters.discovery import discover_sts2_cli
from sts2_autotest.evidence.capture import ScreenCapture

OUTPUT_DIR = Path("tests/output/1sttest")
WINDOW_TITLE = "Slay the Spire 2"


def log(msg: str) -> None:
    print(f"  {msg}")


def _is_recoverable_bootstrap_screen(screen: str) -> bool:
    return screen in {
        "UNKNOWN",
        "MAIN_MENU",
        "CHARACTER_SELECT",
        "EVENT",
        "GAME_OVER",
        "VICTORY",
    }


def _is_early_first_battle_state(screen: str, payload: dict[str, Any]) -> bool:
    if screen == "EVENT":
        return str(payload.get("event", {}).get("event_id", "")).upper() == "NEOW"
    if screen == "MAP":
        map_payload = payload.get("map", {})
        act_floor = map_payload.get("act_floor")
        current_row = map_payload.get("current_coord", {}).get("row")
        return act_floor == 1 and current_row == 0
    if screen == "COMBAT":
        combat_payload = payload.get("combat", {})
        turn_number = combat_payload.get("turn_number")
        deck_count = combat_payload.get("player", {}).get("deck_count")
        return (
            isinstance(turn_number, int)
            and turn_number <= 3
            and isinstance(deck_count, int)
            and 9 <= deck_count <= 12
        )
    return False


def _is_post_embark_screen(screen: str) -> bool:
    return screen in {"EVENT", "MAP", "COMBAT"}


def _is_bootstrap_complete_screen(screen: str) -> bool:
    return screen in {"CHARACTER_SELECT", "EVENT", "MAP", "COMBAT"}


def _choose_grid_card_action(
    grid_payload: dict[str, Any],
) -> dict[str, Any] | None:
    cards = grid_payload.get("cards", [])
    if not cards:
        return None

    for card in cards:
        card_id = str(card.get("card_id", ""))
        if "STRIKE" in card_id.upper():
            return {"card_id": card_id}

    first = cards[0]
    first_id = str(first.get("card_id", ""))
    if first_id:
        return {"card_id": first_id}
    return None


def _choose_tri_card_action(
    tri_payload: dict[str, Any],
) -> dict[str, Any] | None:
    cards = tri_payload.get("cards", [])
    if not cards:
        return None

    for card in cards:
        card_id = str(card.get("card_id") or card.get("id") or "")
        upper = card_id.upper()
        if "STRIKE" in upper or "ATTACK" in upper:
            return {"card_id": card_id}

    first = cards[0]
    first_id = str(first.get("card_id") or first.get("id") or "")
    if first_id:
        return {"card_id": first_id}
    return None


def _choose_map_coord(
    map_payload: dict[str, Any],
) -> dict[str, Any] | None:
    travelable = map_payload.get("travelable_coords", [])
    if not travelable:
        return None

    node_by_coord: dict[tuple[int, int], dict[str, Any]] = {}
    for node in map_payload.get("nodes", []):
        col = node.get("col")
        row = node.get("row")
        if isinstance(col, int) and isinstance(row, int):
            node_by_coord[(col, row)] = node

    for coord in travelable:
        col = coord.get("col")
        row = coord.get("row")
        if not isinstance(col, int) or not isinstance(row, int):
            continue
        node = node_by_coord.get((col, row), {})
        if str(node.get("type", "")).upper() == "MONSTER":
            return {"col": col, "row": row}

    first = travelable[0]
    col = first.get("col")
    row = first.get("row")
    if isinstance(col, int) and isinstance(row, int):
        return {"col": col, "row": row}
    return None


def _choose_play_card_args(
    card: dict[str, Any],
    combat_payload: dict[str, Any],
) -> dict[str, Any]:
    args: dict[str, Any] = {"card_id": str(card.get("id", ""))}
    if str(card.get("target_type", "")) == "AnyEnemy":
        for enemy in combat_payload.get("enemies", []):
            if enemy.get("is_alive") and isinstance(enemy.get("combat_id"), int):
                args["target"] = enemy["combat_id"]
                break
    return args


def _choose_bootstrap_action(
    screen: str,
    actions: list[str],
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any] | None] | None:
    if screen in {"GAME_OVER", "VICTORY"} and "return_to_menu" in actions:
        return ("return_to_menu", None)

    in_singleplayer_submenu = "singleplayer_submenu" in payload
    if screen == "MAIN_MENU" and in_singleplayer_submenu and "choose_game_mode" in actions:
        return ("choose_game_mode", {"mode": "standard"})

    has_run_save = bool(payload.get("menu", {}).get("has_run_save"))
    if screen == "MAIN_MENU" and has_run_save and "abandon_run" in actions:
        return ("abandon_run", None)
    if screen == "MAIN_MENU" and "new_run" in actions:
        return ("new_run", None)

    if screen == "EVENT" and "choose_event" in actions:
        return ("choose_event", {"choice": 0})
    if screen == "UNKNOWN":
        progress_action = _choose_unknown_progress_action(payload)
        if progress_action is not None:
            return progress_action
    return None


def _choose_reward_action(
    actions: list[str],
    payload: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None] | None:
    if "reward_skip_card" in actions:
        return ("reward_skip_card", {"type": "card"})
    if "relic_skip" in actions:
        return ("relic_skip", None)
    rewards = (payload or {}).get("rewards", {}).get("rewards", [])
    for reward in rewards:
        if str(reward.get("type", "")).upper() == "CARD":
            return ("reward_skip_card", {"type": "card"})
    return None


def _choose_event_progress_action(
    screen: str,
    actions: list[str],
) -> tuple[str, dict[str, Any] | None] | None:
    if screen != "EVENT":
        return None
    if "choose_event" in actions:
        return ("choose_event", {"choice": 0})
    if "advance_dialogue" in actions:
        return ("advance_dialogue", None)
    return None


def _choose_unknown_progress_action(
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any] | None] | None:
    if "grid_card_select" in payload:
        grid_action = _choose_grid_card_action(payload["grid_card_select"])
        if grid_action is not None:
            return ("grid_select_card", grid_action)
    if "tri_select" in payload:
        tri_action = _choose_tri_card_action(payload["tri_select"])
        if tri_action is not None:
            return ("tri_select_card", tri_action)
        return ("tri_select_skip", None)
    return None


def _choose_post_embark_progress_action(
    screen: str,
    actions: list[str],
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any] | None] | None:
    if screen == "CHARACTER_SELECT" and "embark" in actions:
        return ("embark", None)
    if screen == "UNKNOWN":
        return _choose_unknown_progress_action(payload)
    return _choose_event_progress_action(screen, actions)


def _build_adapter() -> CliModAdapter:
    cli_path = discover_sts2_cli()
    if cli_path is None:
        raise RuntimeError(
            "未找到 STS2-Cli-Mod CLI，请先设置 STS2_CLI_PATH 或安装 sts2.exe。"
        )
    return CliModAdapter(cli_path=cli_path, timeout=20.0)


def _build_capture() -> ScreenCapture:
    return ScreenCapture(OUTPUT_DIR)


async def screenshot(capture: ScreenCapture, step: int, label: str) -> None:
    result = capture.capture_with_validation(WINDOW_TITLE, f"test{step:02d}_{label}")
    if result.ok:
        log(f"[截图] {label} -> {result.path}")
    else:
        log(f"[截图] {label} skipped ({result.message})")


async def read_context(
    adapter: CliModAdapter,
) -> tuple[str, list[str], dict[str, Any]]:
    state = await adapter.get_state()
    actions = await adapter.get_available_actions()
    payload = json.loads(state.model_dump_json())
    return state.screen.value, actions, payload


async def act(
    adapter: CliModAdapter,
    label: str,
    action: str,
    args: dict[str, Any] | None = None,
    *,
    required: bool = True,
) -> tuple[ActionResult, str, list[str], dict[str, Any]]:
    result = await adapter.act(action, args or {})
    screen, actions, payload = await read_context(adapter)
    print(f"\n[{label}] {action}{' ' + str(args) if args else ''}")
    print(f"  -> status={result.status}, screen={screen}, actions={actions}")
    if result.detail:
        print(f"  -> detail={result.detail[:160]}")
    if required and result.status != "success":
        raise RuntimeError(
            f"{label} 执行失败: action={action}, status={result.status}, "
            f"screen={screen}, detail={result.detail}, state={payload}"
        )
    return result, screen, actions, payload


async def wait_for_screen(
    adapter: CliModAdapter,
    target: str,
    timeout: float = 25.0,
) -> tuple[str, list[str], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: tuple[str, list[str], dict[str, Any]] | None = None
    while time.monotonic() < deadline:
        last = await read_context(adapter)
        if last[0] == target:
            return last
        await asyncio.sleep(0.5)
    if last is None:
        last = await read_context(adapter)
    return last


async def wait_for_any_screen(
    adapter: CliModAdapter,
    targets: set[str],
    timeout: float = 25.0,
) -> tuple[str, list[str], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: tuple[str, list[str], dict[str, Any]] | None = None
    while time.monotonic() < deadline:
        last = await read_context(adapter)
        if last[0] in targets:
            return last
        await asyncio.sleep(0.5)
    if last is None:
        last = await read_context(adapter)
    return last


async def advance_until_map_or_combat(
    adapter: CliModAdapter,
    capture: ScreenCapture,
    *,
    initial_screen: str,
    initial_actions: list[str],
    initial_payload: dict[str, Any],
    step: int,
    timeout: float = 25.0,
) -> tuple[str, list[str], dict[str, Any]]:
    screen = initial_screen
    actions = initial_actions
    payload = initial_payload
    deadline = time.monotonic() + timeout
    attempt = 0

    while time.monotonic() < deadline:
        if screen in {"MAP", "COMBAT"}:
            return screen, actions, payload

        action_spec = _choose_post_embark_progress_action(screen, actions, payload)
        if action_spec is not None:
            attempt += 1
            action, args = action_spec
            required = not (action == "embark")
            result, screen, actions, payload = await act(
                adapter,
                f"Step{step} - progress {attempt}",
                action,
                args,
                required=required,
            )
            if result.status != "success":
                detail = result.detail or ""
                if (
                    action == "embark"
                    and "Not on character select screen" in detail
                    and screen in {"EVENT", "MAP", "COMBAT", "UNKNOWN"}
                ):
                    log("embark 与界面跳转发生竞态，按已离开角色选择继续")
                else:
                    raise RuntimeError(
                        f"Step{step} - progress {attempt} 执行失败: "
                        f"action={action}, status={result.status}, screen={screen}, "
                        f"detail={result.detail}, state={payload}"
                    )
            await screenshot(capture, step, f"progress_{attempt}_{action}")
            continue

        if screen == "UNKNOWN":
            log("等待事件/地图状态稳定...")
            await asyncio.sleep(1.0)
            screen, actions, payload = await read_context(adapter)
            continue

        log(f"等待地图... 当前: {screen}")
        await asyncio.sleep(1.0)
        screen, actions, payload = await read_context(adapter)

    raise RuntimeError(
        f"等待 MAP/COMBAT 超时。当前 screen={screen}, actions={actions}, state={payload}"
    )


async def wait_for_player_turn(
    adapter: CliModAdapter,
    *,
    timeout: float = 20.0,
) -> tuple[str, list[str], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: tuple[str, list[str], dict[str, Any]] | None = None

    while time.monotonic() < deadline:
        last = await read_context(adapter)
        screen, _actions, payload = last
        if screen != "COMBAT":
            return last

        combat = payload.get("combat", {})
        is_player_turn = bool(combat.get("is_player_turn"))
        is_actions_disabled = bool(combat.get("is_player_actions_disabled"))
        hand = combat.get("hand", [])
        if is_player_turn and not is_actions_disabled and hand:
            return last
        await asyncio.sleep(0.5)

    if last is None:
        last = await read_context(adapter)
    return last


async def settle_unknown_screen(
    adapter: CliModAdapter,
    *,
    timeout: float = 10.0,
) -> tuple[str, list[str], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last = await read_context(adapter)

    while time.monotonic() < deadline:
        screen, _actions, _payload = last
        if screen != "UNKNOWN":
            return last
        await asyncio.sleep(0.5)
        last = await read_context(adapter)

    return last


async def bootstrap_to_fresh_start(
    adapter: CliModAdapter,
    capture: ScreenCapture,
) -> tuple[str, list[str], dict[str, Any]]:
    screen, actions, payload = await read_context(adapter)
    log(f"当前画面: {screen}, 动作={actions}")

    if not _is_recoverable_bootstrap_screen(screen) and not _is_early_first_battle_state(
        screen, payload
    ):
        raise RuntimeError(
            f"检测到中途 run，无法可靠复位到新游戏起点。"
            f"当前 screen={screen}, actions={actions}, state={payload}。"
            "请先手动回到主菜单后重试。"
        )

    for attempt in range(1, 9):
        if screen in {"UNKNOWN", "MAIN_MENU", "GAME_OVER", "VICTORY", "EVENT"}:
            action_spec = _choose_bootstrap_action(screen, actions, payload)
            if action_spec is None:
                if screen == "UNKNOWN":
                    log("等待可识别状态...")
                    await asyncio.sleep(1.0)
                    screen, actions, payload = await read_context(adapter)
                    continue
                break
            action, args = action_spec
            _, screen, actions, payload = await act(
                adapter,
                f"Bootstrap {attempt}",
                action,
                args,
            )
            await screenshot(capture, 1, f"bootstrap_{attempt}_{action}")
            continue

        if _is_bootstrap_complete_screen(screen) or _is_early_first_battle_state(
            screen, payload
        ):
            return screen, actions, payload

        break

    if not _is_bootstrap_complete_screen(screen) and not _is_early_first_battle_state(
        screen, payload
    ):
        raise RuntimeError(
            f"未能进入可继续状态（CHARACTER_SELECT/EVENT/MAP/COMBAT）。"
            f"当前 screen={screen}, actions={actions}, state={payload}"
        )
    return screen, actions, payload


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    adapter = _build_adapter()
    capture = _build_capture()

    print("=" * 60)
    print("  端到端测试：Ironclad 首场战斗")
    print(f"  输出目录: {OUTPUT_DIR}")
    print("=" * 60)

    screen, actions, payload = await bootstrap_to_fresh_start(adapter, capture)

    if screen == "CHARACTER_SELECT":
        _, screen, actions, payload = await act(
            adapter,
            "Step2 - select IRONCLAD",
            "select_character",
            {"character_id": "IRONCLAD"},
        )
        await screenshot(capture, 2, "after_select")
        selected = payload.get("character_select", {}).get("selected_character")
        log(f"已选角色: {selected}")

        _, screen, actions, payload = await act(
            adapter,
            "Step3 - embark",
            "embark",
        )
        await screenshot(capture, 3, "after_embark")
        screen, actions, payload = await wait_for_any_screen(
            adapter,
            {"EVENT", "MAP", "UNKNOWN", "COMBAT"},
            timeout=20.0,
        )
        screen, actions, payload = await advance_until_map_or_combat(
            adapter,
            capture,
            initial_screen=screen,
            initial_actions=actions,
            initial_payload=payload,
            step=4,
            timeout=25.0,
        )

    if screen not in {"MAP", "COMBAT"}:
        screen, actions, payload = await advance_until_map_or_combat(
            adapter,
            capture,
            initial_screen=screen,
            initial_actions=actions,
            initial_payload=payload,
            step=4,
            timeout=25.0,
        )

    if screen == "MAP":
        log("到达地图。")
        await screenshot(capture, 4, "on_map")

        map_coord = _choose_map_coord(payload.get("map", {}))
        if map_coord is None:
            raise RuntimeError(f"地图中没有可走节点。state={payload}")

        _, screen, actions, payload = await act(
            adapter,
            "Step5 - choose_map_node",
            "choose_map_node",
            map_coord,
        )
        await screenshot(capture, 5, "after_choose_node")
        await screenshot(capture, 5, f"node_type_{screen}")

        event_action = _choose_event_progress_action(screen, actions)
        if event_action is not None:
            action, args = event_action
            _, screen, actions, payload = await act(
                adapter,
                f"Step5b - {action}",
                action,
                args,
            )
        elif screen == "REST" and "choose_rest_option" in actions:
            _, screen, actions, payload = await act(
                adapter,
                "Step5b - rest choose heal",
                "choose_rest_option",
                {"option_id": "REST"},
                required=False,
            )
        elif screen == "MAP" and "proceed" in actions:
            _, screen, actions, payload = await act(
                adapter,
                "Step5b - proceed",
                "proceed",
            )
            await screenshot(capture, 5, "after_proceed")

        if screen == "MAP":
            map_coord = _choose_map_coord(payload.get("map", {}))
            if map_coord is None:
                raise RuntimeError(f"地图中没有第二个可走节点。state={payload}")
            _, screen, actions, payload = await act(
                adapter,
                "Step6 - choose another node",
                "choose_map_node",
                map_coord,
            )
            await screenshot(capture, 6, "second_node")

    if screen != "COMBAT":
        screen, actions, payload = await wait_for_screen(adapter, "COMBAT", timeout=20.0)
    log(f"战斗画面: {screen}")
    await screenshot(capture, 6, "enter_combat")
    if screen != "COMBAT":
        raise RuntimeError(
            f"未能进入 COMBAT。当前 screen={screen}, actions={actions}, state={payload}"
        )

    for turn in range(1, 16):
        screen, actions, payload = await wait_for_player_turn(adapter, timeout=15.0)
        if screen != "COMBAT":
            log(f"战斗结束，当前画面: {screen}")
            break

        combat = payload.get("combat", {})
        hand = combat.get("hand", [])
        log(f"回合 {turn}: 手牌={hand}, 动作={actions}")

        played = False
        for card in hand:
            cid = card.get("id", "") if isinstance(card, dict) else str(card)
            upper = cid.upper()
            if "STRIKE" in upper or "ATTACK" in upper:
                _, screen, actions, payload = await act(
                    adapter,
                    f"Step7.{turn} - attack {cid}",
                    "play_card",
                    _choose_play_card_args(card, combat),
                )
                played = True
                break

        if not played:
            for card in hand:
                cid = card.get("id", "") if isinstance(card, dict) else str(card)
                upper = cid.upper()
                if "DEFEND" in upper or "BLOCK" in upper:
                    _, screen, actions, payload = await act(
                        adapter,
                        f"Step7.{turn} - defend {cid}",
                        "play_card",
                        _choose_play_card_args(card, combat),
                    )
                    played = True
                    break

        if screen == "UNKNOWN":
            screen, actions, payload = await settle_unknown_screen(adapter, timeout=10.0)
        if screen != "COMBAT":
            log(f"出牌后离开战斗，当前画面: {screen}")
            break

        await act(adapter, f"Step7.{turn} - end_turn", "end_turn")
        await screenshot(capture, 7, f"turn_{turn}")

    screen, actions, payload = await read_context(adapter)
    await screenshot(capture, 7, "combat_end")

    reward_action = _choose_reward_action(actions, payload)
    if reward_action is not None:
        action, args = reward_action
        await act(adapter, "Step8 - skip reward", action, args)
        await screenshot(capture, 8, "skip_reward")
        screen, actions, payload = await read_context(adapter)

    if screen == "MAP":
        log("已回到地图，无需处理奖励。")
    elif "proceed" in actions or "rewards" in payload:
        await act(adapter, "Step8 - proceed", "proceed")
        await screenshot(capture, 8, "proceed")
    else:
        log(f"当前画面: {screen}，未找到可安全跳过的奖励动作。")

    print(f"\n{'=' * 60}")
    print(f"  测试完成，截图目录: {OUTPUT_DIR}")
    screenshots = list(OUTPUT_DIR.glob('*.png'))
    print(f"  截图数量: {len(screenshots)}")
    for path in sorted(screenshots):
        print(f"    {path.name}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
