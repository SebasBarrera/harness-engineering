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

DISPUTE_PROMPT = """You are the product owner of the product described at the end of this message. A coding
agent implemented the task below. Acceptance tests for the task were written by another agent call and frozen
after a person approved them. The coding agent did not change them: it disputes the tests below, saying they are
wrong, and they are the only thing that fails.

Decide as the product owner, from the task's acceptance criteria and the product description only:
- UPHOLD when the disputed tests check what the criteria and the product description ask for (the code must
  change to pass them);
- REVISE when a disputed test is wrong: it contradicts the criteria or the product description, asserts a wrong
  value, or cannot pass for any correct implementation (the tests are then written again).

Reply with exactly one ```yaml block and nothing else, in this format:

```yaml
decision: UPHOLD      # or REVISE
reason: |
  ...
```

Task:
{task}

Disputed tests and the agent's reasons:
{disputed}

Source of the disputed test files:
{tests}

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
                + "\n\nYour previous reply could not be read as the answers file ("
                + str(error)[:300]
                + "). Reply again with one valid ```yaml block, every text as a | block scalar."
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
                + "\n\nYour previous reply could not be read ("
                + str(error)[:300]
                + "). Reply again with one valid ```yaml block with decision and reason."
            )
            verdict = parse_dispute(record.get("resultText", ""))
        self._record(
            {"kind": "acceptance-dispute", "disputed": dispute.get("disputed"), **verdict}, record
        )
        return verdict
