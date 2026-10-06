"""Standards, principles and testing strategy in the run (#56).

``AgentResults`` delegates to this class; a project without the ``standards``, ``testing``,
``architecture`` or ``verification.principles`` settings never reaches it.

Token cost rules, all deterministic:

* the implement request carries only the standards cards that apply to the files the call works
  on, compact, selected once per digest of (packs, paths) and reused from the cache;
* the review call carries the cards no tool verifies and the principles checklist in its own
  request: no extra call;
* the TDD evidence (red, green, refactor) is measured by running the tests, not by asking."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.checks import DiffFile, Issue, is_test_path
from governed_harness.checks.principles import PRINCIPLES_CHECKLIST
from governed_harness.configuration.engineering import (
    PrinciplesConfig,
    StandardsConfig,
    TestingConfig,
)
from governed_harness.domain.enums import ActorType, FindingSeverity, PhaseId, ResultStatus
from governed_harness.domain.models import Actor, ChangeSet, Execution, PhaseExecution, Task
from governed_harness.orchestration.engine_types import Strategy as Strategy
from governed_harness.orchestration.workspace_ops import materialized
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.runtime.process_runner import CommandSpec
from governed_harness.standards import (
    ProjectStandards,
    WorkspaceFacts,
    bdd_frameworks,
    builtin_pack,
    cards_payload,
    checklist_payload,
    detect_from_facts,
    project_standards,
    select_cards,
    selection_digest,
)
from governed_harness.validators import ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

TDD_ID = "harness.tdd"
SETUP_FLAG = "projectsetup"


def detect_testing(workspace: Path, technologies: tuple[str, ...] = ()) -> Strategy:
    """``bdd`` when the repository has feature files or a BDD framework, ``conventional``
    when it has tests, else ``unknown`` (asked in INTENT under ``intake.projectSetup``)."""
    facts = WorkspaceFacts.read(workspace)
    packs = [builtin_pack(item) for item in detect_from_facts(facts, technologies)]
    frameworks = bdd_frameworks(packs, facts)
    if frameworks:
        return Strategy("bdd", "detected", tuple(frameworks))
    if any(is_test_path(item) for item in facts.files):
        return Strategy("conventional", "detected")
    return Strategy("unknown", "unknown")


class Engineering:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results
        self._standards: ProjectStandards | None = None
        self._detected: Strategy | None = None
        """The detected strategy, read once per command (the workspace is walked once)."""

    # ----- settings -----------------------------------------------------------------------------
    @property
    def standards_config(self) -> StandardsConfig | None:
        return self.results.project.standards

    @property
    def principles_config(self) -> PrinciplesConfig | None:
        verification = self.results.project.verification
        return verification.principles if verification else None

    @property
    def testing_config(self) -> TestingConfig | None:
        return self.results.project.testing

    @property
    def configured(self) -> bool:
        project = self.results.project
        return any(
            (
                project.standards is not None,
                project.testing is not None,
                project.architecture is not None,
                self.principles_config is not None,
                bool(project.intake and project.intake.project_setup is not None),
            )
        )

    def technologies(self) -> tuple[str, ...]:
        return tuple(item.technology for item in self.results.s.resolved.profiles)

    def standards(self) -> ProjectStandards | None:
        config = self.standards_config
        if config is None:
            return None
        if self._standards is None:
            packs = config.packs
            answered = self.setup_record(self.results.project.project_id).get("standards")
            if (packs is None or "auto" in packs) and isinstance(answered, list) and answered:
                packs = tuple(str(item) for item in answered)
            self._standards = project_standards(
                self.results.s.paths.workspace,
                packs=packs,
                overrides=config.overrides_path,
                disabled=config.disabled or (),
                technologies=self.technologies(),
            )
        return self._standards

    # ----- project setup answers -------------------------------------------------------------
    def setup_record(self, project_id: str) -> dict[str, Any]:
        value = self.results.flag_json(f"{SETUP_FLAG}:{project_id}")
        return value if isinstance(value, dict) else {}

    # ----- testing strategy ------------------------------------------------------------------
    def strategy(self, project_id: str) -> Strategy:
        """The testing strategy: the configuration, else a person's answer (rule ``P1``), else,
        only under ``testing.strategy: auto``, what the repository shows. Without the
        ``testing`` section nothing is detected: the 1.1 behaviour (``conventional``)."""
        config = self.testing_config
        if config is not None and config.strategy not in {None, "auto"}:
            return Strategy(str(config.strategy), "configuration")
        answered = self.setup_record(project_id).get("testing")
        if isinstance(answered, str) and answered in {"tdd", "bdd", "conventional"}:
            return Strategy(answered, "project-setup")
        if config is None:
            return Strategy("conventional", "off")
        if self._detected is None:
            self._detected = detect_testing(self.results.s.paths.workspace, self.technologies())
        return self._detected

    def features_directory(self) -> str:
        config = self.testing_config
        return config.features_path if config is not None else "features"

    def bdd_command(self) -> tuple[str, ...]:
        config = self.testing_config
        if config is not None and config.bdd_command:
            return config.bdd_command
        for technology in self._languages():
            runner = builtin_pack(technology).runner
            if runner.bdd:
                return runner.bdd
        return ("python", "-m", "behave")

    def test_command(self, files: list[str]) -> tuple[str, ...]:
        """The command that runs ``files`` (TDD red check): ``testing.testCommand`` with the
        files appended, else the pack runner of the files' language."""
        config = self.testing_config
        if config is not None and config.test_command:
            return (*config.test_command, *files)
        for technology in self._languages(files):
            runner = builtin_pack(technology).runner
            if runner.test:
                return (*runner.test, *files) if runner.test_files else runner.test
        return ("python", "-m", "pytest", "-q", *files)

    def _languages(self, files: list[str] | None = None) -> list[str]:
        standards = self.standards()
        ids = [item.pack_id for item in standards.packs] if standards else []
        if not ids:
            ids = [item for item in self.technologies() if item in {"python", "node"}]
        if files:
            by_suffix = {
                ".py": "python",
                ".go": "go",
                ".rs": "rust",
                ".java": "java",
                ".kt": "kotlin",
                ".cs": "csharp",
                ".php": "php",
                ".rb": "ruby",
                ".swift": "swift",
            }
            first = [
                by_suffix[Path(item).suffix] for item in files if Path(item).suffix in by_suffix
            ]
            ids = [*first, *ids]
        return list(dict.fromkeys(item for item in ids if item))

    # ----- the implement request --------------------------------------------------------------
    def candidate_paths(self, task: Task, changed: list[str], extra: list[str]) -> list[str]:
        from governed_harness.agents.context_manifest import referenced_paths

        workspace = self.results.s.paths.workspace
        paths = [
            *[str(item) for item in task.metadata.get("ownedPaths") or []],
            *referenced_paths(task, workspace),
            *(patch.path for patch in task.implementation.patches),
            *changed,
            *extra,
        ]
        return list(dict.fromkeys(item.replace("\\", "/") for item in paths if item))

    def implement_extra(
        self,
        execution: Execution,
        phase: PhaseExecution,
        task: Task,
        paths: list[str],
    ) -> tuple[dict[str, Any], str]:
        """The ``standards`` and ``testing`` blocks of the implement request and the sentence
        the instructions gain for them."""
        extra: dict[str, Any] = {}
        notes: list[str] = []
        standards = self.standards()
        config = self.standards_config
        if standards is not None and config is not None and config.cards_enabled:
            payload = self._cards(execution, phase, standards, paths, config.card_limit)
            if payload["cards"]:
                extra["standards"] = payload
                notes.append(
                    "Follow the standards cards for the files they apply to; listed exceptions "
                    "are allowed."
                )
        if self.testing_config is not None or self.setup_record(execution.project_id):
            strategy = self.strategy(execution.project_id)
            if strategy.strategy in {"tdd", "bdd"}:
                extra["testing"] = strategy.as_dict()
            if strategy.strategy == "tdd":
                notes.append(
                    "Work test-first (TDD): write or change the tests for each criterion first, "
                    "make them pass with the smallest change, then refactor with every test "
                    "green. The harness runs your new tests on the code before your change and "
                    "they must fail there."
                )
            elif strategy.strategy == "bdd":
                notes.append(
                    "Behaviour-driven (BDD): the approved feature files are frozen; write the "
                    "step definitions and the code that make their scenarios pass."
                )
        architecture = self.results.architecture.request_extra()
        if architecture is not None:
            extra["architecture"] = architecture
            notes.append(
                "Respect the architecture block: a file of one layer may import only the "
                "layers listed as allowed for it."
            )
        return extra, " ".join(notes)

    def _cards(
        self,
        execution: Execution,
        phase: PhaseExecution,
        standards: ProjectStandards,
        paths: list[str],
        limit: int,
    ) -> dict[str, Any]:
        digest = selection_digest(standards, paths, limit)
        key = f"standardscards:{execution.project_id}:{digest}"
        cached = self.results.flag_json(key)
        reused = isinstance(cached, dict) and isinstance(cached.get("cards"), list)
        if reused:
            payload: dict[str, Any] = cached
        else:
            cards = select_cards(standards, paths, limit)
            payload = cards_payload(cards, digest, packs=[item.pack_id for item in standards.packs])
            self.results.set_flag_json(key, payload)
        self.results.s.events.append(
            execution.execution_id,
            "standards.cards.selected",
            {
                "digest": digest,
                "packs": payload["packs"],
                "cards": [item["id"] for item in payload["cards"]],
                "paths": len(paths),
                "reused": reused,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return payload

    # ----- the review call -------------------------------------------------------------------
    def review_extra(
        self, execution: Execution, change_set: ChangeSet
    ) -> tuple[dict[str, Any], str]:
        """The checklist the existing review call gains: the cards no tool verifies, for the
        files the ChangeSet changed, and the principles checklist. No extra call."""
        checklist: dict[str, Any] = {}
        standards = self.standards()
        config = self.standards_config
        paths = [item.path for item in change_set.files]
        if standards is not None and config is not None and config.cards_enabled:
            cards = select_cards(standards, paths, config.card_limit, review_only=True)
            if cards:
                checklist["standards"] = checklist_payload(cards)
        principles = self.principles_config
        if principles is not None and principles.enabled and principles.checklist is not False:
            checklist["principles"] = [
                {"id": rule, "check": text} for rule, text in PRINCIPLES_CHECKLIST
            ]
        if not checklist:
            return {}, ""
        return {"checklist": checklist}, (
            "Also check each checklist item against the ChangeSet; report a violation as a "
            "finding whose rule is the item id."
        )

    # ----- TDD evidence (VERIFICATION) ---------------------------------------------------------
    def tdd_validation(
        self,
        execution: Execution,
        change_set: ChangeSet,
        diff: list[DiffFile],
        outputs_so_far: list[ValidatorOutput],
    ) -> ValidatorOutput | None:
        if self.testing_config is None and not self.setup_record(execution.project_id):
            return None
        if self.strategy(execution.project_id).strategy != "tdd":
            return None
        friction = self.results.engine.friction
        if friction.active and friction.tests_exempt(execution, change_set, "TDD evidence"):
            # friction.changeTypes (#58): a documentation or configuration change.
            return None
        results = self.results
        started = datetime.now(UTC)
        tests = [item.path for item in diff if not item.is_deleted and is_test_path(item.path)]
        sources = [item.path for item in diff if not is_test_path(item.path)]
        issues: list[Issue] = []
        record: dict[str, Any] = {"tests": tests, "sources": sources}
        if sources and not tests:
            issues.append(
                Issue(
                    rule_id="tdd.no-tests",
                    severity=FindingSeverity.HIGH,
                    message="The change modifies code but adds or changes no test (TDD)",
                    category="tests",
                    recommendation="Write the failing test first, then the code.",
                )
            )
            record["red"] = "NO_TESTS"
        elif tests and sources:
            red = self._red(execution, tests, sources)
            record["red"] = red
            if red["status"] == ResultStatus.PASSED.value:
                issues.append(
                    Issue(
                        rule_id="tdd.not-red",
                        severity=FindingSeverity.HIGH,
                        message=(
                            f"The tests the change adds ({', '.join(tests[:5])}) already pass on "
                            "the code before the change: they were not written first"
                        ),
                        category="tests",
                        recommendation="Write a test that fails without the change.",
                    )
                )
            elif red["status"] != ResultStatus.FAILED.value:
                issues.append(
                    Issue(
                        rule_id="tdd.red-unavailable",
                        severity=FindingSeverity.INFO,
                        message=f"The red step could not be measured ({red['status']})",
                        category="tests",
                    )
                )
        else:
            record["red"] = "NOT_APPLICABLE"
        latest = results.engine._latest_validations(execution.execution_id, change_set.digest)
        green = [
            item.validator_id
            for item in latest
            if item.mandatory and item.status is not ResultStatus.PASSED
        ]
        record["green"] = {"status": "PASSED" if not green else "FAILED", "failing": green}
        refactor_checks = {"harness.principles", "harness.architecture", "harness.layers"}
        refactor_blocking = [
            output.result.validator_id
            for output in outputs_so_far
            if output.result.validator_id in refactor_checks
            and output.result.status is not ResultStatus.PASSED
        ]
        record["refactor"] = {
            "status": "PASSED" if not refactor_blocking and not green else "FAILED",
            "checks": sorted(refactor_checks),
            "failing": refactor_blocking,
        }
        ref = results.record_json(
            execution,
            PhaseId.VERIFICATION,
            record,
            kind="tdd-evidence",
            summary=(
                f"TDD: red {record['red'] if isinstance(record['red'], str) else record['red']['status']}, "
                f"green {record['green']['status']}, refactor {record['refactor']['status']}"
            ),
        )
        output = results.verification._output(
            execution,
            change_set,
            TDD_ID,
            "enforce",
            issues,
            "test-driven development (red, green, refactor)",
            started=started,
            extra_evidence=(ref,),
            report={"tdd": record},
        )
        return output

    def _red(self, execution: Execution, tests: list[str], sources: list[str]) -> dict[str, Any]:
        """Run the changed tests on the workspace with the changed sources reverted to the
        baseline: they must fail there."""
        results = self.results
        engine = results.engine
        contents = results.baseline_contents(execution)
        if contents is None:
            return {"status": ResultStatus.NOT_APPLICABLE.value, "reason": "no baseline"}
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{TDD_ID}", version="1")
        grants = grants_from_rules(
            execution.execution_id, actor, results.s.resolved.effective_capabilities
        )
        argv = self.test_command(tests)
        workspace = results.s.paths.workspace
        with materialized(
            workspace, results.s.paths.harness_dir / "tmp", contents, sources
        ) as copy:
            if copy is None:
                return {"status": ResultStatus.NOT_APPLICABLE.value, "reason": "not revertible"}
            outcome = SafeProcessRunner(copy).run(
                CommandSpec(
                    argv=argv,
                    cwd=copy,
                    timeout_seconds=float(engine._bounded_timeout(900)),
                    max_output_bytes=results.project.runtime.max_output_bytes,
                ),
                actor=actor,
                grants=grants,
                cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
            )
        return {
            "status": outcome.status.value,
            "exitCode": outcome.exit_code,
            "command": list(argv),
            "stdoutRef": results.s.artifacts.put(outcome.stdout, media_type="text/plain").uri,
        }


__all__ = ["SETUP_FLAG", "TDD_ID", "Engineering", "Strategy", "detect_testing"]
