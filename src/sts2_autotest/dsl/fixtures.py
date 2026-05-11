"""Test data fixture loader (FR19). Loads card/relic/seed data from JSON/YAML."""

import copy
import json
from pathlib import Path
from typing import Any, Literal, cast

import yaml

FixtureScope = Literal["test", "class", "session"]


class FixtureLoader:
    """Load test data from JSON or YAML files.

    Each scope ("test", "class", "session") has an isolated cache.
    Returned data is a deep copy — callers cannot pollute the cache.
    """

    def __init__(self, fixture_dir: str | Path = "fixtures") -> None:
        self._dir = Path(fixture_dir)
        self._caches: dict[str, dict[str, dict[str, Any]]] = {
            "test": {},
            "class": {},
            "session": {},
        }

    def load(self, name: str, scope: FixtureScope = "test") -> dict[str, Any]:
        """Load a fixture by name. Returns a deep copy to isolate callers."""
        cache = self._caches[scope]
        if name in cache:
            return copy.deepcopy(cache[name])

        tried: list[str] = []
        for ext in (".json", ".yaml", ".yml"):
            path = self._dir / f"{name}{ext}"
            tried.append(str(path))
            if path.is_file():
                data = self._read_file(path)
                cache[name] = data
                return copy.deepcopy(data)

        raise FileNotFoundError(
            f"Fixture '{name}' not found. Tried: {', '.join(tried)}"
        )

    def load_cards(self, scope: FixtureScope = "test") -> list[dict[str, Any]]:
        data = self.load("cards", scope=scope)
        cards = data.get("cards", [])
        if not isinstance(cards, list):
            raise TypeError(f"Expected cards to be a list, got {type(cards)}")
        return cast(list[dict[str, Any]], cards)

    def load_relics(self, scope: FixtureScope = "test") -> list[dict[str, Any]]:
        data = self.load("relics", scope=scope)
        relics = data.get("relics", [])
        if not isinstance(relics, list):
            raise TypeError(f"Expected relics to be a list, got {type(relics)}")
        return cast(list[dict[str, Any]], relics)

    def load_seeds(self, scope: FixtureScope = "test") -> list[int]:
        data = self.load("seeds", scope=scope)
        seeds = data.get("seeds", [])
        if not isinstance(seeds, list):
            raise TypeError(f"Expected seeds to be a list, got {type(seeds)}")
        return [int(s) for s in seeds]

    def _read_file(self, path: Path) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as f:
                if path.suffix in (".yaml", ".yml"):
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ValueError(
                f"Failed to parse fixture file {path}: {exc}"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"Failed to read fixture file {path}: {exc}"
            ) from exc

        if data is None:
            raise ValueError(f"Fixture file {path} is empty or contains only whitespace")
        if isinstance(data, dict):
            return data
        raise ValueError(
            f"Fixture file {path} contains {type(data).__name__}, expected dict. "
            f"Top-level arrays are not supported — wrap in an object."
        )
