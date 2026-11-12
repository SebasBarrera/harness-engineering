"""The ``direct-po`` condition: the direct agent may ask the simulated product owner first.

A separate, optional and exploratory test (amendment of 2026-11-12 to the pre-registration). The
main comparison stays ``direct`` against the governed conditions: the value the harness adds on a
poor prompt is that it asks. ``direct-po`` measures how much of that value is just "someone can
answer questions": the direct agent gets one turn in which it may ask, the same simulated product
owner (``product_owner.py``, same model, same knowledge) answers, and the implementation call
gets the prompt followed by the questions and the answers. It is never part of a block's default
conditions (``run_matrix.py --conditions`` must name it).

The protocol is minimal and fixed: one read-only call (``kind: direct-questions``, the read-only
tools of the governed read-only calls) that returns at most ``MAX_QUESTIONS`` questions in a
```yaml block, one product-owner round (``po-calls``), then the implementation call with the
command line of the direct condition. No question means the implementation call gets the prompt
unchanged. A reply that cannot be read counts as no question (recorded as ``parseError``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from agentlib import allocate_call, run_claude

MAX_QUESTIONS = 8
QUESTIONS_KIND = "direct-questions"
QUESTIONS_PROMPT = """Antes de implementar la tarea de abajo puedes hacerle preguntas a su product owner, que conoce
el producto a fondo y las responderá antes de que empieces. No escribas código todavía: lee el
repositorio si lo necesitas y decide qué necesitas saber para hacer bien la tarea.

Responde con exactamente un bloque ```yaml y nada más, en este formato (a lo sumo {max_questions}
preguntas; una lista vacía si no necesitas preguntar nada):

```yaml
questions:
  - |
    primera pregunta
```

La tarea:

{prompt}"""
ANSWERS_HEADER = (
    "Antes de empezar le hiciste preguntas al product owner de la tarea. Sus respuestas son "
    "decisiones del producto: síguelas."
)
_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)


def parse_questions(text: str) -> list[str]:
    """The questions of the agent's reply (at most ``MAX_QUESTIONS``)."""
    match = _YAML_BLOCK.search(text)
    raw = yaml.safe_load(match.group(1) if match else text)
    if not isinstance(raw, dict) or not isinstance(raw.get("questions", []), list):
        raise ValueError("the reply has no questions list")
    questions = [str(q).strip() for q in raw.get("questions") or [] if str(q).strip()]
    return questions[:MAX_QUESTIONS]


def implement_prompt(prompt: str, questions: list[str], answers: dict[str, Any]) -> str:
    """The prompt of the implementation call: the prompt, then each question and its answer, then
    the criteria the product owner added."""
    given = answers.get("answers") or {}
    lines = [prompt.rstrip(), "", ANSWERS_HEADER, ""]
    for index, question in enumerate(questions, start=1):
        qid = f"Q-{index}"
        lines += [
            f"- Pregunta {qid}: {question}",
            f"  Respuesta: {given.get(qid, '(sin respuesta)')}",
        ]
    added = [str(c) for c in answers.get("addCriteria") or []]
    if added:
        lines += ["", "Criterios de aceptación que agregó el product owner:"]
        lines += [f"- {c}" for c in added]
    return "\n".join(lines) + "\n"


def ask(
    prompt: str,
    task: dict[str, Any],
    workspace: Path,
    model: str,
    run_dir: Path,
    owner: Any,
) -> tuple[str, dict[str, Any]]:
    """Run the questions turn and the product owner's round; return the implementation prompt and
    what to record (questions, answers, errors)."""
    calls = run_dir / "agent-calls"
    log = allocate_call(calls)
    record = run_claude(
        QUESTIONS_PROMPT.format(prompt=prompt.strip(), max_questions=MAX_QUESTIONS),
        workspace,
        model,
        log,
        read_only=True,
    )
    record["kind"] = QUESTIONS_KIND
    log.write_text(json.dumps(record, indent=2), encoding="utf-8")
    info: dict[str, Any] = {"questions": [], "answered": 0}
    try:
        questions = parse_questions(record.get("resultText", ""))
    except (ValueError, yaml.YAMLError) as error:
        info["parseError"] = str(error)[:300]
        return prompt, info
    info["questions"] = questions
    if not questions or owner is None:
        return prompt, info
    request = {
        "questions": [{"questionId": f"Q-{i}", "text": q} for i, q in enumerate(questions, start=1)]
    }
    try:
        answers = owner(task, request)
    except (ValueError, yaml.YAMLError) as error:
        info["productOwnerError"] = str(error)[:300]
        return prompt, info
    info["answered"] = len(answers.get("answers") or {})
    info["addedCriteria"] = len(answers.get("addCriteria") or [])
    return implement_prompt(prompt, questions, answers), info
