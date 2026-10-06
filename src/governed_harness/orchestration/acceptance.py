"""Independent, frozen acceptance tests (#52, N4).

The gate checked the agent's work with the agent's own tests: approved steps of a small model
covered some sections at 31-40 %, and a single systematic defect produced hundreds of errors
while the agent's fifty tests passed. Under ``verification.acceptanceTests.mode: agent``, in
SPECIFICATION a separate call (kind ``acceptance``, read-only, the provider and model of
``acceptanceTests.author``) writes acceptance tests from the criteria and requirements only and
returns them as files. The harness does not write them yet: a person approves them, bound to
their digest (``harness acceptance decide``). On approval the harness writes them under
``acceptanceTests.directory``, records their digests (frozen) and runs them on the workspace as it
is before the change: a test that already passes there does not test the change (a MEDIUM
finding). Every later VERIFICATION checks that the frozen files are unchanged (a modified or
deleted file is a HIGH finding) and that they pass; the implement request names them as frozen.
A proposal without tests is recorded and the run continues.

Since #56, under ``testing.strategy: bdd`` the same call writes the criteria as Gherkin feature
files (under ``testing.featuresDirectory``, default ``features``) instead of pytest files: a
person approves the scenarios, the harness freezes them and runs the BDD runner of the project
(``testing.bddCommand`` or the standards pack's runner) before the change (it must fail: the
steps are not defined yet) and in every VERIFICATION; the implement request asks for the step
definitions."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.capabilities.authorizer import contained_path
from governed_harness.domain.enums import (
    ActorType,
    DecisionKind,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.domain.models import (
    Actor,
    ChangeSet,
    Execution,
    Finding,
    PhaseExecution,
    Task,
)
from governed_harness.evidence import sha256_json
from governed_harness.evidence.hashing import sha256_bytes
from governed_harness.orchestration.engine_types import PhaseOutcome
from governed_harness.runtime import CancellationToken
from governed_harness.runtime.process_runner import CommandSpec
from governed_harness.validators import CommandValidator, ValidatorOutput

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

ACCEPTANCE_ID = "harness.acceptance-tests"
MAX_FILES = 20
MAX_FILE_BYTES = 200_000


def _check_feature(index: int, path: str, content: Any, directory: str) -> None:
    """A feature file lives under ``directory`` and has a Feature and a Scenario."""
    if (
        not path.startswith(directory + "/")
        or ".." in PurePosixPath(path).parts
        or not path.endswith(".feature")
    ):
        raise ValueError(f"feature file {index} must be {directory}/<name>.feature, got {path!r}")
    if not isinstance(content, str) or "Feature:" not in content or ("Scenario" not in content):
        raise ValueError(f"feature file {path} needs a Feature and a Scenario")


def _check_test_path(index: int, path: str, directory: str) -> None:
    """A pytest file lives under ``directory`` and is named ``test_<name>.py``."""
    if (
        not path.startswith(directory + "/")
        or ".." in PurePosixPath(path).parts
        or not PurePosixPath(path).name.startswith("test_")
        or not path.endswith(".py")
    ):
        raise ValueError(f"test file {index} must be {directory}/test_<name>.py, got {path!r}")


def _test_file(index: int, entry: Any, directory: str, gherkin: bool) -> dict[str, str]:
    """One file of the answer, checked: its place, its name and its content."""
    if not isinstance(entry, dict):
        raise ValueError(f"test file {index} is not an object")
    path = str(entry.get("path") or "")
    content = entry.get("content")
    if gherkin:
        _check_feature(index, path, content, directory)
    else:
        _check_test_path(index, path, directory)
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"test file {path} has no content")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"test file {path} is larger than {MAX_FILE_BYTES} bytes")
    return {"path": path, "content": content}


def validate_tests(
    result: dict[str, Any], directory: str, *, gherkin: bool = False
) -> list[dict[str, str]]:
    raw = result.get("tests")
    if not isinstance(raw, list):
        raise ValueError("the acceptance result needs a 'tests' list")
    if len(raw) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} acceptance test files are allowed")
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        item = _test_file(index, entry, directory, gherkin)
        if item["path"] in seen:
            raise ValueError(f"test file {item['path']} appears twice")
        seen.add(item["path"])
        files.append(item)
    return files


def _free_path(workspace: Path, path: str, execution_id: str) -> str:
    """``path`` when no file is there; otherwise the same name with the run's suffix (and a
    counter if that is taken too), in the same directory and with the same extension, so the
    runner still collects it (``test_<name>_<run>.py``, ``<name>_<run>.feature``)."""
    if not (workspace / path).exists():
        return path
    pure = PurePosixPath(path)
    suffix = execution_id.rsplit("_", 1)[-1][-8:]
    candidate = str(pure.with_name(f"{pure.stem}_{suffix}{pure.suffix}"))
    number = 2
    while (workspace / candidate).exists():
        candidate = str(pure.with_name(f"{pure.stem}_{suffix}_{number}{pure.suffix}"))
        number += 1
    return candidate


class AcceptanceTests:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def config(self) -> Any:
        verification = self.results.project.verification
        return verification.acceptance_tests if verification else None

    @property
    def bdd(self) -> bool:
        """``testing.strategy: bdd`` (#56): the acceptance tests are Gherkin scenarios."""
        engineering = self.results.engineering
        if not engineering.configured:
            return False
        return engineering.strategy(self.results.project.project_id).strategy == "bdd"

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.config.enabled) or self.bdd

    def _command(self, paths: list[str]) -> tuple[str, ...]:
        if self.bdd:
            return self.results.engineering.bdd_command()
        return ("python", "-m", "pytest", "-q", *paths)

    def _key(self, execution: Execution) -> str:
        return f"acceptance:{execution.execution_id}"

    def state(self, execution: Execution) -> dict[str, Any] | None:
        value = self.results.flag_json(self._key(execution))
        return value if isinstance(value, dict) else None

    # ----- SPECIFICATION -------------------------------------------------------------------
    def propose(
        self, execution: Execution, phase: PhaseExecution, task: Task
    ) -> PhaseOutcome | None:
        if not self.enabled:
            return None
        state = self.state(execution)
        if state is not None:
            if state["status"] == "PROPOSED":
                return PhaseOutcome(
                    ResultStatus.BLOCKED,
                    f"{len(state['tests'])} acceptance test file(s) wait for a person: harness "
                    f"acceptance decide --run {execution.execution_id} --decision APPROVE "
                    f"--digest {state['digest']}",
                    (state["ref"],),
                )
            return None
        gherkin = self.bdd
        directory = self.results.engineering.features_directory() if gherkin else self.config.path
        outcome = self.results.call_agent(
            execution,
            phase,
            "acceptance",
            {"directory": directory, **({"format": "gherkin"} if gherkin else {})},
            task=task,
            instruction_values={
                "directory": directory,
                "format": "gherkin" if gherkin else "pytest",
            },
        )
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The acceptance-test call did not answer ({outcome.status}): {outcome.summary}",
                outcome.evidence_refs,
            )
        try:
            tests = validate_tests(outcome.result, directory, gherkin=gherkin)
        except ValueError as error:
            self.results.record_finding(
                execution,
                validator_id=ACCEPTANCE_ID,
                rule_id="acceptance.malformed",
                category="acceptance-tests",
                severity=FindingSeverity.HIGH,
                message=f"The proposed acceptance tests are not valid: {error}",
                evidence_refs=outcome.evidence_refs,
            )
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The proposed acceptance tests are not valid: {error}",
                outcome.evidence_refs,
            )
        digest = sha256_json({"tests": tests})
        status = "PROPOSED" if tests else "EMPTY"
        record = {
            "status": status,
            "digest": digest,
            "directory": directory,
            "format": "gherkin" if gherkin else "pytest",
            "tests": tests,
            "invocationId": outcome.invocation_id,
        }
        ref = self.results.record_json(
            execution,
            PhaseId.SPECIFICATION,
            record,
            kind="acceptance-tests-proposal",
            summary=f"Proposed acceptance tests: {len(tests)} file(s)",
            supports=tuple(item.criterion_id for item in task.acceptance_criteria),
        )
        self.results.set_flag_json(self._key(execution), {**record, "ref": ref})
        self.results.s.events.append(
            execution.execution_id,
            "acceptance.tests.proposed",
            {"digest": digest, "files": [item["path"] for item in tests], "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        if not tests:
            return None
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"{len(tests)} acceptance test file(s) wait for a person: harness acceptance decide "
            f"--run {execution.execution_id} --decision APPROVE --digest {digest}",
            (ref,),
        )

    def decide(
        self,
        execution: Execution,
        *,
        decision: DecisionKind,
        digest: str,
        actor: Actor,
        rationale: str,
    ) -> dict[str, Any]:
        """Approve (write and freeze) or reject the proposed acceptance tests."""
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a person can decide acceptance tests")
        if decision not in {DecisionKind.APPROVE, DecisionKind.REJECT}:
            raise PolicyViolationError("acceptance tests are approved or rejected")
        state = self.state(execution)
        if not state or state.get("status") != "PROPOSED":
            raise NotFoundError(f"run {execution.execution_id} has no proposed acceptance tests")
        if state["digest"] != digest:
            raise PolicyViolationError("the digest does not match the proposed acceptance tests")
        frozen: dict[str, str] = {}
        renamed: dict[str, str] = {}
        if decision is DecisionKind.APPROVE:
            frozen, renamed = self._write(execution, state["tests"])
        status = "APPROVED" if decision is DecisionKind.APPROVE else "REJECTED"
        state = {
            **state,
            "status": status,
            "frozen": frozen,
            "decidedBy": actor.actor_id,
            "rationale": rationale,
        }
        payload: dict[str, Any] = {"decision": decision.value, "digest": digest, "frozen": frozen}
        if renamed:
            state["renamed"] = renamed
            payload["renamed"] = renamed
        self.results.set_flag_json(self._key(execution), state)
        self.results.s.events.append(
            execution.execution_id, "acceptance.tests.decided", payload, actor=actor
        )
        if frozen:
            self._fail_before(execution, list(frozen))
            state = self.state(execution) or state
        return state

    def _write(
        self, execution: Execution, tests: list[dict[str, str]]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Write the approved files and return their digests (frozen) and the proposed paths
        written under another name. A path where a file already exists (the frozen acceptance
        test of an earlier run in the same workspace, or any file of the project) is never
        overwritten (#82): the file gets a run-unique name next to it, so no earlier run's
        frozen file changes and no agent is blamed for a test that disappeared."""
        workspace = self.results.s.paths.workspace
        frozen: dict[str, str] = {}
        renamed: dict[str, str] = {}
        for item in tests:
            path = _free_path(workspace, item["path"], execution.execution_id)
            if path != item["path"]:
                renamed[item["path"]] = path
            target = contained_path(workspace, Path(path))
            target.parent.mkdir(parents=True, exist_ok=True)
            data = item["content"].encode("utf-8")
            target.write_bytes(data)
            frozen[path] = sha256_bytes(data)
        return frozen, renamed

    def _fail_before(self, execution: Execution, paths: list[str]) -> None:
        """The frozen tests run on the workspace before the change: passing there means they
        do not test the change."""
        results = self.results
        engine = results.engine
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{ACCEPTANCE_ID}", version="1")
        process = engine._runner(execution).run(
            CommandSpec(
                argv=self._command(paths),
                cwd=results.s.paths.workspace,
                timeout_seconds=900.0,
                max_output_bytes=results.project.runtime.max_output_bytes,
            ),
            actor=actor,
            grants=grants_from_rules(
                execution.execution_id, actor, results.s.resolved.effective_capabilities
            ),
            cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
        )
        ref = results.record_json(
            execution,
            PhaseId.SPECIFICATION,
            {
                "tests": paths,
                "status": process.status.value,
                "exitCode": process.exit_code,
                "stdoutRef": results.s.artifacts.put(process.stdout, media_type="text/plain").uri,
            },
            kind="acceptance-fail-before",
            summary=f"Acceptance tests before the change: {process.status.value}",
        )
        state = self.state(execution) or {}
        state["failBefore"] = {"status": process.status.value, "ref": ref}
        if process.status is ResultStatus.PASSED:
            finding = results.record_finding(
                execution,
                validator_id=ACCEPTANCE_ID,
                rule_id="acceptance.passes-before",
                category="acceptance-tests",
                severity=FindingSeverity.MEDIUM,
                message=(
                    "The approved acceptance tests already pass before the change; they do not "
                    "test the new behaviour"
                ),
                evidence_refs=(ref,),
            )
            state["failBeforeFinding"] = finding.finding_id
        results.set_flag_json(self._key(execution), state)

    # ----- IMPLEMENTATION and VERIFICATION --------------------------------------------------
    def request_extra(self, execution: Execution) -> dict[str, Any] | None:
        state = self.state(execution)
        if not state or state.get("status") != "APPROVED" or not state.get("frozen"):
            return None
        if state.get("format") == "gherkin":
            return {
                "paths": sorted(state["frozen"]),
                "frozen": True,
                "format": "gherkin",
                "runner": list(self._command([])),
                "note": "Write the step definitions and the code that make these scenarios "
                "pass; do not modify or delete the feature files.",
            }
        return {
            "paths": sorted(state["frozen"]),
            "frozen": True,
            "note": "Make these tests pass; do not modify or delete them.",
        }

    def validation(self, execution: Execution, change_set: ChangeSet) -> ValidatorOutput | None:
        state = self.state(execution)
        if not self.enabled or not state or state.get("status") != "APPROVED":
            return None
        frozen: dict[str, str] = state.get("frozen") or {}
        if not frozen:
            return None
        results = self.results
        workspace = results.s.paths.workspace
        findings: list[Finding] = []
        for path, digest in sorted(frozen.items()):
            target = workspace / path
            current = sha256_bytes(target.read_bytes()) if target.is_file() else None
            if current != digest:
                findings.append(
                    results.record_finding(
                        execution,
                        validator_id=ACCEPTANCE_ID,
                        rule_id="acceptance.modified",
                        category="acceptance-tests",
                        severity=FindingSeverity.HIGH,
                        message=(
                            f"The frozen acceptance test {path} was "
                            + ("deleted" if current is None else "modified")
                        ),
                        path=path,
                        recommendation="Restore the approved acceptance test; change the code.",
                        introduced=True,
                    )
                )
        engine = results.engine
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{ACCEPTANCE_ID}", version="1")
        from governed_harness.configuration.models import ValidatorDefinition
        from governed_harness.validators import ValidationContext

        output = CommandValidator(ACCEPTANCE_ID).execute(
            ValidationContext(
                execution_id=execution.execution_id,
                workspace=workspace,
                task=engine.run_task(execution),
                change_set=change_set,
                definition=engine._bounded_definition(
                    ValidatorDefinition(
                        id=ACCEPTANCE_ID,
                        command=self._command(sorted(frozen)),
                        mandatory=True,
                    )
                ),
                grants=grants_from_rules(
                    execution.execution_id, actor, results.s.resolved.effective_capabilities
                ),
                artifact_store=results.s.artifacts,
                process_runner=engine._runner(execution),
                provenance=engine._provenance(execution).model_copy(update={"actor": actor}),
                cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
                max_output_bytes=results.project.runtime.max_output_bytes,
                parse_output=True,
            )
        )
        for tool in output.tool_invocations:
            engine._save_tool(execution, tool)
        for finding in output.findings:
            results.s.state.put(
                "finding",
                finding.finding_id,
                finding,
                execution_id=execution.execution_id,
                project_id=execution.project_id,
            )
            results.s.events.append(
                execution.execution_id,
                "finding.recorded",
                finding.model_dump(mode="json"),
                actor=finding.provenance.actor,
            )
        failed = bool(findings) or output.result.status is not ResultStatus.PASSED
        result = results.record_validation(
            execution,
            validator_id=ACCEPTANCE_ID,
            digest=change_set.digest,
            status=output.result.status if not findings else ResultStatus.FAILED,
            kind=ValidationKind.VALIDATION_FAILURE if failed else ValidationKind.SUCCESS,
            mandatory=True,
            summary=(
                f"frozen acceptance tests: {output.result.summary}"
                + (f"; {len(findings)} frozen file(s) changed" if findings else "")
            ),
            findings=(*findings, *output.findings),
            evidence_refs=output.result.evidence_refs,
        )
        return ValidatorOutput(result, ())


__all__ = ["ACCEPTANCE_ID", "AcceptanceTests", "validate_tests"]
