---
name: sts2-start-game
description: Use when a task in the STS2 workspace needs Slay the Spire 2 running with STS2-Agent loaded, especially when `sts2 ping` reports `CONNECTION_ERROR` or `http://127.0.0.1:8080/health` is unreachable on macOS.
---

# STS2 Start Game

Use this skill for local macOS STS2 bootstrap in the current workspace.

## Workflow

1. Run `scripts/start_sts2.sh`.
2. If `sts2 ping` already succeeds, reuse the existing game session.
3. Otherwise try to open Steam as a best-effort pre-step, but do not fail on that alone; the decisive launch step is `open -b com.valvesoftware.steam "steam://run/2868840"`.
4. Poll for readiness with `STS2-Agent` first: `GET /health`, `GET /state`, and `GET /actions/available`; treat `sts2 ping` as an additional success path rather than the only signal.
5. If startup times out, inspect Steam logs before claiming the game failed to start:
   - `~/Library/Application Support/Steam/logs/gameprocess_log.txt`
   - `~/Library/Application Support/Steam/logs/console_log.txt`

## Command

```bash
bash scripts/start_sts2.sh --timeout 180
```

Optional flags:

- `--timeout <seconds>`: total wait budget. Default `180`.
- `--app-id <id>`: Steam app id. Default `2868840`.
- `--agent-url <url>`: Agent base URL. Default `http://127.0.0.1:8080`.

## Success Criteria

- `sts2 ping` exits `0`, or `STS2-Agent` reports a reachable `health/state/actions` surface
- `GET /health` succeeds
- `GET /state` succeeds
- `GET /actions/available` succeeds

## Notes

- `STS2-Agent` is a game-loaded mod, not a separate daemon to launch first.
- Prefer this skill over ad hoc `open steam://...` calls when a task depends on a usable in-game Agent surface.
