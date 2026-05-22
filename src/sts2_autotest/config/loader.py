"""Four-layer configuration loader for STS2-AUTOTEST (FR39).

Layer precedence (latter overrides former):
1. Built-in defaults (schema.py Field defaults)
2. Project YAML file (sts2-autotest.yaml)
3. Environment variables (STS2_ prefix) + .env file
4. CLI arguments (passed as dict)
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel

from sts2_autotest.config.schema import STS2Config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Override wins on conflicts."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(project_dir: Path) -> dict[str, Any]:
    """Load sts2-autotest.yaml from project directory."""
    yaml_path = project_dir / "sts2-autotest.yaml"
    if not yaml_path.is_file():
        return {}
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _parse_env_vars() -> dict[str, Any]:
    """Parse STS2_ prefixed environment variables into nested dict.

    Uses __ (double underscore) as section separator, preserving
    single underscores within field names.

    Mapping: STS2_SECTION__FIELD → section.field
             STS2_SECTION__SUBSECTION__FIELD → section.subsection.field
    """
    result: dict[str, Any] = {}
    prefix = "STS2_"
    sep = "__"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        remainder = key[len(prefix):]
        if not remainder:
            continue
        parts = remainder.lower().split(sep)
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
    return result


def _load_dotenv(project_dir: Path) -> dict[str, Any]:
    """Load .env file and return STS2_ prefixed variables as nested dict."""
    dotenv_path = project_dir / ".env"
    if not dotenv_path.is_file():
        return {}
    values = dotenv_values(dotenv_path)
    result: dict[str, Any] = {}
    prefix = "STS2_"
    sep = "__"
    for key, value in values.items():
        if value is None or not key.startswith(prefix):
            continue
        remainder = key[len(prefix):]
        if not remainder:
            continue
        parts = remainder.lower().split(sep)
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
    return result


def _coerce_types(
    overrides: dict[str, Any], schema: type[BaseModel]
) -> dict[str, Any]:
    """Best-effort type coercion for env var strings based on schema field types."""
    coerced: dict[str, Any] = {}
    schema_fields = schema.model_fields
    for key, value in overrides.items():
        if key in schema_fields and isinstance(value, str):
            field_info = schema_fields[key]
            annotation = field_info.annotation
            if annotation is bool:
                if value.lower() in ("true", "1", "yes"):
                    coerced[key] = True
                elif value.lower() in ("false", "0", "no"):
                    coerced[key] = False
                else:
                    raise ValueError(
                        f"Config key '{key}' expects bool but got '{value}'"
                    )
            elif annotation is int:
                try:
                    coerced[key] = int(value)
                except ValueError:
                    raise ValueError(
                        f"Config key '{key}' expects int but got '{value}'"
                    ) from None
            elif annotation is float:
                try:
                    coerced[key] = float(value)
                except ValueError:
                    raise ValueError(
                        f"Config key '{key}' expects float but got '{value}'"
                    ) from None
            else:
                coerced[key] = value
        elif isinstance(value, dict):
            # Recurse into sub-models
            if key in schema_fields:
                sub_annotation = schema_fields[key].annotation
                if (
                    isinstance(sub_annotation, type)
                    and issubclass(sub_annotation, BaseModel)
                ):
                    coerced[key] = _coerce_types(value, sub_annotation)
                else:
                    coerced[key] = value
            else:
                coerced[key] = value
        else:
            coerced[key] = value
    return coerced


def load_config(
    project_dir: Path | str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> STS2Config:
    """Load configuration with four-layer inheritance.

    Args:
        project_dir: Directory to search for sts2-autotest.yaml and .env.
                     Defaults to current working directory.
        cli_overrides: Dict from CLI argument parsing (e.g., click params).
                       Highest priority layer.

    Returns:
        Frozen STS2Config instance.

    Raises:
        ConfigValidationError: On validation failures with precise error info.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    elif isinstance(project_dir, str):
        project_dir = Path(project_dir)

    # Layer 1: defaults are baked into STS2Config Field defaults
    merged: dict[str, Any] = {}

    # Layer 2: YAML
    yaml_data = _load_yaml(project_dir)
    if yaml_data:
        merged = _deep_merge(merged, yaml_data)

    # Layer 3: env vars + .env
    env_data = _parse_env_vars()
    dotenv_data = _load_dotenv(project_dir)
    env_combined = _deep_merge(dotenv_data, env_data)
    if env_combined:
        env_coerced = _coerce_types(env_combined, STS2Config)
        merged = _deep_merge(merged, env_coerced)

    # Layer 4: CLI overrides
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)

    # Validate and construct frozen config
    try:
        return STS2Config(**merged)
    except Exception as exc:
        from pydantic import ValidationError as PydanticVE
        from sts2_autotest.config.errors import ConfigValidationError

        if isinstance(exc, PydanticVE):
            raise ConfigValidationError(exc, "config") from exc
        raise
