"""The simulated person of the governed conditions (declared as a simulation in the thesis).

A governed run of 2.0.0 stops for a person at more points than DECISION. The evaluation cannot
have a person at every run, so two simulated actors stand in, with rules fixed in code before
any run:

* ``human.product-owner-simulated`` (``product_owner.py``) answers the clarification questions
  about the task (rules C0-C3, T1, A1, A2): a separate agent call that knows the full task and the
  specification (a declared bias in favour of the governed condition).
* ``human.reviewer-simulated`` (this module) handles every other wait, without a model:

  ======================  ===================================================================
  Wait                    Rule
  ======================  ===================================================================
  project setup (P1)      Fixed answers, in the same answers file as the product owner's:
                          testing ``conventional``, standards ``default`` (the detected packs),
                          architecture ``custom`` (asked only when ``architecture.mode`` is not
                          ``agent``).
  operational contract    ``harness task confirm`` when the contract has no missing item;
                          otherwise not confirmed (the run stops).
  architecture options    ``harness architecture decide --option`` the option the agent marked
                          recommended (the first one when none is); none proposed: not decided.
  architecture rules      ``--decision APPROVE`` when the survey returned its layers, else REJECT.
  acceptance tests        APPROVE when the proposal has at least one test file, each with a path
                          and content, and its digest is the one ``acceptance show`` prints;
                          REJECT otherwise.
  plan (decomposition)    APPROVE when every sub-task has a title and the proposal's digest is the
                          one ``plan show`` prints; REJECT otherwise.
  preflight UNAVAILABLE   ``harness verification decide --continue-uncertified``, citing the
                          reasons the preflight listed (the criteria end WAIVED, never certified).
  DECISION                the 0.9.0 rule: APPROVE when the gate PASSED (ticking every item of the
                          manual checklist and acknowledging every risk factor the brief lists as
                          requiring it); otherwise REQUEST_CHANGES with the gate reasons and the
                          findings as feedback, at most two correction cycles; then REJECT. Never
                          APPROVE_EXCEPTION.
  budget exceeded         never raised: the run stops (recorded).
  deferred verification   no evidence can be attached: recorded, never acted on.
  ======================  ===================================================================

Every act uses ``--no-continue`` and then ``harness run continue`` so the exit code of the run is
read in one place. Every wait is logged with what the harness showed (inbox kinds, the phase
summary) and the command answered.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REVIEWER_ACTOR = "human.reviewer-simulated"
MAX_CORRECTIONS = 2
MAX_CLARIFY_ROUNDS = 3
MAX_WAITS = 20
PROJECT_SETUP = {
    "project:testing": "conventional",
    "project:standards": "default",
    "project:architecture": "custom",
}
APPROVE_RATIONALE = "Gate passed; verification and review evidence inspected."
CHANGES_RATIONALE = "Gate did not pass; changes requested with the findings as feedback."
REJECT_RATIONALE = "Gate did not pass after the allowed correction cycles."

_DECIDE = {
    "acceptance": re.compile(
        r"harness acceptance decide --run \S+ --decision APPROVE --digest (\S+)"
    ),
    "plan": re.compile(r"harness plan decide --run \S+ --decision APPROVE --digest (\S+)"),
    "architecture-options": re.compile(
        r"harness architecture decide --run \S+ --option <id> --digest (\S+)"
    ),
    "architecture-rules": re.compile(
        r"harness architecture decide --run \S+ --decision APPROVE --digest (\S+)"
    ),
    "contract": re.compile(r"harness task confirm --task \S+ --digest (\S+)"),
    "preflight": re.compile(r"harness verification decide --run \S+ --continue-uncertified"),
}


def recorded(proc: Any) -> bool:
    """A decision command recorded the act. With ``--no-continue`` a command that reports the run
    (``plan decide`` on the plan-approval checkpoint) exits with the code of the still-blocked run
    (6) after recording; refusals are 5, unknown runs 3, configuration errors 2, crashes 1."""
    return int(proc.returncode) in (0, 4, 6)


def _json(proc: Any) -> Any:
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None


class SimulatedPerson:
    """Drives one governed run from its first exit code to an outcome."""

    def __init__(
        self,
        harness: Callable[..., Any],
        workspace: Path,
        run_dir: Path,
        clarifier: Any = None,
        feedback: Callable[[str, dict[str, Any]], str] | None = None,
    ) -> None:
        self.harness = harness
        self.workspace = workspace
        self.run_dir = run_dir
        self.clarifier = clarifier
        self.feedback = feedback
        self.waits: list[dict[str, Any]] = []
        self.clarification: list[dict[str, Any]] = []
        self.gate_history: list[str] = []
        self.decisions: list[dict[str, Any]] = []
        self.corrections = 0
        self.seconds = 0.0
        """Time of the simulated person's own work (its harness commands, not the product owner's
        model calls, which are timed in po-calls)."""

    # ----- helpers ---------------------------------------------------------------------------
    def h(self, *args: str) -> Any:
        started = time.monotonic()
        proc = self.harness(self.workspace, *args)
        self.seconds += time.monotonic() - started
        return proc

    def hj(self, *args: str) -> Any:
        return _json(self.h(*args, "--path", "."))

    def resume(self, run_id: str) -> int:
        return int(
            self.harness(
                self.workspace, "run", "continue", "--path", ".", "--run", run_id
            ).returncode
        )

    # ----- the loop --------------------------------------------------------------------------
    def drive(self, run_id: str, task_id: str, code: int) -> dict[str, Any]:
        outcome = None
        for _ in range(MAX_WAITS):
            if code == 4:
                code, outcome = self.decide(run_id)
                if outcome is not None:
                    break
                continue
            if code == 0:
                outcome = (
                    "approved"
                    if any(d["decision"] == "APPROVE" for d in self.decisions)
                    else "closed"
                )
                break
            if code != 6:
                outcome = f"exit-{code}"
                break
            acted = self.wait(run_id, task_id)
            if acted is None:
                outcome = "stopped"
                break
            code = acted
        else:
            outcome = "wait-limit"
        return {
            "outcome": outcome,
            "corrections": self.corrections,
            "gateHistory": self.gate_history,
            "decisions": self.decisions,
            "waits": self.waits,
            "clarification": self.clarification,
            "personSeconds": round(self.seconds, 3),
        }

    # ----- waits before DECISION ---------------------------------------------------------------
    def wait(self, run_id: str, task_id: str) -> int | None:
        """Handle the wait the run stopped at; the exit code of the next ``run continue``, or None
        when the run did not stop for a person (or the rule does not act)."""
        status = self.hj("status", "--run", run_id) or {}
        execution = status.get("execution") or {}
        phase, state = execution.get("currentPhase"), execution.get("status")
        summary = ""
        for item in status.get("phases") or []:
            if item.get("phaseId") == phase:
                summary = item.get("summary") or ""
        inbox = self.hj("inbox", "--json") or []
        entry: dict[str, Any] = {
            "phase": phase,
            "status": state,
            "summary": summary[:600],
            "inboxKinds": sorted({i.get("kind") for i in inbox if i.get("executionId") == run_id}),
        }
        self.waits.append(entry)
        if state != "BLOCKED":
            entry["wait"] = None
            return None
        if phase == "INTENT":
            questions = self.hj("task", "questions", "--task", task_id) or {}
            if questions.get("openRequest"):
                entry["wait"] = "clarification"
                return self.clarify(run_id, task_id, questions["openRequest"], entry)
        for kind, pattern in _DECIDE.items():
            match = pattern.search(summary)
            if match:
                entry["wait"] = kind
                return getattr(self, "_" + kind.replace("-", "_"))(run_id, task_id, match, entry)
        entry["wait"] = "budget" if "budget" in summary.lower() else None
        return None

    def clarify(
        self, run_id: str, task_id: str, request: dict[str, Any], entry: dict[str, Any]
    ) -> int | None:
        if len(self.clarification) >= MAX_CLARIFY_ROUNDS:
            entry["action"] = "clarification-rounds-exhausted"
            return None
        questions = request.get("questions") or []
        setup = [q for q in questions if q.get("ruleId") == "P1"]
        task_questions = [q for q in questions if q.get("ruleId") != "P1"]
        answers: dict[str, Any] = {
            "answers": {
                q["questionId"]: PROJECT_SETUP.get(q.get("target", ""), "default") for q in setup
            }
        }
        actor = REVIEWER_ACTOR
        round_record: dict[str, Any] = {
            "questions": [q.get("ruleId") for q in questions],
            "categories": [q.get("category") for q in questions],
            "projectSetup": len(setup),
            "taskQuestions": len(task_questions),
        }
        if task_questions:
            if self.clarifier is None:
                entry["action"] = "no-clarifier"
                self.clarification.append({**round_record, "error": "no product owner"})
                return None
            current = self.hj("task", "show", "--task", task_id) or {}
            try:
                owner = self.clarifier(current, {**request, "questions": task_questions})
            except Exception as error:  # noqa: BLE001 - the clarifier is an agent; its failure is a result
                self.clarification.append(
                    {**round_record, "error": f"{type(error).__name__}: {str(error)[:300]}"}
                )
                entry["action"] = "product-owner-failed"
                return None
            owner_answers = dict(owner.get("answers") or {})
            answers = {**owner, "answers": {**owner_answers, **answers["answers"]}}
            actor = getattr(self.clarifier, "actor", REVIEWER_ACTOR)
        number = len(self.clarification) + 1
        answers_file = self.run_dir / f"answers-{task_id}-{number}.yaml"
        answers_file.write_text(
            yaml.safe_dump(answers, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        proc = self.h(
            "task",
            "clarify",
            "--path",
            ".",
            "--task",
            task_id,
            "--file",
            str(answers_file),
            "--actor",
            actor,
        )
        self.clarification.append(
            {
                **round_record,
                "actor": actor,
                "answered": sorted(answers["answers"]),
                "clarifyExit": proc.returncode,
            }
        )
        entry["action"] = f"task clarify ({actor}) -> {proc.returncode}"
        if proc.returncode != 0:
            return None
        return self.resume(run_id)

    def _contract(self, run_id: str, task_id: str, match: Any, entry: dict[str, Any]) -> int | None:
        proc = self.h(
            "task",
            "confirm",
            "--path",
            ".",
            "--task",
            task_id,
            "--digest",
            match.group(1),
            "--actor",
            REVIEWER_ACTOR,
        )
        entry["action"] = f"task confirm -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _acceptance(
        self, run_id: str, task_id: str, match: Any, entry: dict[str, Any]
    ) -> int | None:
        shown = self.hj("acceptance", "show", "--run", run_id) or {}
        tests = shown.get("tests") or []
        complete = (
            bool(tests)
            and all(t.get("path") and t.get("content") for t in tests)
            and shown.get("digest") == match.group(1)
        )
        entry["evidence"] = {
            "files": len(tests),
            "digestMatches": shown.get("digest") == match.group(1),
        }
        return self._decide_proposal(
            run_id,
            "acceptance",
            match.group(1),
            complete,
            entry,
            "Acceptance tests proposed for every criterion; evidence complete.",
        )

    def _plan(self, run_id: str, task_id: str, match: Any, entry: dict[str, Any]) -> int | None:
        shown = self.hj("plan", "show", "--run", run_id) or {}
        approval = shown.get("approval") if isinstance(shown.get("approval"), dict) else None
        if approval is not None and approval.get("status") == "PENDING":
            # The plan-approval checkpoint (friction.planApproval: risk): the recorded plan of a
            # task with a risk flag, bound to its digest.
            complete = approval.get("digest") == match.group(1) and bool(approval.get("planRef"))
            entry["wait"] = "plan-approval"
            entry["evidence"] = {
                "reasons": approval.get("reasons"),
                "digestMatches": approval.get("digest") == match.group(1),
            }
            why = "The plan is recorded and bound to its digest; the risk flags are listed."
        else:
            subtasks = shown.get("subtasks") or []
            complete = (
                bool(subtasks)
                and all(s.get("title") for s in subtasks)
                and shown.get("digest") == match.group(1)
            )
            entry["evidence"] = {
                "subtasks": len(subtasks),
                "digestMatches": shown.get("digest") == match.group(1),
            }
            why = "Decomposition partitions the requirements; evidence complete."
        return self._decide_proposal(run_id, "plan", match.group(1), complete, entry, why)

    def _decide_proposal(
        self,
        run_id: str,
        command: str,
        digest: str,
        complete: bool,
        entry: dict[str, Any],
        why: str,
    ) -> int | None:
        decision = "APPROVE" if complete else "REJECT"
        rationale = why if complete else "The proposal is incomplete."
        proc = self.h(
            command,
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            decision,
            "--digest",
            digest,
            "--rationale",
            rationale,
            "--actor",
            REVIEWER_ACTOR,
            "--no-continue",
        )
        entry["action"] = f"{command} decide {decision} -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _architecture_options(
        self, run_id: str, task_id: str, match: Any, entry: dict[str, Any]
    ) -> int | None:
        shown = self.hj("architecture", "show", "--json") or {}
        options = _find_options(shown)
        if not options:
            entry["action"] = "no-options-found"
            return None
        chosen = next((o for o in options if o.get("recommended")), options[0])
        entry["evidence"] = {
            "options": len(options),
            "chosen": chosen.get("id"),
            "recommended": bool(chosen.get("recommended")),
        }
        proc = self.h(
            "architecture",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--option",
            str(chosen.get("id")),
            "--digest",
            match.group(1),
            "--rationale",
            "The option the proposal recommends.",
            "--actor",
            REVIEWER_ACTOR,
            "--no-continue",
        )
        entry["action"] = f"architecture decide --option {chosen.get('id')} -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _architecture_rules(
        self, run_id: str, task_id: str, match: Any, entry: dict[str, Any]
    ) -> int | None:
        layers = re.search(r"inferred (\d+) layer", entry["summary"])
        decision = "APPROVE" if layers and int(layers.group(1)) > 0 else "REJECT"
        if "rules wait for a person" in entry["summary"]:
            decision = "APPROVE"
        proc = self.h(
            "architecture",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            decision,
            "--digest",
            match.group(1),
            "--rationale",
            "The inferred layer rules match the survey.",
            "--actor",
            REVIEWER_ACTOR,
            "--no-continue",
        )
        entry["action"] = f"architecture decide {decision} -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _preflight(
        self, run_id: str, task_id: str, match: Any, entry: dict[str, Any]
    ) -> int | None:
        proc = self.h(
            "verification",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--continue-uncertified",
            "--rationale",
            "This environment cannot reach the listed rungs; continue uncertified.",
            "--actor",
            REVIEWER_ACTOR,
            "--no-continue",
        )
        entry["action"] = f"verification decide --continue-uncertified -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    # ----- DECISION ------------------------------------------------------------------------------
    def decide(self, run_id: str) -> tuple[int, str | None]:
        status = self.hj("status", "--run", run_id) or {}
        brief = self.hj("review", "--run", run_id, "--json") or {}
        gate = (status.get("gate") or {}).get("status")
        digest = (status.get("execution") or {}).get("changeSetDigest")
        self.gate_history.append(str(gate))
        extra: list[str] = []
        if gate == "PASSED":
            decision, rationale = "APPROVE", APPROVE_RATIONALE
            for item in brief.get("checklist") or []:
                if not item.get("checked"):
                    extra += ["--check", str(item.get("itemId"))]
            for factor in (brief.get("riskFactors") or {}).get("acknowledgementRequired") or []:
                extra += [
                    "--acknowledge-risk",
                    str(factor.get("factor") if isinstance(factor, dict) else factor),
                ]
        elif self.corrections < MAX_CORRECTIONS:
            decision, rationale = "REQUEST_CHANGES", CHANGES_RATIONALE
            if self.feedback is not None:
                (self.run_dir / "feedback.md").write_text(
                    self.feedback(run_id, status), encoding="utf-8"
                )
        else:
            decision, rationale = "REJECT", REJECT_RATIONALE
        proc = self.h(
            "gate",
            "decide",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            decision,
            "--change-set-digest",
            str(digest),
            "--actor",
            REVIEWER_ACTOR,
            "--rationale",
            rationale,
            "--no-continue",
            *extra,
        )
        self.decisions.append(
            {
                "decision": decision,
                "gate": gate,
                "exit": proc.returncode,
                "checks": extra.count("--check"),
                "riskAcknowledged": extra.count("--acknowledge-risk"),
                "error": proc.stderr[-300:] if proc.returncode else None,
            }
        )
        if not recorded(proc):
            return proc.returncode, "decision-refused"
        if decision == "REQUEST_CHANGES":
            self.corrections += 1
        code = self.resume(run_id)
        if decision == "REJECT":
            return code, "rejected"
        if decision == "APPROVE":
            return code, "approved" if code == 0 else f"approved-closure-exit-{code}"
        return code, None


def _find_options(shown: Any) -> list[dict[str, Any]]:
    """The architecture options of a pending proposal, wherever ``architecture show`` puts them."""
    if isinstance(shown, dict):
        if isinstance(shown.get("options"), list) and all(
            isinstance(o, dict) for o in shown["options"]
        ):
            return shown["options"]
        for value in shown.values():
            found = _find_options(value)
            if found:
                return found
    if isinstance(shown, list):
        for value in shown:
            found = _find_options(value)
            if found:
                return found
    return []
