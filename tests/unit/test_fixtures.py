"""Tests for dsl/fixtures.py — FixtureLoader."""

import json
from pathlib import Path

import pytest

from sts2_autotest.dsl.fixtures import FixtureLoader


class TestFixtureLoader:
    """Fixture file loading and caching."""

    def test_load_json(self, tmp_path: Path) -> None:
        cards = {"cards": [{"id": "Strike", "cost": 1}, {"id": "Defend", "cost": 1}]}
        (tmp_path / "cards.json").write_text(json.dumps(cards))
        loader = FixtureLoader(str(tmp_path))
        data = loader.load("cards")
        assert data == cards

    def test_load_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "relics.yaml").write_text("relics:\n  - id: BurningBlood\n    tier: boss\n")
        loader = FixtureLoader(str(tmp_path))
        data = loader.load("relics")
        assert data["relics"][0]["id"] == "BurningBlood"

    def test_json_preferred_over_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"from": "json"}')
        (tmp_path / "data.yaml").write_text("from: yaml\n")
        loader = FixtureLoader(str(tmp_path))
        assert loader.load("data") == {"from": "json"}

    def test_cached_on_second_load(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"value": 1}')
        loader = FixtureLoader(str(tmp_path))
        loader.load("data")
        # Modify file after load — cache should return old value
        (tmp_path / "data.json").write_text('{"value": 2}')
        assert loader.load("data") == {"value": 1}

    def test_missing_fixture_raises(self, tmp_path: Path) -> None:
        loader = FixtureLoader(str(tmp_path))
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            loader.load("nonexistent")

    def test_load_cards(self, tmp_path: Path) -> None:
        (tmp_path / "cards.json").write_text(
            '{"cards": [{"id": "Strike"}, {"id": "Defend"}]}'
        )
        loader = FixtureLoader(str(tmp_path))
        cards = loader.load_cards()
        assert len(cards) == 2

    def test_load_seeds(self, tmp_path: Path) -> None:
        (tmp_path / "seeds.json").write_text('{"seeds": [42, 1337, 9001]}')
        loader = FixtureLoader(str(tmp_path))
        seeds = loader.load_seeds()
        assert seeds == [42, 1337, 9001]

    def test_non_dict_data_raises(self, tmp_path: Path) -> None:
        (tmp_path / "list.json").write_text('[1, 2, 3]')
        loader = FixtureLoader(str(tmp_path))
        with pytest.raises(ValueError, match="expected dict"):
            loader.load("list")

    def test_empty_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "empty.yaml").write_text("")
        loader = FixtureLoader(str(tmp_path))
        with pytest.raises(ValueError, match="empty or contains only whitespace"):
            loader.load("empty")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("{not valid json")
        loader = FixtureLoader(str(tmp_path))
        with pytest.raises(ValueError, match="Failed to parse"):
            loader.load("bad")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(":\n  - : invalid\n  - [unclosed")
        loader = FixtureLoader(str(tmp_path))
        with pytest.raises(ValueError, match="Failed to parse"):
            loader.load("bad")

    def test_deep_copy_isolation(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"items": [1, 2, 3]}')
        loader = FixtureLoader(str(tmp_path))
        d1 = loader.load("data")
        d2 = loader.load("data")
        d1["items"].append(4)
        assert d2["items"] == [1, 2, 3]

    def test_scope_isolation(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"val": 1}')
        loader = FixtureLoader(str(tmp_path))
        d_test = loader.load("data", scope="test")
        d_session = loader.load("data", scope="session")
        d_test["val"] = 999
        assert d_session["val"] == 1
