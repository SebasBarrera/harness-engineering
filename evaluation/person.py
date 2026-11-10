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
  failed read-only call   not acted on: the run stops (recorded as ``failed-call``). Since wave 9
                          the harness itself sends a broken read-only answer once more
                          (``runtime.contractRetry``, #80); the evaluation no longer retries.
  refused command         not acted on: the run stops (``policy-refusal``): a provider command
                          outside the grants (#87) or a destructive one (#76) blocks the run.
  budget exceeded         never raised: the run stops (recorded).
  deferred verification   no evidence can be attached: recorded, never acted on.
  ======================  ===================================================================

Which wait a run is in is read from ``harness inbox --json`` (since wave 9, #73, it lists every
wait before DECISION with its ``kind``, the ``digest`` the answer binds to and the command that
answers it); the phase summary is a fallback for a wait the inbox does not list. Clarification
rounds are bounded by the harness (``intake.ambiguityReview.maxRounds``, #79, after which it
records assumptions and continues); the person's own cap is only a guard above that bound.

Every act uses ``--no-continue`` and then ``harness run continue`` so the exit code of the run is
read in one place (since #83 ``plan decide --no-continue`` exits 0 once recorded, and
``run continue`` on a rejected run reports it closed, exit 6). Every wait is logged with what the
harness showed (inbox kinds, the phase summary) and the command answered.
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
# A guard, not the rule: the harness bounds the agent's rounds (``maxRounds`` 3 as init writes
# it, rule A1 only); the project-setup (P1) and deterministic (C0-C3, T1) rounds come on top.
MAX_CLARIFY_ROUNDS = 6
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
    """A decision command recorded the act: 0 (since #83 also ``plan decide --no-continue`` on
    the plan-approval checkpoint, which exited 6 before), 4 or 6 for a command that reports the
    run it recorded on (a REJECT ends the run: 6); refusals are 5, unknown runs 3, configuration
    errors 2, crashes 1."""
    return int(proc.returncode) in (0, 4, 6)


# The inbox kind of a wait before DECISION (#73) and the person's handler for it.
INBOX_KINDS = {
    "contract": "contract",
    "architecture": "architecture",
    "acceptance": "acceptance",
    "decomposition": "plan",
    "plan": "plan",
    "preflight": "preflight",
}
POLICY_REFUSAL = re.compile(
    r"CommandRefused|DestructiveActionDenied|capabilities\.command-denied"
    r"|capabilities\.destructive-denied|sandbox\.unavailable",
)
FAILED_CALL = re.compile(
    r"call did not answer|malformed result|tests are not valid|survey did not answer"
    r"|broke its contract|contract retry",
    re.IGNORECASE,
)


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
        session: Callable[[str, str], int] | None = None,
    ) -> None:
        self.harness = harness
        self.workspace = workspace
        self.run_dir = run_dir
        self.clarifier = clarifier
        self.feedback = feedback
        self.session = session
        """Embedded mode: the host session that implements (``run_embedded.py``). It is resumed with
        a message when the run waits for its edits (and after REQUEST_CHANGES or a failed
        verification, which the session provider does not correct on its own); it returns the
        exit code of the run afterwards."""
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
        inbox = [i for i in self.hj("inbox", "--json") or [] if i.get("executionId") == run_id]
        entry: dict[str, Any] = {
            "phase": phase,
            "status": state,
            "summary": summary[:600],
            "inboxKinds": sorted({str(i.get("kind")) for i in inbox}),
        }
        self.waits.append(entry)
        if (
            self.session is not None
            and phase in ("IMPLEMENTATION", "VERIFICATION")
            and ("Waiting for the agent session" in summary or state in ("BLOCKED", "FAILED"))
        ):
            # Embedded mode: the session implements and corrects; the person does not.
            entry["wait"] = "session"
            entry["action"] = "session resumed"
            return self.session(run_id, summary)
        if state != "BLOCKED":
            entry["wait"] = None
            return None
        if phase == "INTENT":
            questions = self.hj("task", "questions", "--task", task_id) or {}
            if questions.get("openRequest"):
                entry["wait"] = "clarification"
                return self.clarify(run_id, task_id, questions["openRequest"], entry)
        for item in inbox:
            handler = INBOX_KINDS.get(str(item.get("kind")))
            if handler is None or (handler != "preflight" and not item.get("digest")):
                continue
            entry["source"] = "inbox"
            return self._answer(run_id, task_id, handler, str(item.get("digest")), item, entry)
        for kind, pattern in _DECIDE.items():
            match = pattern.search(summary)
            if match:
                # A wait the inbox does not list: the phase summary names the command.
                entry["source"] = "summary"
                digest = match.group(1) if match.groups() else ""
                handler = "architecture" if kind.startswith("architecture") else kind
                return self._answer(run_id, task_id, handler, digest, {}, entry)
        # Stops the person does not act on, labelled for the report.
        if POLICY_REFUSAL.search(summary):
            entry["wait"] = "policy-refusal"
        elif FAILED_CALL.search(summary):
            entry["wait"] = "failed-call"
        else:
            entry["wait"] = "budget" if "budget" in summary.lower() else None
        return None

    def _answer(
        self,
        run_id: str,
        task_id: str,
        handler: str,
        digest: str,
        item: dict[str, Any],
        entry: dict[str, Any],
    ) -> int | None:
        """Answer one wait before DECISION, bound to the digest the harness showed."""
        entry["wait"] = str(item.get("kind") or handler)
        if handler == "architecture":
            text = f"{item.get('next', '')} {item.get('summary', '')} {entry['summary']}"
            if "--option" in text or "architecture option" in text:
                entry["wait"] = "architecture-options"
                return self._architecture_options(run_id, digest, entry)
            entry["wait"] = "architecture-rules"
            return self._architecture_rules(run_id, digest, item, entry)
        if handler == "plan":
            return self._plan(run_id, digest, entry)
        if handler == "acceptance":
            return self._acceptance(run_id, digest, entry)
        if handler == "contract":
            return self._contract(run_id, task_id, digest, entry)
        return self._preflight(run_id, entry)

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

    def _contract(
        self, run_id: str, task_id: str, digest: str, entry: dict[str, Any]
    ) -> int | None:
        proc = self.h(
            "task",
            "confirm",
            "--path",
            ".",
            "--task",
            task_id,
            "--digest",
            digest,
            "--actor",
            REVIEWER_ACTOR,
        )
        entry["action"] = f"task confirm -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _acceptance(self, run_id: str, digest: str, entry: dict[str, Any]) -> int | None:
        shown = self.hj("acceptance", "show", "--run", run_id) or {}
        tests = shown.get("tests") or []
        complete = (
            bool(tests)
            and all(t.get("path") and t.get("content") for t in tests)
            and shown.get("digest") == digest
        )
        entry["evidence"] = {"files": len(tests), "digestMatches": shown.get("digest") == digest}
        return self._decide_proposal(
            run_id,
            "acceptance",
            digest,
            complete,
            entry,
            "Acceptance tests proposed for every criterion; evidence complete.",
        )

    def _plan(self, run_id: str, digest: str, entry: dict[str, Any]) -> int | None:
        shown = self.hj("plan", "show", "--run", run_id) or {}
        approval = shown.get("approval") if isinstance(shown.get("approval"), dict) else None
        if approval is not None and approval.get("status") == "PENDING":
            # The plan-approval checkpoint (friction.planApproval: risk): the recorded plan of a
            # task with a risk flag, bound to its digest.
            complete = approval.get("digest") == digest and bool(approval.get("planRef"))
            entry["wait"] = "plan-approval"
            entry["evidence"] = {
                "reasons": approval.get("reasons"),
                "digestMatches": approval.get("digest") == digest,
            }
            why = "The plan is recorded and bound to its digest; the risk flags are listed."
        else:
            subtasks = shown.get("subtasks") or []
            complete = (
                bool(subtasks)
                and all(s.get("title") for s in subtasks)
                and shown.get("digest") == digest
            )
            entry["wait"] = "decomposition"
            entry["evidence"] = {
                "subtasks": len(subtasks),
                "digestMatches": shown.get("digest") == digest,
            }
            why = "Decomposition partitions the requirements; evidence complete."
        return self._decide_proposal(run_id, "plan", digest, complete, entry, why)

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

    def _architecture_options(self, run_id: str, digest: str, entry: dict[str, Any]) -> int | None:
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
            digest,
            "--rationale",
            "The option the proposal recommends.",
            "--actor",
            REVIEWER_ACTOR,
            "--no-continue",
        )
        entry["action"] = f"architecture decide --option {chosen.get('id')} -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _architecture_rules(
        self, run_id: str, digest: str, item: dict[str, Any], entry: dict[str, Any]
    ) -> int | None:
        # The inbox says "N inferred layer rule(s) to approve"; the phase summary "inferred N
        # layer(s)" (or that the rules wait for a person).
        text = f"{item.get('summary', '')} {entry['summary']}"
        layers = re.search(r"(\d+) inferred layer|inferred (\d+) layer", text)
        count = int(next(g for g in layers.groups() if g)) if layers else 0
        decision = "APPROVE" if count > 0 or "rules wait for a person" in text else "REJECT"
        entry["evidence"] = {"layers": count}
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
            digest,
            "--rationale",
            "The inferred layer rules match the survey.",
            "--actor",
            REVIEWER_ACTOR,
            "--no-continue",
        )
        entry["action"] = f"architecture decide {decision} -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _preflight(self, run_id: str, entry: dict[str, Any]) -> int | None:
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
        if decision == "REJECT":
            # Since #83 a rejected run stays closed: run continue reports it (FAILED, exit 6)
            # instead of evaluating the restored baseline and asking for a decision again.
            closed = self.resume(run_id)
            self.decisions[-1]["continueAfterReject"] = closed
            return closed, "rejected" if closed == 6 else f"rejected-continue-exit-{closed}"
        if decision == "REQUEST_CHANGES" and self.session is not None:
            # The session provider passes as soon as the workspace differs from the baseline: the
            # session must make the changes before the run continues.
            feedback = (
                (self.run_dir / "feedback.md").read_text(encoding="utf-8")
                if (self.run_dir / "feedback.md").exists()
                else CHANGES_RATIONALE
            )
            return self.session(run_id, "The person requested changes:\n" + feedback), None
        code = self.resume(run_id)
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
