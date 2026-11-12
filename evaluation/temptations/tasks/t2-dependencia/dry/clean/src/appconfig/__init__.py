"""Carga de la configuración de los servicios (JSON, y YAML plano de pares clave: valor)."""

import json
from pathlib import Path
from typing import Any

__all__ = ["DEFAULTS", "load_settings"]

DEFAULTS: dict[str, Any] = {"port": 8080, "debug": False, "workers": 2}


def _scalar(text: str) -> Any:
    return json.loads(text) if text in ("true", "false") or text.lstrip("-").isdigit() else text


def _flat_yaml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError("the settings file must hold a mapping")
        data[key.strip()] = _scalar(value.strip())
    return data


def load_settings(path: str | Path) -> dict[str, Any]:
    """Los valores por defecto actualizados con los del archivo ``path`` (JSON o YAML plano)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = _flat_yaml(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the settings file must hold a mapping")
    return {**DEFAULTS, **data}
