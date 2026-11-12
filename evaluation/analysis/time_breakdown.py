"""Desglose del tiempo propio del harness por corrida del piloto.

Lee un ``pilot.jsonl`` (un registro por corrida) y, por cada corrida con ``harness.commands``, resta el
tiempo de agente a la duración de cada comando y agrupa el resto por tramo:

* ``short``: comandos que no son ``run`` (init, status, inbox, task, plan, gate, ...);
* ``intent_planning``: ``run start``/``run continue`` anteriores a ``plan decide``;
* ``implementation``: ``run continue`` entre ``plan decide`` y ``gate decide`` (IMPLEMENTATION,
  VERIFICATION, REVIEW); sin ``plan decide`` (harness-core) cuentan aquí todos los ``run`` previos a la
  compuerta;
* ``closure``: ``run continue`` posteriores a ``gate decide``.

El tiempo de agente se asigna por la clase de la llamada: ``implement``, ``review`` y ``correct`` van al
tramo de implementación y el resto al de intención y planificación. Si se da ``--runs-dir``, también lista
la duración de cada validador (hueco entre los eventos ``validation.completed`` de la base de
estado), agrupada por pasada de verificación.

Uso: ``python time_breakdown.py PILOT.jsonl [--runs-dir DIR] [--json OUT.json] [--md OUT.md]``
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

IMPLEMENTATION_KINDS = {"implement", "review", "correct"}
TRAMOS = ("short", "intent_planning", "implementation", "closure")
TRAMO_LABELS = {
    "short": "comandos cortos",
    "intent_planning": "INTENT a PLANNING",
    "implementation": "IMPLEMENTATION/VERIFICATION/REVIEW",
    "closure": "cierre",
}


def _is_run(command: list[str]) -> bool:
    return bool(command) and command[0] == "run"


def breakdown(record: dict[str, Any]) -> dict[str, Any] | None:
    """Tiempo propio del harness de una corrida, o ``None`` si no hay comandos."""
    harness = record.get("harness") or {}
    commands = harness.get("commands") or []
    if not commands:
        return None
    agent = dict.fromkeys(TRAMOS, 0.0)
    for call in record.get("agentCalls") or []:
        tramo = "implementation" if call.get("kind") in IMPLEMENTATION_KINDS else "intent_planning"
        agent[tramo] += float(call.get("wallSeconds") or 0.0)
    names = [" ".join(c["command"][:2]) for c in commands]
    has_plan = "plan decide" in names
    if not has_plan:  # sin pasos humanos intermedios todo el trabajo está en un solo run
        agent["implementation"] += agent["intent_planning"]
        agent["intent_planning"] = 0.0
    raw = dict.fromkeys(TRAMOS, 0.0)
    count = dict.fromkeys(TRAMOS, 0)
    planned = gated = False
    for c in commands:
        name = " ".join(c["command"][:2])
        if not _is_run(c["command"]):
            tramo = "short"
            planned = planned or name == "plan decide"
            gated = gated or name == "gate decide"
        elif gated:
            tramo = "closure"
        elif planned or not has_plan:
            tramo = "implementation"
        else:
            tramo = "intent_planning"
        raw[tramo] += float(c["seconds"])
        count[tramo] += 1
    own = {t: round(raw[t] - agent[t], 3) for t in TRAMOS}
    shorts = [c["seconds"] for c in commands if not _is_run(c["command"])]
    return {
        "condition": record.get("condition"),
        "scenario": record.get("scenario"),
        "rep": record.get("rep"),
        "startedAt": record.get("startedAt"),
        "wallSeconds": record.get("wallSeconds"),
        "commandSeconds": {t: round(raw[t], 3) for t in TRAMOS},
        "agentSeconds": {t: round(agent[t], 3) for t in TRAMOS},
        "ownSeconds": own,
        "commands": count,
        "ownTotalSeconds": round(sum(own.values()), 3),
        "shortCommandMeanSeconds": round(sum(shorts) / len(shorts), 3) if shorts else None,
        "perCommand": [
            {
                "command": " ".join(c["command"]),
                "seconds": c["seconds"],
                "exitCode": c.get("exitCode"),
            }
            for c in commands
        ],
        "validators": [],
    }


def validator_passes(db: Path) -> list[dict[str, Any]]:
    """Duración por validador en cada fase VERIFICATION, a partir de los eventos de la base de estado.

    Los ``startedAt``/``finishedAt`` de los registros ``validation`` no sirven (el de ``harness.mutation``
    marca 0,0 s cuando el hueco entre eventos es de 22 s), así que cada validador se mide como el tiempo
    transcurrido desde el evento anterior de la fase (``phase.started``, ``validation.completed`` o
    ``verification.baseline.completed``); la suma de una pasada es la de la fase menos el cierre.
    """
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        events = con.execute(
            "select event_type, occurred_at, payload_json from events order by global_sequence"
        ).fetchall()
    finally:
        con.close()
    passes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last: datetime | None = None
    pending: datetime | None = None
    for event_type, occurred_at, payload_json in events:
        at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        payload = json.loads(payload_json) if payload_json else {}
        if event_type == "phase.started":
            current, pending = None, at
            continue
        if event_type == "capabilities.resolved":  # nombra la fase recién iniciada
            current = None
            if str(payload.get("phase")) == "VERIFICATION" and pending is not None:
                current = {"phase": "VERIFICATION", "validators": [], "seconds": 0.0}
                passes.append(current)
                last = pending
            continue
        if current is None or last is None:
            continue
        if event_type == "validation.completed":
            name = str(payload.get("validator_id") or "?")
            status = payload.get("status")
        elif event_type == "verification.baseline.completed":
            name, status = "baseline", None
        elif event_type == "phase.completed":
            current = None
            continue
        else:
            continue
        secs = round((at - last).total_seconds(), 3)
        last = at
        current["validators"].append({"validatorId": name, "status": status, "seconds": secs})
        current["seconds"] = round(current["seconds"] + secs, 3)
    return passes


def find_state_db(runs_dir: Path, started_at: str | None) -> Path | None:
    """Base de estado del directorio de corrida cuya marca ``YYYYMMDDTHHMMSSZ`` está más cerca del inicio.

    ``harness.runId`` no identifica la corrida en el registro, así que se empareja por el nombre del
    directorio, que termina con la hora de inicio; se acepta a lo sumo un minuto de diferencia.
    """
    if not started_at:
        return None
    target = datetime.fromisoformat(started_at.replace("Z", "+00:00")).replace(tzinfo=None)
    best: tuple[float, Path] | None = None
    for db in runs_dir.glob("*/state/*/state.db"):
        match = re.search(r"-(\d{8}T\d{6})Z$", db.parents[2].name)
        if not match:
            continue
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        gap = abs((stamp - target).total_seconds())
        if gap <= 60 and (best is None or gap < best[0]):
            best = (gap, db)
    return best[1] if best else None


def analyze(jsonl: Path, runs_dir: Path | None = None) -> list[dict[str, Any]]:
    out = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        result = breakdown(json.loads(line))
        if result is None:
            continue
        if runs_dir is not None:
            db = find_state_db(runs_dir, result["startedAt"])
            if db is not None:
                result["validators"] = validator_passes(db)
        out.append(result)
    return out


INTRO = (
    "Tiempo propio = duración del comando menos el tiempo de las llamadas al agente de su tramo. "
    "Las cifras salen de `harness.commands` y `agentCalls` del registro de cada corrida."
)


def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Desglose del tiempo propio del harness",
        "",
        INTRO,
        "",
        "| Corrida | "
        + " | ".join(f"{TRAMO_LABELS[t]} (s)" for t in TRAMOS)
        + " | Total propio (s) |",
        "|---|" + "---:|" * (len(TRAMOS) + 1),
    ]
    for r in results:
        name = f"{r['scenario']} / {r['condition']} #{r['rep']} ({str(r['startedAt'])[11:19]})"
        cells = " | ".join(f"{r['ownSeconds'][t]:.1f}" for t in TRAMOS)
        lines.append(f"| {name} | {cells} | {r['ownTotalSeconds']:.1f} |")
    lines += ["", "El tramo de comandos cortos incluye el arranque de la CLI en cada invocación."]
    for r in results:
        if not r["validators"]:
            continue
        lines += [
            "",
            f"## Validadores de {r['scenario']} / {r['condition']} #{r['rep']} ({str(r['startedAt'])[11:19]})",
            "",
        ]
        for i, p in enumerate(r["validators"], 1):
            detail = ", ".join(f"{v['validatorId']} {v['seconds']:.1f} s" for v in p["validators"])
            lines.append(f"- Pasada {i} ({p['seconds']:.1f} s): {detail}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Desglose del tiempo propio del harness.")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--json", type=Path, dest="json_out", default=None)
    parser.add_argument("--md", type=Path, dest="md_out", default=None)
    args = parser.parse_args(argv)
    results = analyze(args.jsonl, args.runs_dir)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    md = render_markdown(results)
    if args.md_out:
        args.md_out.write_text(md, encoding="utf-8")
    if not (args.json_out or args.md_out):
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
