"""当前游戏状态续跑探针：从现有 run 推进到双重打击注入点。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sts2_autotest.adapters.cli_mod import CliModAdapter

OUTPUT = Path("tests/output/ironclad-twin-strike/current-state-probe.json")


def _state_summary(state: Any) -> dict[str, Any]:
    payload = state.model_dump(mode="json")
    summary: dict[str, Any] = {"screen": payload.get("screen")}
    for key in ("event", "map", "combat", "damage_events"):
        if key in payload:
            summary[key] = payload[key]
    return summary


async def main() -> int:
    adapter = CliModAdapter(timeout=30.0)
    steps: list[tuple[str, dict[str, Any] | None]] = [
        ("choose_event", {"index": 0}),
        ("choose_map_node", {"col": 2, "row": 1}),
        ("enter_combat", None),
        ("give_card", {"card_id": "TWIN_STRIKE"}),
        ("play_card", {"card_id": "TWIN_STRIKE", "target": 0}),
    ]

    records: list[dict[str, Any]] = []
    try:
        initial = await adapter.get_state()
        for action, args in steps:
            before = await adapter.get_state()
            available = await adapter.get_available_actions()
            result = await adapter.act(action, args)
            try:
                after = await adapter.get_state()
                after_summary = _state_summary(after)
            except Exception as exc:
                after_summary = {"error": str(exc)}
            record = {
                "action": action,
                "args": args,
                "before": _state_summary(before),
                "available_actions": available,
                "result": {
                    "status": result.status,
                    "state_changed": result.state_changed,
                    "detail": result.detail,
                },
                "after": after_summary,
            }
            records.append(record)
            if result.status != "success":
                break
    finally:
        await adapter.cleanup()

    report = {
        "initial": _state_summary(initial),
        "steps": records,
        "passed": bool(records) and records[-1]["action"] == "play_card" and records[-1]["result"]["status"] == "success",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
