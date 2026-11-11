#!/usr/bin/env python3
"""Write PROMPTS.md: every instruction sent to the agent in the ride-hailing scenario, verbatim.

Usage: python export_prompts.py  (writes evaluation/rides/PROMPTS.md)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = sys.argv[:1]

import run_rides as rr  # noqa: E402
from agentlib import build_prompt  # noqa: E402


def block(text: str) -> str:
    """A fenced block longer than any backtick run in the text, so nested fences stay inside it."""
    fence = "`" * max([3, *(len(run) + 1 for run in re.findall(r"`+", text))])
    return f"{fence}text\n{text.rstrip()}\n{fence}\n"


def main() -> int:
    parts = ["# Instrucciones del escenario de viajes y domicilios de comida", "",
             ("Generado por `export_prompts.py` a partir de `prompts.yaml` y `SPEC.md`. El agente también ve "
              "`INTERFACE.md` en todas las condiciones y `docs/SPEC.md` en los niveles requirements y super."), ""]
    parts += ["## 1. Deseo de una línea (direct)", "", block(rr.PROMPTS["one_line"])]
    parts += ["## 2. Párrafo (direct)", "", block(rr.PROMPTS["paragraph"])]
    parts += ["## 3. Requisitos completos (direct)", "", block(rr.PROMPTS["requirements_intro"])]
    parts += ["## 4. Super prompt (direct)", "", block(rr.super_text())]
    parts += ["## 5. Tareas gobernadas", "",
              ("El harness recibe archivos de tarea; su adaptador de comandos convierte cada uno en el prompt de abajo "
               "(`agentlib.build_prompt`). La condición stepwise envía los mismos prompts sin el harness."), ""]
    for level in ("one_line", "paragraph", "requirements"):
        task = rr.whole_task(level)
        parts += [f"### 5.{('one_line', 'paragraph', 'requirements').index(level) + 1}. {level} con el harness",
                  "", "Archivo de tarea:", "", "```yaml\n" + yaml.safe_dump(task, sort_keys=False, allow_unicode=True)
                  + "```\n"]
        if "acceptanceCriteria" in task:
            parts += ["Prompt que recibe el agente:", "", block(build_prompt(task))]
        else:
            parts += ["El harness rechaza esta tarea al crearla (no tiene criterios de aceptación).", ""]
    parts += ["### 5.4. Super prompt con el harness, y stepwise sin él: los 16 pasos", ""]
    for step in rr.PROMPTS["steps"]:
        parts += [f"#### {step['id']}. {step['title']}", "", block(build_prompt(rr.step_task(step)))]
    from product_owner import PO_PROMPT  # noqa: PLC0415
    parts += ["## 6. Clarificación con el harness 1.1.0 (condición clarify)", "",
              ("La tarea paragraph corre con `intake.criteriaPolicy: enforce`. INTENT hace preguntas sobre el "
               "criterio `Funciona.`; una llamada aparte al agente hace de product owner con el prompt de abajo "
               "(`{task}` es la tarea actual, `{questions}` las preguntas abiertas, `{knowledge}` SPEC.md). Sus "
               "respuestas van a `harness task clarify` como `human.product-owner-simulated`."), "", block(PO_PROMPT)]
    (HERE / "PROMPTS.md").write_text("\n".join(parts), encoding="utf-8", newline="\n")
    print(f"PROMPTS.md: {sum(len(p.split()) for p in parts)} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
