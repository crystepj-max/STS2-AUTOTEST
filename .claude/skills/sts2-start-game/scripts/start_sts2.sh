#!/bin/bash
set -euo pipefail

TIMEOUT=180
APP_ID=2868840
AGENT_URL="http://127.0.0.1:8080"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --app-id)
      APP_ID="$2"
      shift 2
      ;;
    --agent-url)
      AGENT_URL="${2%/}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

HEALTH_URL="${AGENT_URL}/health"
ACTIONS_URL="${AGENT_URL}/actions/available"
STATE_URL="${AGENT_URL}/state"

start_steam_best_effort() {
  if open -a Steam 2>/dev/null; then
    return 0
  fi
  if [[ -d /Applications/Steam.app ]]; then
    open /Applications/Steam.app 2>/dev/null || true
  fi
}

launch_game_best_effort() {
  if open "steam://run/${APP_ID}" 2>/dev/null; then
    return 0
  fi
  if open -b com.valvesoftware.steam "steam://run/${APP_ID}" 2>/dev/null; then
    return 0
  fi
  return 0
}

is_agent_ready() {
  curl -fsS "${HEALTH_URL}" >/dev/null 2>&1 || return 1
  curl -fsS "${STATE_URL}" >/dev/null 2>&1 || return 1
  curl -fsS "${ACTIONS_URL}" >/dev/null 2>&1 || return 1
}

if sts2 ping >/dev/null 2>&1; then
  echo "STS2 already ready"
  exit 0
fi

if is_agent_ready; then
  echo "STS2 agent already ready"
  exit 0
else
  start_steam_best_effort
  sleep 5
  launch_game_best_effort
fi

deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  if sts2 ping >/dev/null 2>&1; then
    echo "STS2 ready via sts2 ping"
    exit 0
  fi
  if is_agent_ready; then
    echo "STS2 ready via STS2-Agent"
    exit 0
  fi
  sleep 3
done

echo "Timed out waiting for STS2 readiness after ${TIMEOUT}s" >&2
exit 1
