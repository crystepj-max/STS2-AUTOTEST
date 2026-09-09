"""统一构造运行期公共能力。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast


def build_lifecycle_manager(adapter: Any, steam_controller: Any, evidence_root: Path) -> Any:
    """构建可自动重启的生命周期管理器。

    优先使用显式环境变量（STS2_GAME_EXE / STS2_GAME_DIR）；二者皆缺时回退到
    discovery 自动定位 Steam 游戏目录。只有确实无法定位游戏时才返回 None，
    使预检能够真正尝试「游戏未启动 → 自行拉起」的恢复，而非静默跳过（修复四：
    系统重启后 8080 connection refused 的原始问题）。无法拉起时由预检返回显式
    环境阻塞，而不是把任务放行到旅程里才失败。
    """
    game_exe = os.environ.get("STS2_GAME_EXE")
    game_dir = os.environ.get("STS2_GAME_DIR")
    if not game_exe and not game_dir:
        from sts2_autotest.adapters.discovery import find_game_dir

        discovered = find_game_dir()
        if discovered is None:
            return None
        game_dir = str(discovered)

    from sts2_autotest.core.lifecycle import GameLifecycleManager

    game_log = evidence_root / "logs" / "game-process.log"
    game_log.parent.mkdir(parents=True, exist_ok=True)
    return GameLifecycleManager(
        adapter,
        game_exe=game_exe,
        game_dir=game_dir,
        steam_controller=steam_controller,
        game_log=str(game_log),
    )


def _get_env(keys: list[str], default: str) -> str:
    """按 STS2_ 前缀约定查找环境变量：依次尝试各键，全部缺失时返回默认值。

    与 config loader 的环境变量约定保持一致但不 import config。
    自 cli.main._get_env 下沉（其唯一消费者是 adapter 构造）。
    """
    for key in keys:
        val = os.environ.get(key)
        if val is not None:
            return val
    return default


def create_adapter_from_env(adapter_type: str, project: str | None = None) -> Any:
    """按环境变量约定构造适配器（自 cli.main._create_adapter 下沉）。

    worker 子进程（``python -m sts2_autotest.core.run_executor``）经此在
    进程内构造 adapter；CLI 层保留同名委托 ``_create_adapter``，行为与
    测试 patch 点均不变。

    Args:
        adapter_type: "cli" 或 "agent"。
        project: 任务携带的项目名；提供时按该项目自己的配置目录读取
            项目扩展规则（卡牌前缀、种子命令模板），实现按任务隔离。

    Returns:
        GameAdapterProtocol 兼容的适配器实例。
    """
    if adapter_type == "agent":
        from sts2_autotest.adapters.agent import AgentAdapter, FastMcpAgentClient
        from sts2_autotest.adapters.project_extension import (
            load_card_id_prefixes,
            load_seed_command_template,
            resolve_base_dir,
        )
        from sts2_autotest.core.workspace import resolve_project_base_dir

        extension_base_dir = resolve_project_base_dir(project) or resolve_base_dir()

        transport_raw = _get_env(["STS2_ADAPTER__AGENT__TRANSPORT"], "http")
        if transport_raw not in ("http", "mcp"):
            raise ValueError(
                "STS2_ADAPTER__AGENT__TRANSPORT must be 'http' or 'mcp'"
            )
        transport = cast(Literal["http", "mcp"], transport_raw)
        agent_endpoint = _get_env(
            ["STS2_ADAPTER__AGENT__ENDPOINT"], "http://127.0.0.1:8080"
        )
        mcp_client = (
            FastMcpAgentClient(
                endpoint=_get_env(
                    ["STS2_ADAPTER__AGENT__MCP_ENDPOINT"],
                    agent_endpoint,
                )
            )
            if transport == "mcp"
            else None
        )

        return AgentAdapter(
            endpoint=agent_endpoint,
            timeout=float(_get_env(["STS2_ADAPTER__AGENT__TIMEOUT"], "30")),
            tool_profile=_get_env(
                ["STS2_ADAPTER__AGENT__TOOL_PROFILE"], "guided"
            ),
            debug_actions=_get_env(
                ["STS2_ADAPTER__AGENT__DEBUG_ACTIONS"], "false"
            ).lower()
            in ("true", "1", "yes"),
            mcp_client=mcp_client,
            transport=transport,
            health_path=_get_env(["STS2_ADAPTER__AGENT__HEALTH_PATH"], "health"),
            state_path=_get_env(
                ["STS2_ADAPTER__AGENT__STATE_PATH"], "state"
            ),
            actions_path=_get_env(
                ["STS2_ADAPTER__AGENT__ACTIONS_PATH"], "actions/available"
            ),
            act_path=_get_env(["STS2_ADAPTER__AGENT__ACT_PATH"], "action"),
            wait_path=_get_env(
                ["STS2_ADAPTER__AGENT__WAIT_PATH"], "wait_until_actionable"
            ),
            card_id_prefixes=load_card_id_prefixes(extension_base_dir),
            seed_command_template=load_seed_command_template(extension_base_dir),
        )
    else:
        from sts2_autotest.adapters.cli_mod import CliModAdapter

        cli_path = os.environ.get("STS2_ADAPTER__CLI__CLI_PATH")
        cli_timeout = float(
            os.environ.get("STS2_ADAPTER__CLI__TIMEOUT", "30")
        )
        return CliModAdapter(cli_path=cli_path, timeout=cli_timeout)


def is_agent_default() -> bool:
    """STS2_ADAPTER__AGENT__ENABLED 是否将 agent 适配器设为默认。

    自 cli.main._is_agent_default 下沉（worker 入口需要同一判定），
    CLI 层保留同名委托。
    """
    raw = os.environ.get("STS2_ADAPTER__AGENT__ENABLED", "false")
    return raw.lower() in ("true", "1", "yes")
