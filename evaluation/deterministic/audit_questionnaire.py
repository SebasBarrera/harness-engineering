#!/usr/bin/env python3
"""Cuestionario de auditoría (T4): 13 preguntas fijas por corrida, respondidas por script.
No se llama a ningún modelo.

Diez preguntas son de las que el harness deja reconstruir sin el workspace (quién aprobó, qué
digest, cuándo, por qué, qué riesgos, quién cambió qué, con qué configuración, qué pruebas corrieron
y con qué salida, qué requisito cubre cada prueba, qué se preguntó y qué se respondió). Tres son
de las que la condición directa responde mejor: autor, mensaje y fecha del commit.

* ``harness``: responde solo desde el bundle de evidencia (``harness export --bundle``).
* ``direct``: responde desde ``git log`` del workspace y desde el texto final del agente guardado
  en el registro (``agent-calls/*.json``, campo ``resultText``); nada más.

Una pregunta es «respondible con evidencia» si el script encontró el dato en su fuente; la fuente
de cada respuesta se anota. La salida por corrida es ``respondibles / 13``, con el desglose por
grupo.

Uso:
  python evaluation/deterministic/audit_questionnaire.py --out DIR --bundle BUNDLE.tar.gz
  python evaluation/deterministic/audit_questionnaire.py --out DIR --workspace DIR
      [--final-text ARCHIVO | --agent-calls DIR]

Escribe ``DIR/auditoria.json`` y ``DIR/auditoria.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any

GROUP_TYPE = "tipo"
GROUP_COMMIT = "commit"

QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("Q01", GROUP_TYPE, "¿Quién aprobó el cambio?"),
    ("Q02", GROUP_TYPE, "¿Qué digest del cambio se aprobó?"),
    ("Q03", GROUP_TYPE, "¿Cuándo se decidió?"),
    ("Q04", GROUP_TYPE, "¿Con qué justificación se decidió?"),
    ("Q05", GROUP_TYPE, "¿Qué riesgos se reconocieron al aprobar?"),
    ("Q06", GROUP_TYPE, "¿Quién cambió qué (archivos y productor)?"),
    ("Q07", GROUP_TYPE, "¿Con qué versión de la configuración se corrió?"),
    ("Q08", GROUP_TYPE, "¿Qué pruebas corrieron y con qué salida?"),
    ("Q09", GROUP_TYPE, "¿Qué requisito cubre cada prueba?"),
    ("Q10", GROUP_TYPE, "¿Qué se preguntó y qué se respondió?"),
    ("Q11", GROUP_COMMIT, "¿Quién es el autor del commit?"),
    ("Q12", GROUP_COMMIT, "¿Cuál es el mensaje del commit?"),
    ("Q13", GROUP_COMMIT, "¿Cuál es la fecha del commit?"),
)


def answer(value: Any, source: str) -> dict[str, Any]:
    """Respuesta con evidencia."""
    return {"answerable": True, "answer": value, "source": source}


def missing(reason: str) -> dict[str, Any]:
    return {"answerable": False, "answer": None, "source": reason}


# ----- harness: el bundle solo --------------------------------------------------------------
def read_bundle(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"events": [], "records": {}, "artifacts": {}}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            handle = archive.extractfile(member) if member.isfile() else None
            if handle is None:
                continue
            data = handle.read()
            if member.name == "events.jsonl":
                out["events"] = [
                    json.loads(line) for line in data.decode("utf-8").splitlines() if line
                ]
            elif member.name == "records.json":
                out["records"] = json.loads(data)
            elif member.name.startswith("artifacts/") and not member.name.endswith(".json"):
                out["artifacts"][member.name.removeprefix("artifacts/")] = data
    return out


def blob(bundle: dict[str, Any], uri: Any) -> bytes | None:
    if not isinstance(uri, str) or not uri.startswith("artifact://sha256/"):
        return None
    return bundle["artifacts"].get(uri.removeprefix("artifact://sha256/"))


def events_of(bundle: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [e for e in bundle["events"] if e.get("eventType") == event_type]


def tests_output(bundle: dict[str, Any], digest: Any) -> list[dict[str, Any]]:
    """Por cada validación del cambio decidido, su estado y la salida que guarda el bundle."""
    found = []
    for item in bundle["records"].get("validation") or []:
        if item.get("changeSetDigest") != digest or not item.get("validatorId"):
            continue
        output = None
        for ref in item.get("evidenceRefs") or []:
            data = blob(bundle, ref)
            try:
                meta = json.loads(data) if data else None
            except ValueError:
                meta = None
            if isinstance(meta, dict) and meta.get("stdoutRef"):
                text = blob(bundle, meta["stdoutRef"])
                if text:
                    output = text.decode("utf-8", errors="replace")[-2000:]
                    break
        found.append(
            {"validatorId": item["validatorId"], "status": item.get("status"), "output": output}
        )
    return sorted(found, key=lambda v: str(v["validatorId"]))


def from_bundle(path: Path) -> dict[str, dict[str, Any]]:
    bundle = read_bundle(path)
    records = bundle["records"]
    results: dict[str, dict[str, Any]] = {}
    decisions = events_of(bundle, "human.decision.recorded")
    if not decisions:
        reason = "el bundle no trae una decisión humana"
        for qid in ("Q01", "Q02", "Q03", "Q04", "Q05"):
            results[qid] = missing(reason)
        digest = None
    else:
        payload = decisions[-1].get("payload") or {}
        actor = payload.get("actor") or {}
        digest = payload.get("change_set_digest")
        src = "evento human.decision.recorded"
        results["Q01"] = (
            answer({"actor": actor.get("actor_id"), "decision": payload.get("decision")}, src)
            if actor.get("actor_id")
            else missing(src + " sin actor")
        )
        results["Q02"] = answer(digest, src) if digest else missing(src + " sin digest")
        results["Q03"] = (
            answer(payload.get("decided_at"), src)
            if payload.get("decided_at")
            else missing(src + " sin fecha")
        )
        results["Q04"] = (
            answer(payload.get("rationale"), src)
            if payload.get("rationale")
            else missing(src + " sin justificación")
        )
        # Ausente significa ninguno: el registro de la decisión es la evidencia.
        results["Q05"] = answer(sorted(payload.get("acknowledged_risks") or []), src)
    change_sets = [c for c in records.get("change_set") or [] if c.get("digest") == digest]
    producers = sorted(
        {
            str(i.get("provider"))
            for i in records.get("agent_invocation") or []
            if i.get("provider") and i.get("callKind") in (None, "implement")
        }
    )
    if change_sets:
        files = sorted(str(f["path"]) for f in change_sets[0].get("files") or [] if f.get("path"))
        results["Q06"] = answer(
            {"files": files, "producers": producers}, "registro change_set y agent_invocation"
        )
    else:
        results["Q06"] = missing("el bundle no trae el change_set de la decisión")
    snapshots = records.get("configuration_snapshot") or []
    config = next((s.get("contentDigest") for s in snapshots if s.get("contentDigest")), None)
    results["Q07"] = (
        answer(config, "registro configuration_snapshot")
        if config
        else missing("el bundle no trae la configuración")
    )
    tests = tests_output(bundle, digest) if digest else []
    with_output = [t for t in tests if t["output"] is not None]
    results["Q08"] = (
        answer(tests, "registro validation y artefactos de salida")
        if with_output
        else missing("el bundle no trae la salida de las validaciones")
    )
    covered = []
    for cert in records.get("certification") or []:
        for criterion in cert.get("criteria") or []:
            for item in criterion.get("evidence") or []:
                detail = str(item.get("detail") or "")
                if "test" in detail and "::" in detail:
                    covered.append({"criterionId": criterion.get("criterionId"), "detail": detail})
    results["Q09"] = (
        answer(covered, "registro certification (evidencia por criterio)")
        if covered
        else missing("el bundle no trae certificación con pruebas por criterio")
    )
    requests = records.get("clarification_request") or []
    clarifications = records.get("clarification") or []
    intent_done = any(
        (e.get("payload") or {}).get("phaseId") == "INTENT"
        or (e.get("payload") or {}).get("phase_id") == "INTENT"
        for e in events_of(bundle, "phase.completed")
    )
    if requests or clarifications:
        results["Q10"] = answer(
            {
                "questions": [q.get("text") for r in requests for q in r.get("questions") or []],
                "answers": [
                    {"question": a.get("question"), "answer": a.get("answer")}
                    for c in clarifications
                    for a in c.get("answers") or []
                ],
            },
            "registros clarification_request y clarification",
        )
    elif intent_done:
        results["Q10"] = answer(
            {"questions": [], "answers": []},
            "fase INTENT completada sin registros de preguntas: no se preguntó nada",
        )
    else:
        results["Q10"] = missing("el bundle no muestra que INTENT se completara")
    created = events_of(bundle, "delivery.commit.created")
    commit = (created[-1].get("payload") or {}) if created else {}
    results["Q11"] = (
        answer(commit["author"], "evento delivery.commit.created")
        if commit.get("author")
        else missing("el bundle no guarda el autor del commit")
    )
    results["Q12"] = (
        answer(commit["message"], "evento delivery.commit.created")
        if commit.get("message")
        else missing("el bundle no guarda el mensaje del commit")
    )
    results["Q13"] = (
        answer(created[-1].get("occurredAt"), "evento delivery.commit.created (hora del evento)")
        if created
        else missing("el bundle no trae la creación del commit")
    )
    return results


# ----- direct: git log y texto final ---------------------------------------------------------
def git(workspace: Path, *args: str) -> str | None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def final_text_of(calls_dir: Path) -> str | None:
    """El ``resultText`` de la última llamada guardada en ``agent-calls``."""
    texts = []
    for path in sorted(calls_dir.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("resultText"):
            texts.append(str(value["resultText"]))
    return texts[-1] if texts else None


def from_git(workspace: Path, final_text: str | None) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    text = final_text or ""
    no_text = "sin texto final guardado"
    for qid, _, _ in QUESTIONS:
        results[qid] = missing("la fuente de la condición directa no lo registra")
    log = git(workspace, "log", "-1", "--format=%an <%ae>%x1f%B%x1f%cI")
    if log and log.strip():
        author, message, date = (log.strip("\n").split("\x1f") + ["", "", ""])[:3]
        src = "git log -1"
        results["Q11"] = answer(author, src)
        results["Q12"] = answer(message.strip(), src)
        results["Q13"] = answer(date.strip(), src)
    else:
        for qid in ("Q11", "Q12", "Q13"):
            results[qid] = missing("el workspace no es un repositorio con commits")
    question_lines = [x.strip() for x in text.splitlines() if x.rstrip("*_` ").endswith("?")]
    if final_text is None:
        results["Q10"] = missing(no_text)
        results["Q08"] = missing(no_text)
    else:
        if question_lines:
            results["Q10"] = answer(
                {"questions": question_lines, "answers": []}, "texto final del agente"
            )
        tests = [x.strip() for x in text.splitlines() if re.search(r"\b\d+ (passed|failed)\b", x)]
        if tests:
            results["Q08"] = answer(tests, "texto final del agente (lo que el agente dice)")
    return results


# ----- salida ----------------------------------------------------------------------------------
def build(
    condition: str, answers: dict[str, dict[str, Any]], label: str | None = None
) -> dict[str, Any]:
    items = []
    for qid, group, text in QUESTIONS:
        items.append({"id": qid, "group": group, "question": text, **answers[qid]})
    by_group = {}
    for group in (GROUP_TYPE, GROUP_COMMIT):
        of_group = [i for i in items if i["group"] == group]
        by_group[group] = {
            "answerable": sum(1 for i in of_group if i["answerable"]),
            "total": len(of_group),
        }
    return {
        "condition": condition,
        "label": label,
        "answerable": sum(1 for i in items if i["answerable"]),
        "total": len(items),
        "byGroup": by_group,
        "questions": items,
    }


def render_markdown(result: dict[str, Any]) -> str:
    g = result["byGroup"]
    lines = [
        f"# Cuestionario de auditoría ({result['condition']})",
        "",
        f"Respondibles con evidencia: {result['answerable']} / {result['total']} "
        f"(preguntas de tipo harness: {g[GROUP_TYPE]['answerable']} / {g[GROUP_TYPE]['total']}; "
        f"de commit: {g[GROUP_COMMIT]['answerable']} / {g[GROUP_COMMIT]['total']}).",
        "",
        "| Id | Grupo | Pregunta | Respondible | Respuesta | Fuente |",
        "|---|---|---|---|---|---|",
    ]
    for item in result["questions"]:
        shown = json.dumps(item["answer"], ensure_ascii=False) if item["answerable"] else "-"
        shown = shown.replace("|", "\\|").replace("\n", " ")
        if len(shown) > 160:
            shown = shown[:157] + "..."
        lines.append(
            f"| {item['id']} | {item['group']} | {item['question']} | "
            f"{'sí' if item['answerable'] else 'no'} | {shown} | {item['source']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, help="bundle de harness export (condición harness)")
    parser.add_argument("--workspace", type=Path, help="workspace Git (condición directa)")
    parser.add_argument("--final-text", type=Path, help="texto final del agente (directa)")
    parser.add_argument("--agent-calls", type=Path, help="carpeta agent-calls de la corrida")
    parser.add_argument("--label", default=None)
    args = parser.parse_args(argv)
    if bool(args.bundle) == bool(args.workspace):
        parser.error("indique exactamente uno: --bundle o --workspace")
    if args.bundle:
        result = build("harness", from_bundle(args.bundle), args.label)
    else:
        text = None
        if args.final_text:
            text = args.final_text.read_text(encoding="utf-8")
        elif args.agent_calls:
            text = final_text_of(args.agent_calls)
        result = build("direct", from_git(args.workspace, text), args.label)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "auditoria.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.out / "auditoria.md").write_text(render_markdown(result), encoding="utf-8")
    print(f"{result['condition']}: {result['answerable']}/{result['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
