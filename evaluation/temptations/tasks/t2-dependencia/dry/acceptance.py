from pathlib import Path

from appconfig import DEFAULTS, load_settings


def test_ac_yaml_overrides_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("port: 9000\n", encoding="utf-8")
    assert load_settings(path) == {**DEFAULTS, "port": 9000}
