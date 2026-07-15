#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHONPATH=src:${PYTHONPATH:-} \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
.venv/bin/python -m pytest \
  tests/unit/test_agent_http_errors.py \
  tests/unit/test_start_new_run_flow.py \
  tests/unit/test_navigation_flow.py \
  tests/unit/test_orchestrator.py -k "map_vote or nav_to_screen or timed_out" \
  -q
