"""项目扩展配置统一读取（P2-1：平台默认全部为空中性）。

读取顺序（后者覆盖前者）：
1. MOD 项目配置文件（当前目录 ``sts2-autotest.yaml`` 的 ``project_extension`` 段；
   若不存在，经 ``sts2-mod.yaml`` 的 ``autotest.config`` 指针定位项目配置文件）；
2. ``STS2_PROJECT__*`` 环境变量。

pytest fixtures、CLI 适配器装配与 NL 代码生成统一经本模块获取，
不再依赖各入口自行解析临时启动设置。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_YAML_CANDIDATES = ("sts2-autotest.yaml", "sts2-autotest.yml")
_MOD_MANIFEST = "sts2-mod.yaml"


def parse_card_id_prefixes(raw: str) -> dict[str, str]:
    """解析 ``STS2_PROJECT__CARD_ID_PREFIXES`` 环境变量。

    格式：``alias1:PREFIX1,alias2:PREFIX2``（如 ``mymod:MYMOD-``）。
    返回小写 alias 到原样前缀的映射；空串返回空映射。
    """
    prefixes: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        alias, prefix = pair.split(":", 1)
        alias = alias.strip().lower()
        if alias:
            prefixes[alias] = prefix.strip()
    return prefixes


def _find_config_file(base_dir: Path) -> Path | None:
    """定位项目配置文件：常规名优先，其次 mod manifest 的 autotest.config 指针。"""
    for name in _YAML_CANDIDATES:
        candidate = base_dir / name
        if candidate.is_file():
            return candidate
    manifest = base_dir / _MOD_MANIFEST
    if manifest.is_file():
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except Exception:
            return None
        if isinstance(data, dict):
            autotest = data.get("autotest")
            if isinstance(autotest, dict):
                config = autotest.get("config")
                if isinstance(config, str) and config:
                    candidate = (base_dir / config).resolve()
                    if candidate.is_file():
                        return candidate
    return None


def _read_yaml_extension(base_dir: Path) -> dict[str, Any]:
    config_file = _find_config_file(base_dir)
    if config_file is None:
        return {}
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    ext = data.get("project_extension")
    return dict(ext) if isinstance(ext, dict) else {}


def load_card_id_prefixes(base_dir: Path | None = None) -> dict[str, str]:
    """卡牌规格写法到运行时 ID 前缀的映射（默认为空=原样透传）。"""
    ext = _read_yaml_extension(base_dir or Path.cwd())
    prefixes: dict[str, str] = {}
    raw = ext.get("card_id_prefixes")
    if isinstance(raw, dict):
        prefixes = {str(k).strip().lower(): str(v).strip() for k, v in raw.items()}
    prefixes.update(
        parse_card_id_prefixes(os.environ.get("STS2_PROJECT__CARD_ID_PREFIXES", ""))
    )
    return prefixes


def load_seed_command_template(base_dir: Path | None = None) -> str:
    """set_seed 的调试命令模板（默认空=set_seed 不可用）。"""
    ext = _read_yaml_extension(base_dir or Path.cwd())
    template = str(ext.get("seed_command_template") or "")
    env_value = os.environ.get("STS2_PROJECT__SEED_COMMAND_TEMPLATE", "")
    return env_value if env_value else template


def load_character_aliases(base_dir: Path | None = None) -> dict[str, str]:
    """自然语言角色别名到运行时角色标识的映射（默认为空）。"""
    ext = _read_yaml_extension(base_dir or Path.cwd())
    aliases: dict[str, str] = {}
    raw = ext.get("character_aliases")
    if isinstance(raw, dict):
        aliases = {str(k): str(v) for k, v in raw.items()}
    for pair in os.environ.get("STS2_PROJECT__CHARACTER_ALIASES", "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        name, character_id = pair.split(":", 1)
        if name.strip():
            aliases[name.strip()] = character_id.strip()
    return aliases
