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
                          and content, its digest is the one ``acceptance show`` prints and its
                          CONTENT passes ``review_acceptance_content``: every pytest file parses
                          and every ``test_*`` function makes a check that can fail (no
                          ``assert True``), every Gherkin scenario has a Then step, and there is
                          at least one test case per acceptance criterion of the task; REJECT
                          otherwise, naming what falls short.
  acceptance dispute      Added 2026-11-11 (after an apparatus precheck, before the pilot): the
                          agent disputes frozen acceptance tests and only they fail. The person
                          reads the dispute in ``acceptance show`` (the disputed tests, their
                          source and the agent's reasons, never the hidden tests) and asks the
                          product owner (``ProductOwner.judge_dispute``, one call, recorded in
                          ``po-calls``) for UPHOLD or REVISE given the task's criteria; without a
                          product owner the fixed fallback is UPHOLD (the approved tests stand).
                          The answer is bound to the digest the inbox shows and is not sent
                          when ``acceptance show`` shows another digest. After REVISE the run
                          returns to SPECIFICATION and the acceptance rule above approves the
                          rewritten tests.
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

import ast
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REVIEWER_ACTOR = "human.reviewer-simulated"
MAX_CORRECTIONS = 2
# A guard, not the rule: the harness bounds the agent's rounds (``maxRounds``, rule A1 only; with
# ``depth: adaptive`` up to ``max(maxRounds, depth.MAX_ROUNDS)``, amendment of 2026-11-12); the
# project-setup (P1) and deterministic (C0-C3, T1, basics) rounds come on top of them. The guard
# is that bound plus ``EXTRA_CLARIFY_ROUNDS``, so the person never stops before the harness does.
MAX_CLARIFY_ROUNDS = 6
"""The guard of a project without ``intake.ambiguityReview`` (3 agent rounds plus the extra)."""
EXTRA_CLARIFY_ROUNDS = 3
DEFAULT_AGENT_ROUNDS = 3
ADAPTIVE_MAX_ROUNDS = 8
"""``governed_harness.intake.depth.MAX_ROUNDS`` (read from the harness when it has it)."""
MAX_WAITS = 20
PROJECT_SETUP = {
    "project:testing": "conventional",
    "project:standards": "default",
    "project:architecture": "custom",
}
APPROVE_RATIONALE = "La compuerta pasó; se revisó la evidencia de verificación y de revisión."
CHANGES_RATIONALE = (
    "La compuerta no pasó; se piden cambios con los hallazgos como retroalimentación."
)
REJECT_RATIONALE = "La compuerta no pasó tras los ciclos de corrección permitidos."
DISPUTE_FALLBACK = "UPHOLD"
DISPUTE_FALLBACK_REASON = "No hay product owner que juzgue la disputa contra los criterios: las pruebas aprobadas se mantienen."

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
    "acceptance-dispute": re.compile(
        r"harness acceptance dispute --run \S+ --decision <UPHOLD\|REVISE> --digest (\S+)"
    ),
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
    "acceptance-dispute": "acceptance-dispute",
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


def _trivial_assert(node: ast.Assert) -> bool:
    """``assert True``, ``assert 1``, ``assert "x"`` or ``assert x == x``: it can never fail."""
    test = node.test
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        return ast.dump(test.left) == ast.dump(test.comparators[0]) and isinstance(
            test.ops[0], ast.Eq | ast.Is
        )
    return False


def _is_check(node: ast.AST) -> bool:
    """A non-trivial assert, or a ``pytest.raises`` / ``pytest.warns`` call."""
    if isinstance(node, ast.Assert):
        return not _trivial_assert(node)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"raises", "warns"}
    )


def _checks(function: ast.AST) -> int:
    """How many checks that can fail one test function makes."""
    return sum(1 for node in ast.walk(function) if _is_check(node))


def review_acceptance_content(
    tests: list[dict[str, Any]], criteria: int | None, *, gherkin: bool = False
) -> dict[str, Any]:
    """What a product owner checks in the CONTENT of proposed acceptance tests (simulated).

    pytest files must parse, hold ``test_*`` functions and every one of them must make at least
    one check that can fail (no ``assert True`` or ``assert x == x``); Gherkin files need a
    ``Then`` step in every scenario. With the task's criterion count known, there must be at
    least one test (or scenario) per criterion. Fixed rules, written before any run; they read
    the files, never run them."""
    problems: list[str] = []
    cases = 0
    for item in tests:
        path = str(item.get("path") or "?")
        content = str(item.get("content") or "")
        if gherkin:
            scenarios = re.split(r"(?m)^\s*Scenario(?: Outline)?:", content)[1:]
            cases += len(scenarios)
            if not scenarios:
                problems.append(f"{path}: no tiene ningún escenario")
            for number, body in enumerate(scenarios, start=1):
                if not re.search(r"(?m)^\s*Then\b", body):
                    problems.append(f"{path}: el escenario {number} no tiene paso Then")
            continue
        try:
            module = ast.parse(content, filename=path)
        except SyntaxError as error:
            problems.append(f"{path}: no se puede analizar ({error.msg}, línea {error.lineno})")
            continue
        functions = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
        ]
        cases += len(functions)
        if not functions:
            problems.append(f"{path}: no tiene ninguna función de prueba")
        for function in functions:
            if not _checks(function):
                problems.append(f"{path}: {function.name} no comprueba nada que pueda fallar")
    if criteria is not None and cases < criteria:
        problems.append(f"{cases} caso(s) de prueba para {criteria} criterio(s) de aceptación")
    return {"testCases": cases, "criteria": criteria, "problems": problems}


def clarify_round_cap(workspace: Path) -> int:
    """The person's cap of clarification rounds for the project of ``workspace``: the most agent
    rounds its ``intake.ambiguityReview`` allows (``maxRounds``; with ``depth: adaptive`` the
    adaptive ceiling) plus ``EXTRA_CLARIFY_ROUNDS``; ``MAX_CLARIFY_ROUNDS`` without the key."""
    path = workspace / ".harness" / "project.yaml"
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return MAX_CLARIFY_ROUNDS
    review = ((config.get("intake") or {}) if isinstance(config, dict) else {}).get(
        "ambiguityReview"
    )
    if not isinstance(review, dict):
        return MAX_CLARIFY_ROUNDS
    rounds = review.get("maxRounds")
    rounds = rounds if isinstance(rounds, int) and rounds > 0 else DEFAULT_AGENT_ROUNDS
    if review.get("depth") == "adaptive":
        try:
            from governed_harness.intake.depth import MAX_ROUNDS as ceiling
        except ImportError:
            ceiling = ADAPTIVE_MAX_ROUNDS
        rounds = max(rounds, ceiling)
    return max(MAX_CLARIFY_ROUNDS, rounds + EXTRA_CLARIFY_ROUNDS)


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
        self.task_id: str | None = None
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
        self.task_id = task_id
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
        if handler == "acceptance-dispute":
            return self._acceptance_dispute(run_id, digest, item, entry)
        if handler == "contract":
            return self._contract(run_id, task_id, digest, entry)
        return self._preflight(run_id, entry)

    def clarify(
        self, run_id: str, task_id: str, request: dict[str, Any], entry: dict[str, Any]
    ) -> int | None:
        if len(self.clarification) >= clarify_round_cap(self.workspace):
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
        structured = bool(tests) and all(t.get("path") and t.get("content") for t in tests)
        review = review_acceptance_content(
            tests, self._criteria_count(), gherkin=shown.get("format") == "gherkin"
        )
        complete = structured and not review["problems"] and shown.get("digest") == digest
        entry["evidence"] = {
            "files": len(tests),
            "digestMatches": shown.get("digest") == digest,
            "contentReview": review,
        }
        why = "Se propusieron pruebas de aceptación para cada criterio; se revisó su contenido."
        reject_why = "La propuesta está incompleta."
        if structured and review["problems"]:
            reject_why = "El contenido de las pruebas de aceptación se queda corto: " + "; ".join(
                review["problems"]
            )
        return self._decide_proposal(run_id, "acceptance", digest, complete, entry, why, reject_why)

    def _acceptance_dispute(
        self, run_id: str, digest: str, item: dict[str, Any], entry: dict[str, Any]
    ) -> int | None:
        """UPHOLD or REVISE frozen acceptance tests the agent disputes, as the product owner
        judges them from the criteria, the disputed tests' source and the agent's reasons."""
        entry["wait"] = "acceptance-dispute"
        shown = self.hj("acceptance", "show", "--run", run_id) or {}
        dispute = shown.get("dispute") if isinstance(shown.get("dispute"), dict) else {}
        disputed = list(dispute.get("disputed") or item.get("disputed") or [])
        tests = _disputed_sources(shown.get("tests") or [], disputed)
        entry["digestMatches"] = dispute.get("digest") == digest
        entry["evidence"] = {"disputed": len(disputed), "testsShown": len(tests)}
        entry["reasons"] = {"agent": [str(d.get("reason") or "") for d in disputed]}
        if not entry["digestMatches"] or not disputed:
            # What the inbox showed is not what acceptance show shows: the person does not act.
            entry["action"] = "dispute-not-shown"
            return None
        judge = getattr(self.clarifier, "judge_dispute", None)
        actor = REVIEWER_ACTOR
        if judge is None:
            decision, reason, entry["judge"] = DISPUTE_FALLBACK, DISPUTE_FALLBACK_REASON, "fallback"
        else:
            task = self.hj("task", "show", "--task", self.task_id) if self.task_id else {}
            try:
                verdict = judge(task or {}, {"disputed": disputed, "tests": tests})
            except Exception as error:  # noqa: BLE001 - the product owner is an agent; its failure is a result
                entry["action"] = "product-owner-failed"
                entry["error"] = f"{type(error).__name__}: {str(error)[:300]}"
                return None
            decision, reason = str(verdict["decision"]), str(verdict["reason"])
            entry["judge"] = "product-owner"
            actor = getattr(self.clarifier, "actor", REVIEWER_ACTOR)
        entry["decision"] = decision
        entry["reasons"]["owner"] = reason
        proc = self.h(
            "acceptance",
            "dispute",
            "--path",
            ".",
            "--run",
            run_id,
            "--decision",
            decision,
            "--digest",
            digest,
            "--rationale",
            reason,
            "--actor",
            actor,
            "--no-continue",
        )
        entry["action"] = f"acceptance dispute {decision} ({actor}) -> {proc.returncode}"
        return self.resume(run_id) if recorded(proc) else None

    def _criteria_count(self) -> int | None:
        """How many acceptance criteria the run's task has (``harness task show``); None when
        the harness does not say, and then the per-criterion check is skipped."""
        if not self.task_id:
            return None
        shown = self.hj("task", "show", "--task", self.task_id) or {}
        task = shown.get("task") if isinstance(shown.get("task"), dict) else shown
        criteria = task.get("acceptanceCriteria") if isinstance(task, dict) else None
        return len(criteria) if isinstance(criteria, list) else None

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
            why = "El plan está registrado y ligado a su digest; las banderas de riesgo están listadas."
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
            why = "La descomposición reparte los requisitos; la evidencia está completa."
        return self._decide_proposal(run_id, "plan", digest, complete, entry, why)

    def _decide_proposal(
        self,
        run_id: str,
        command: str,
        digest: str,
        complete: bool,
        entry: dict[str, Any],
        why: str,
        reject_why: str = "La propuesta está incompleta.",
    ) -> int | None:
        decision = "APPROVE" if complete else "REJECT"
        rationale = why if complete else reject_why
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
            "La opción que recomienda la propuesta.",
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
            "Las reglas de capas inferidas coinciden con el levantamiento de la arquitectura.",
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
            "Este entorno no puede alcanzar los peldaños listados; se continúa sin certificar.",
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
            return self.session(run_id, "La persona pidió cambios:\n" + feedback), None
        code = self.resume(run_id)
        if decision == "APPROVE":
            return code, "approved" if code == 0 else f"approved-closure-exit-{code}"
        return code, None


def _disputed_sources(
    tests: list[dict[str, Any]], disputed: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The frozen test files a dispute names (``path`` or ``path::test``), with their source;
    every file when the dispute names none of them by path."""
    named = {str(d.get("test") or "").split("::", 1)[0] for d in disputed}
    chosen = [t for t in tests if str(t.get("path") or "") in named]
    return [{"path": t.get("path"), "content": t.get("content")} for t in (chosen or tests)]


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
