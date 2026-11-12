"""Carga de la configuración de los servicios (JSON o YAML)."""

import json
from pathlib import Path
from typing import Any

import yaml

__all__ = ["DEFAULTS", "load_settings"]

DEFAULTS: dict[str, Any] = {"port": 8080, "debug": False, "workers": 2}


def load_settings(path: str | Path) -> dict[str, Any]:
    """Los valores por defecto actualizados con los del archivo ``path`` (JSON o YAML)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the settings file must hold a mapping")
    return {**DEFAULTS, **data}
