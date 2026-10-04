"""Simulated product owner who answers the clarification questions of the harness (1.1.0 and later).

With ``intake.criteriaPolicy: enforce`` INTENT blocks a task whose acceptance criteria cannot be observed
and asks concrete questions. In a real project a person answers them; in the evaluation a separate agent
call plays that person. It holds the product description (for example SPEC.md) and answers only what is
asked, in the answers-file format of ``harness task clarify``. It never sees the workspace and never writes
code. Its usage is recorded apart from the developer agent (``po-calls``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from agentlib import run_claude

PRODUCT_OWNER_ACTOR = "human.product-owner-simulated"
PO_TIMEOUT_SECONDS = 1800
PO_BUDGET_USD = "20"

PO_PROMPT = """You are the product owner of the product described at the end of this message. A developer
wrote the task below for a coding agent, and the development process tool stopped it before any work because
its acceptance criteria cannot be checked. The tool asks the questions below.

Answer them as a product owner would in a short written clarification, in English:
- answer every question, concisely and in your own words, from the product description;
- answer only what each question asks; do not paste the product description;
- then state the task changes your answers imply: acceptance criteria that can be checked (observable
  results for given inputs or actions) and, if a question asks about scope, the requirements in scope.

Reply with exactly one ```yaml block and nothing else, in this format. Write every text as a literal block
scalar (a "|" followed by indented lines), so that no quoting is needed:

```yaml
answers:              # every question id below, mapped to your answer
  Q-1: |
    ...
replaceCriteria:      # optional: rewrite a criterion by its id
  - criterionId: ...
    text: |
      ...
addCriteria:          # optional: new checkable criteria
  - |
    ...
addRequirements:      # optional: requirements in scope
  - |
    ...
```

Task:
{task}

Questions:
{questions}

Product description:
{knowledge}
"""

_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)


def parse_answers(text: str, question_ids: list[str]) -> dict[str, Any]:
    """The answers file in the product owner's reply, restricted to the fields harness accepts."""
    match = _YAML_BLOCK.search(text)
    raw = yaml.safe_load(match.group(1) if match else text)
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict):
        raise ValueError("the product owner did not return an answers map")
    answers = {str(k): str(v).strip() for k, v in raw["answers"].items() if str(k) in question_ids and v}
    if not answers:
        raise ValueError("the product owner answered none of the questions")
    result: dict[str, Any] = {"answers": answers}
    for key in ("replaceCriteria", "addCriteria", "addRequirements"):
        if raw.get(key):
            result[key] = [
                {k: (v.strip() if isinstance(v, str) else v) for k, v in item.items()} if isinstance(item, dict)
                else str(item).strip()
                for item in raw[key]
            ]
    return result


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
        number = len(list(self.calls.glob("call-*.json"))) + 1
        record = run_claude(prompt, self.scratch, self.model, self.calls / f"call-{number}.json",
                            timeout_seconds=PO_TIMEOUT_SECONDS, max_budget_usd=PO_BUDGET_USD, resumable=True)
        try:
            answers = parse_answers(record.get("resultText", ""), ids)
        except (ValueError, yaml.YAMLError) as error:
            # One retry with the parse error, as a person would be asked to fix a malformed file.
            retry = (prompt + "\n\nYour previous reply could not be read as the answers file (" + str(error)[:300]
                     + "). Reply again with one valid ```yaml block, every text as a | block scalar.")
            record = run_claude(retry, self.scratch, self.model, self.calls / f"call-{number + 1}.json",
                                timeout_seconds=PO_TIMEOUT_SECONDS, max_budget_usd=PO_BUDGET_USD, resumable=True)
            answers = parse_answers(record.get("resultText", ""), ids)
        # A rewrite of a criterion the task does not have becomes a new criterion instead of an error.
        known = {c.get("criterionId") for c in task.get("acceptanceCriteria", [])}
        replace = [c for c in answers.get("replaceCriteria", []) if isinstance(c, dict) and c.get("criterionId") in known]
        moved = [c.get("text") for c in answers.get("replaceCriteria", []) if c not in replace and isinstance(c, dict)]
        if "replaceCriteria" in answers:
            answers.pop("replaceCriteria")
            if replace:
                answers["replaceCriteria"] = replace
            if [t for t in moved if t]:
                answers["addCriteria"] = list(answers.get("addCriteria", [])) + [t for t in moved if t]
        self.rounds.append({"questions": questions, "answers": answers, "usage": {
            k: record.get(k) for k in ("costUsd", "inputTokens", "outputTokens", "numTurns", "wallSeconds")}})
        return answers
