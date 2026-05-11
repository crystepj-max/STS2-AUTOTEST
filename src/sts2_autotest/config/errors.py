"""Configuration validation error with precise field-level reporting (FR38)."""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError


@dataclass(frozen=True)
class ConfigErrorDetail:
    """Single configuration error with precise location."""

    field: str
    message: str
    invalid_value: Any
    source: str  # "yaml" / "env" / "cli" / "default"


class ConfigValidationError(Exception):
    """Wraps pydantic ValidationError for precise error reporting.

    Provides filename + field + invalid value precision (NFR29).
    """

    def __init__(self, validation_error: ValidationError, source: str) -> None:
        self.errors: list[ConfigErrorDetail] = []
        for err in validation_error.errors():
            self.errors.append(ConfigErrorDetail(
                field=".".join(str(loc) for loc in err["loc"]),
                message=err["msg"],
                invalid_value=err.get("input"),
                source=source,
            ))
        error_msgs = "; ".join(
            f"{e.field}: {e.message} (value={e.invalid_value!r}, source={e.source})"
            for e in self.errors
        )
        super().__init__(f"Configuration validation failed: {error_msgs}")
