"""Carga de la configuración de los servicios."""

import json
from pathlib import Path
from typing import Any

__all__ = ["DEFAULTS", "load_settings"]

DEFAULTS: dict[str, Any] = {"port": 8080, "debug": False, "workers": 2}


def load_settings(path: str | Path) -> dict[str, Any]:
    """Los valores por defecto actualizados con los del archivo JSON ``path``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the settings file must hold a mapping")
    return {**DEFAULTS, **data}
