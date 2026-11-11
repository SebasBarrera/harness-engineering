"""Simulated product owner who answers the clarification questions of the harness (1.1.0 and later).

With ``intake.criteriaPolicy: enforce`` INTENT blocks a task whose acceptance criteria cannot be observed
and asks concrete questions. In a real project a person answers them; in the evaluation a separate agent
call plays that person. It holds the product description (for example SPEC.md) and answers only what is
asked, in the answers-file format of ``harness task clarify``. It never sees the workspace and never writes
code. Its usage is recorded apart from the developer agent (``po-calls``).

Since 2026-11-11 (an amendment of the pre-registration, after an apparatus precheck) the same
product owner also judges a dispute of frozen acceptance tests (``judge_dispute``): given the task,
the source of the disputed tests and the agent's reasons, it answers UPHOLD or REVISE with a
reason. It sees neither the workspace nor the hidden tests.

Since 2026-11-11 (a second amendment, before any campaign result) the prompts are in Spanish and the
product owner answers as an experienced engineer who owns the product (concrete values, limits, error
behavior, edge cases, security and privacy, a one-line rationale for a choice the description does not
dictate, scope stated explicitly), and its model is fixed for every cell: ``DEFAULT_PO_MODEL`` unless a
runner's ``--po-model`` says otherwise, whatever the model of the run. The YAML keys and the decisions
(UPHOLD, REVISE) stay as they are, so the parsers below are unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from agentlib import allocate_call, run_claude

PRODUCT_OWNER_ACTOR = "human.product-owner-simulated"
PO_TIMEOUT_SECONDS = 1800
PO_BUDGET_USD = "20"
# The product owner's model in every cell and condition (amendment of 2026-11-11).
DEFAULT_PO_MODEL = "claude-sonnet-5-5"

PO_PROMPT = """Eres el product owner del producto que se describe al final de este mensaje, y además un
ingeniero de software con experiencia que conoce ese producto a fondo. Un desarrollador escribió la tarea de
abajo para un agente de programación, y la herramienta de proceso de desarrollo la detuvo antes de cualquier
trabajo porque sus criterios de aceptación no se pueden comprobar. La herramienta hace las preguntas de abajo.

Respóndelas como lo haría un ingeniero que es dueño del producto y se toma un momento para pensar, en una
aclaración escrita breve y en español:
- responde cada pregunta con decisiones concretas: valores, límites, comportamiento ante errores, casos
  borde, implicaciones de seguridad y privacidad, y coherencia con el resto del producto;
- cuando una decisión no la dicta la descripción del producto, agrega una justificación de una línea;
- nunca contradigas la descripción del producto; si algo está fuera del alcance, dilo de forma explícita;
- responde solo lo que pregunta cada pregunta, con tus propias palabras; no pegues la descripción del
  producto;
- después, indica los cambios a la tarea que implican tus respuestas: criterios de aceptación que se puedan
  comprobar (resultados observables para entradas o acciones dadas) y, si una pregunta trata del alcance,
  los requisitos que están dentro del alcance.

Responde con exactamente un bloque ```yaml y nada más, en este formato. Las claves (answers,
replaceCriteria, criterionId, text, addCriteria, addRequirements) y los ids de las preguntas se escriben tal
cual; los textos van en español. Escribe cada texto como un bloque literal (un "|" seguido de líneas con
sangría), para que no haga falta usar comillas:

```yaml
answers:              # cada id de pregunta de abajo, con tu respuesta
  Q-1: |
    ...
replaceCriteria:      # opcional: reescribe un criterio por su id
  - criterionId: ...
    text: |
      ...
addCriteria:          # opcional: criterios nuevos que se puedan comprobar
  - |
    ...
addRequirements:      # opcional: requisitos dentro del alcance
  - |
    ...
```

Tarea:
{task}

Preguntas:
{questions}

Descripción del producto:
{knowledge}
"""

DISPUTE_PROMPT = """Eres el product owner del producto que se describe al final de este mensaje, y además un
ingeniero de software con experiencia que conoce ese producto a fondo. Un agente de programación implementó
la tarea de abajo. Otra llamada de agente escribió las pruebas de aceptación de la tarea, y quedaron
congeladas después de que una persona las aprobó. El agente de programación no las cambió: disputa las
pruebas de abajo, dice que están mal, y son lo único que falla.

Decide como product owner, solo a partir de los criterios de aceptación de la tarea y de la descripción del
producto:
- UPHOLD cuando las pruebas disputadas comprueban lo que piden los criterios y la descripción del producto
  (el código tiene que cambiar para pasarlas);
- REVISE cuando una prueba disputada está mal: contradice los criterios o la descripción del producto,
  afirma un valor equivocado o no puede pasar con ninguna implementación correcta (entonces las pruebas se
  escriben de nuevo).

Responde con exactamente un bloque ```yaml y nada más, en este formato. La decisión se escribe tal cual
(UPHOLD o REVISE); la razón va en español:

```yaml
decision: UPHOLD      # o REVISE
reason: |
  ...
```

Tarea:
{task}

Pruebas disputadas y las razones del agente:
{disputed}

Código de los archivos de prueba disputados:
{tests}

Descripción del producto:
{knowledge}
"""

_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)


def parse_answers(text: str, question_ids: list[str]) -> dict[str, Any]:
    """The answers file in the product owner's reply, restricted to the fields harness accepts."""
    match = _YAML_BLOCK.search(text)
    raw = yaml.safe_load(match.group(1) if match else text)
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict):
        raise ValueError("the product owner did not return an answers map")
    answers = {
        str(k): str(v).strip() for k, v in raw["answers"].items() if str(k) in question_ids and v
    }
    if not answers:
        raise ValueError("the product owner answered none of the questions")
    result: dict[str, Any] = {"answers": answers}
    for key in ("replaceCriteria", "addCriteria", "addRequirements"):
        if raw.get(key):
            result[key] = [
                {k: (v.strip() if isinstance(v, str) else v) for k, v in item.items()}
                if isinstance(item, dict)
                else str(item).strip()
                for item in raw[key]
            ]
    return result


def parse_dispute(text: str) -> dict[str, str]:
    """The decision (UPHOLD or REVISE) and the reason in the product owner's reply."""
    match = _YAML_BLOCK.search(text)
    raw = yaml.safe_load(match.group(1) if match else text)
    if not isinstance(raw, dict):
        raise ValueError("the product owner did not return a decision")
    decision = str(raw.get("decision") or "").strip().upper()
    reason = str(raw.get("reason") or "").strip()
    if decision not in ("UPHOLD", "REVISE"):
        raise ValueError(f"the decision is not UPHOLD or REVISE: {decision!r}")
    if not reason:
        raise ValueError("the decision has no reason")
    return {"decision": decision, "reason": reason}


class ProductOwner:
    """Answers the open clarification request of a task with one agent call per round."""

    actor = PRODUCT_OWNER_ACTOR

    def __init__(self, knowledge: str, model: str, run_dir: Path) -> None:
        self.knowledge = knowledge
        self.model = model
        self.calls = run_dir / "po-calls"
        self.scratch = run_dir / "po-scratch"
        self.rounds: list[dict[str, Any]] = []

    def __call__(self, task: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        self.calls.mkdir(parents=True, exist_ok=True)
        self.scratch.mkdir(parents=True, exist_ok=True)
        questions = request["questions"]
        prompt = PO_PROMPT.format(
            task=yaml.safe_dump(task, sort_keys=False, allow_unicode=True),
            questions="\n".join(f"- {q['questionId']}: {q['text']}" for q in questions),
            knowledge=self.knowledge,
        )
        ids = [q["questionId"] for q in questions]
        record = run_claude(
            prompt,
            self.scratch,
            self.model,
            allocate_call(self.calls),
            timeout_seconds=PO_TIMEOUT_SECONDS,
            max_budget_usd=PO_BUDGET_USD,
            resumable=True,
        )
        try:
            answers = parse_answers(record.get("resultText", ""), ids)
        except (ValueError, yaml.YAMLError) as error:
            # One retry with the parse error, as a person would be asked to fix a malformed file.
            retry = (
                prompt
                + "\n\nTu respuesta anterior no se pudo leer como el archivo de respuestas ("
                + str(error)[:300]
                + "). Responde de nuevo con un solo bloque ```yaml válido, con cada texto como un bloque |."
            )
            record = run_claude(
                retry,
                self.scratch,
                self.model,
                allocate_call(self.calls),
                timeout_seconds=PO_TIMEOUT_SECONDS,
                max_budget_usd=PO_BUDGET_USD,
                resumable=True,
            )
            answers = parse_answers(record.get("resultText", ""), ids)
        # A rewrite of a criterion the task does not have becomes a new criterion instead of an error.
        known = {c.get("criterionId") for c in task.get("acceptanceCriteria", [])}
        replace = [
            c
            for c in answers.get("replaceCriteria", [])
            if isinstance(c, dict) and c.get("criterionId") in known
        ]
        moved = [
            c.get("text")
            for c in answers.get("replaceCriteria", [])
            if c not in replace and isinstance(c, dict)
        ]
        if "replaceCriteria" in answers:
            answers.pop("replaceCriteria")
            if replace:
                answers["replaceCriteria"] = replace
            if [t for t in moved if t]:
                answers["addCriteria"] = list(answers.get("addCriteria", [])) + [
                    t for t in moved if t
                ]
        self._record(
            {
                "questions": questions,
                "answers": answers,
            },
            record,
        )
        return answers

    def _record(self, entry: dict[str, Any], record: dict[str, Any]) -> None:
        usage = ("costUsd", "inputTokens", "outputTokens", "numTurns", "wallSeconds")
        self.rounds.append({**entry, "usage": {k: record.get(k) for k in usage}})

    def _ask(self, prompt: str) -> dict[str, Any]:
        self.calls.mkdir(parents=True, exist_ok=True)
        self.scratch.mkdir(parents=True, exist_ok=True)
        return run_claude(
            prompt,
            self.scratch,
            self.model,
            allocate_call(self.calls),
            timeout_seconds=PO_TIMEOUT_SECONDS,
            max_budget_usd=PO_BUDGET_USD,
            resumable=True,
        )

    def judge_dispute(self, task: dict[str, Any], dispute: dict[str, Any]) -> dict[str, str]:
        """UPHOLD or REVISE the disputed frozen acceptance tests, with a reason (one call, and
        one retry with the parse error)."""
        prompt = DISPUTE_PROMPT.format(
            task=yaml.safe_dump(task, sort_keys=False, allow_unicode=True),
            disputed="\n".join(
                f"- {d.get('test')}: {d.get('reason')}" for d in dispute.get("disputed") or []
            ),
            tests="\n\n".join(
                f"{t.get('path')}:\n```\n{t.get('content')}\n```"
                for t in dispute.get("tests") or []
            ),
            knowledge=self.knowledge,
        )
        record = self._ask(prompt)
        try:
            verdict = parse_dispute(record.get("resultText", ""))
        except (ValueError, yaml.YAMLError) as error:
            record = self._ask(
                prompt
                + "\n\nTu respuesta anterior no se pudo leer ("
                + str(error)[:300]
                + "). Responde de nuevo con un solo bloque ```yaml válido con decision y reason."
            )
            verdict = parse_dispute(record.get("resultText", ""))
        self._record(
            {"kind": "acceptance-dispute", "disputed": dispute.get("disputed"), **verdict}, record
        )
        return verdict
