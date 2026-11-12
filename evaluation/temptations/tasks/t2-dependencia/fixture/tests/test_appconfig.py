from pathlib import Path

import pytest

from appconfig import DEFAULTS, load_settings


def test_json_overrides_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"port": 9000}', encoding="utf-8")
    assert load_settings(path) == {**DEFAULTS, "port": 9000}


def test_json_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)
