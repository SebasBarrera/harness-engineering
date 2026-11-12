#!/usr/bin/env python3
"""Repetibilidad (T5): cuánto varía una misma celda entre repeticiones. Sin modelo, sin red.

Lee los ``.jsonl`` de un bloque (los registros de ``run_eval.py``) y, por celda
(escenario x prompt x condición x modelo), da el mínimo, el máximo y el rango de cuatro medidas:
pruebas ocultas pasadas, archivos cambiados, archivos fuera de alcance y líneas cambiadas
(añadidas + eliminadas). Las corridas rechazadas (resultado del flujo ``rejected*``, o rechazo de
la tarea) o inválidas (campo ``invalid``, o archivo ``*.invalid.jsonl``) no entran en el rango:
se cuentan aparte por celda.

    python evaluation/analysis/repeatability.py --in BLOQUE/ [--in otro.jsonl] --out DIR

Escribe ``DIR/repetibilidad.json`` y ``DIR/repetibilidad.md``.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

METRICS = (
    ("hiddenPassed", "Pruebas ocultas pasadas"),
    ("files", "Archivos cambiados"),
    ("outOfScopeFiles", "Archivos fuera de alcance"),
    ("changedLines", "Líneas cambiadas"),
)
CELL_KEYS = ("scenario", "prompt", "condition", "model")


def read_records(paths: list[Path]) -> list[dict[str, Any]]:
    """Registros de archivos ``.jsonl`` o de carpetas (todos sus ``*.jsonl``)."""
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.rglob("*.jsonl")) if path.is_dir() else [path])
    records: list[dict[str, Any]] = []
    for file in files:
        invalid_file = file.name.endswith(".invalid.jsonl")
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                if invalid_file and not value.get("invalid"):
                    value["invalid"] = "archivo .invalid.jsonl"
                records.append(value)
    return records


def exclusion(record: dict[str, Any]) -> str | None:
    """``invalida``, ``rechazada`` o ``None`` si la corrida entra al rango."""
    if record.get("invalid"):
        return "invalida"
    outcome = str((record.get("harness") or {}).get("outcome") or "")
    if outcome.startswith("rejected") or outcome.startswith("task-refused"):
        return "rechazada"
    return None


def values_of(record: dict[str, Any]) -> dict[str, int | None]:
    measures = record.get("measures") or {}
    change = measures.get("change") or {}
    hidden = measures.get("hidden") or {}
    lines = None
    if "addedLines" in change or "removedLines" in change:
        lines = int(change.get("addedLines") or 0) + int(change.get("removedLines") or 0)
    return {
        "hiddenPassed": hidden.get("passed"),
        "files": change.get("files"),
        "outOfScopeFiles": change.get("outOfScopeFiles"),
        "changedLines": lines,
    }


def spread(values: list[int]) -> dict[str, int] | None:
    if not values:
        return None
    return {"min": min(values), "max": max(values), "range": max(values) - min(values)}


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(str(record.get(k)) for k in CELL_KEYS)].append(record)
    cells = []
    for key in sorted(groups):
        runs = groups[key]
        counted = [r for r in runs if exclusion(r) is None]
        per_metric: dict[str, list[int]] = {name: [] for name, _ in METRICS}
        for record in counted:
            for name, value in values_of(record).items():
                if isinstance(value, int):
                    per_metric[name].append(value)
        cells.append(
            {
                **dict(zip(CELL_KEYS, key, strict=True)),
                "runs": len(runs),
                "counted": len(counted),
                "rejected": sum(1 for r in runs if exclusion(r) == "rechazada"),
                "invalid": sum(1 for r in runs if exclusion(r) == "invalida"),
                "metrics": {name: spread(vals) for name, vals in per_metric.items()},
            }
        )
    return {
        "records": len(records),
        "cells": cells,
        "excluded": {
            "rejected": sum(c["rejected"] for c in cells),
            "invalid": sum(c["invalid"] for c in cells),
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    head = (
        "| Escenario | Prompt | Condición | Modelo | Corridas | Usadas | Rechazadas | Inválidas | "
        + " | ".join(label for _, label in METRICS)
        + " |"
    )
    lines = [
        "# Repetibilidad por celda",
        "",
        "Mínimo / máximo (rango) entre las repeticiones de cada celda "
        "(escenario x prompt x condición x modelo). Las corridas rechazadas o inválidas no "
        "entran al rango y se cuentan aparte.",
        "",
        f"Registros leídos: {result['records']}. Excluidas: {result['excluded']['rejected']} "
        f"rechazadas, {result['excluded']['invalid']} inválidas.",
        "",
        head,
        "|" + "---|" * (8 + len(METRICS)),
    ]
    for cell in result["cells"]:
        shown = []
        for name, _ in METRICS:
            s = cell["metrics"][name]
            shown.append("sin datos" if s is None else f"{s['min']} / {s['max']} ({s['range']})")
        lines.append(
            f"| {cell['scenario']} | {cell['prompt']} | {cell['condition']} | {cell['model']} | "
            f"{cell['runs']} | {cell['counted']} | {cell['rejected']} | {cell['invalid']} | "
            + " | ".join(shown)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--in", dest="inputs", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = analyze(read_records(args.inputs))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "repetibilidad.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.out / "repetibilidad.md").write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
