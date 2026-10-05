"""Discriminating evidence and light mutation (#55, item 5; ``verification.mutation``).

A passing test suite says little when the new tests would pass without the change, or when a
changed line is never exercised. After the mandatory validators passed, VERIFICATION:

1. **classifies the new or changed test files**: each runs on a scratch copy of the workspace in
   which every changed source file is put back to its baseline content while the tests keep
   their new content. A test file that fails there and passes on the change is
   *discriminating*; one that passes before and after is *weak* (``tests.weak``); one that fails
   after the change is *broken* (``tests.broken``);
2. **reverts each changed hunk** of the source, one at a time, in a scratch copy and runs the
   relevant tests (the changed test files, else the mandatory test command). A hunk whose
   reversion no test notices is a ``tests.change-not-exercised`` finding at its line.

The order is deterministic (path, then position), blank and comment-only hunks are not
mutated, and the number of hunks (``maxHunks``) and the time (``maxSeconds``) are capped; what
the cap left out is recorded. The test command comes from the profile's ``mutationCommand``
(``{paths}`` is replaced by the test files); a profile without one is skipped and the record
says so. Under ``enforce`` the findings are HIGH and the validation is mandatory; under
``warn`` they are LOW."""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from governed_harness.capabilities import grants_from_rules
from governed_harness.domain.enums import (
    ActorType,
    FindingSeverity,
    PhaseId,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.models import Actor, ChangeSet, Execution, Finding, PhaseExecution
from governed_harness.ladder.hunks import Hunk, cosmetic, hunks, revert
from governed_harness.orchestration.workspace_ops import Contents, materialized
from governed_harness.runtime import CancellationToken, SafeProcessRunner
from governed_harness.runtime.process_runner import CommandSpec
from governed_harness.validators import ValidatorOutput
from governed_harness.validators.traceability import is_test_file

if TYPE_CHECKING:
    from governed_harness.orchestration.ladder_host import LadderHost

MUTATION_ID = "harness.mutation"
NOT_EXERCISED_RULE = "tests.change-not-exercised"
WEAK_RULE = "tests.weak"
BROKEN_RULE = "tests.broken"
MAX_CLASSIFIED_FILES = 10
_TEST_SEGMENTS = frozenset({"test", "tests", "__tests__", "spec", "specs"})


def _single_file(data: bytes) -> Contents:
    """The recorded content of the one path a materialized copy changes."""

    def content(_path: str) -> bytes | None:
        return data

    return Contents(lambda _path: True, content)


def looks_like_test(path: str, technologies: list[str]) -> bool:
    relative = PurePosixPath(path)
    if is_test_file(relative, technologies):
        return True
    lowered = relative.name.lower()
    return (
        any(part.lower() in _TEST_SEGMENTS for part in relative.parts[:-1])
        or lowered.startswith("test")
        or lowered.endswith(("_test.go", "test.java", "tests.swift", "test.kt"))
    )


class Mutation:
    def __init__(self, ladder: LadderHost) -> None:
        self.ladder = ladder

    @property
    def config(self) -> Any:
        verification = self.ladder.project.verification
        return verification.mutation if verification else None

    @property
    def enabled(self) -> bool:
        return bool(self.config and self.config.enabled)

    def _command(self) -> tuple[str, ...] | None:
        for _profile, verification in self.ladder.profile_verifications():
            if verification.mutation_command:
                return tuple(verification.mutation_command)
        return None

    def _suite(self) -> tuple[str, ...] | None:
        for definition in self.ladder.s.resolved.effective_validators:
            if definition.mandatory and definition.command:
                return tuple(definition.command)
        return None

    def run(
        self,
        execution: Execution,
        phase: PhaseExecution,
        change_set: ChangeSet,
        outputs: list[Any],
    ) -> ValidatorOutput | None:
        if not self.enabled:
            return None
        hub = self.ladder
        results = hub.engine.results
        mandatory_failed = [
            item.result.validator_id
            for item in outputs
            if item.result.mandatory and item.result.status is not ResultStatus.PASSED
        ]
        template = self._command()
        record: dict[str, Any] = {
            "changeSetDigest": change_set.digest,
            "mode": self.config.mode,
            "command": list(template or ()),
        }
        if mandatory_failed or template is None:
            reason = (
                f"not run: mandatory validator(s) did not pass ({', '.join(mandatory_failed)})"
                if mandatory_failed
                else "not run: no selected profile declares a mutationCommand"
            )
            record["skipped"] = reason
            ref = results.record_json(
                execution, PhaseId.VERIFICATION, record, kind="mutation-report", summary=reason
            )
            result = results.record_validation(
                execution,
                validator_id=MUTATION_ID,
                digest=change_set.digest,
                status=ResultStatus.NOT_APPLICABLE,
                kind=ValidationKind.SUCCESS,
                mandatory=False,
                summary=reason,
                evidence_refs=(ref,),
            )
            return ValidatorOutput(result, ())
        technologies = [profile.technology for profile in hub.s.resolved.profiles]
        contents = results.baseline_contents(execution)
        changed = [item for item in change_set.files if item.status != "DELETED"]
        tests = sorted(item.path for item in changed if looks_like_test(item.path, technologies))
        sources = sorted(
            item.path for item in changed if not looks_like_test(item.path, technologies)
        )
        findings: list[Finding] = []
        severity = FindingSeverity.HIGH if self.config.mode == "enforce" else FindingSeverity.LOW
        started = time.monotonic()
        deadline = started + self.config.time_limit
        workspace = hub.s.paths.workspace
        scratch = hub.s.paths.harness_dir / "tmp"
        classification: list[dict[str, Any]] = []
        if tests and contents is not None:
            with materialized(workspace, scratch, contents, sources) as before:
                for path in tests[:MAX_CLASSIFIED_FILES]:
                    status_before = (
                        self._run(execution, template, [path], before) if before else None
                    )
                    classification.append({"path": path, "before": status_before})
            current = self._run(execution, template, tests[:MAX_CLASSIFIED_FILES], workspace)
            for item in classification:
                before_status = item["before"]
                if current is not ResultStatus.PASSED:
                    item["class"] = "broken"
                elif before_status is ResultStatus.FAILED:
                    item["class"] = "discriminating"
                elif before_status is ResultStatus.PASSED:
                    item["class"] = "weak"
                else:
                    item["class"] = "unknown"
                item["before"] = before_status.value if before_status else None
                item["after"] = current.value if current else None
                if item["class"] in {"weak", "broken"}:
                    findings.append(
                        results.record_finding(
                            execution,
                            validator_id=MUTATION_ID,
                            rule_id=WEAK_RULE if item["class"] == "weak" else BROKEN_RULE,
                            category="test-quality",
                            severity=FindingSeverity.LOW if item["class"] == "weak" else severity,
                            message=(
                                f"{item['path']} passes on the baseline too: it does not show "
                                "the change"
                                if item["class"] == "weak"
                                else f"{item['path']} fails on the change"
                            ),
                            path=item["path"],
                            recommendation=(
                                "Add an assertion that fails without the change."
                                if item["class"] == "weak"
                                else "Fix the test or the change; the suite may not collect it."
                            ),
                        )
                    )
        relevant = tests or None
        suite = None if relevant else self._suite()
        mutated: list[dict[str, Any]] = []
        skipped_hunks: list[str] = []
        candidates: list[tuple[Hunk, str, str]] = []
        for path in sources:
            if contents is None:
                break
            data = contents.content(path) if contents.existed(path) else b""
            target = workspace / path
            if data is None or not target.is_file():
                continue
            try:
                before_text = data.decode("utf-8")
                after_text = target.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for hunk in hunks(path, before_text, after_text):
                if not cosmetic(hunk, before_text, after_text):
                    candidates.append((hunk, before_text, after_text))
        for hunk, before_text, after_text in candidates:
            if len(mutated) >= self.config.hunk_limit or time.monotonic() >= deadline:
                skipped_hunks.append(hunk.label)
                continue
            reverted = revert(hunk, before_text, after_text).encode("utf-8")
            single = _single_file(reverted)
            with materialized(workspace, scratch, single, [hunk.path]) as copy:
                if copy is None:
                    status = None
                elif relevant:
                    status = self._run(execution, template, relevant, copy)
                elif suite:
                    status = self._run(execution, suite, [], copy)
                else:
                    status = None
            exercised = status is ResultStatus.FAILED
            mutated.append(
                {
                    **hunk.as_dict(),
                    "status": status.value if status else None,
                    "exercised": exercised,
                }
            )
            if status is ResultStatus.PASSED:
                findings.append(
                    results.record_finding(
                        execution,
                        validator_id=MUTATION_ID,
                        rule_id=NOT_EXERCISED_RULE,
                        category="test-quality",
                        severity=severity,
                        message=(
                            f"Reverting the change at {hunk.path}:{hunk.after_start + 1} "
                            "leaves every relevant test passing: no test exercises it"
                        ),
                        path=hunk.path,
                        line=hunk.after_start + 1,
                        recommendation="Add a test that fails without this change.",
                    )
                )
        record.update(
            {
                "testFiles": classification,
                "hunks": mutated,
                "hunksLeftOut": skipped_hunks,
                "maxHunks": self.config.hunk_limit,
                "maxSeconds": self.config.time_limit,
                "elapsedSeconds": round(time.monotonic() - started, 3),
            }
        )
        not_exercised = [item for item in mutated if item["status"] == ResultStatus.PASSED.value]
        summary = (
            f"{len(mutated)} hunk(s) reverted, {len(not_exercised)} not exercised; "
            f"{sum(1 for item in classification if item.get('class') == 'discriminating')} of "
            f"{len(classification)} new test file(s) discriminating"
            + (f"; {len(skipped_hunks)} hunk(s) left out by the limits" if skipped_hunks else "")
        )
        ref = results.record_json(
            execution, PhaseId.VERIFICATION, record, kind="mutation-report", summary=summary
        )
        blocking = self.config.mode == "enforce" and any(
            item.severity is FindingSeverity.HIGH for item in findings
        )
        result = results.record_validation(
            execution,
            validator_id=MUTATION_ID,
            digest=change_set.digest,
            status=ResultStatus.FAILED if blocking else ResultStatus.PASSED,
            kind=ValidationKind.VALIDATION_FAILURE if blocking else ValidationKind.SUCCESS,
            mandatory=self.config.mode == "enforce",
            summary=summary,
            findings=tuple(findings),
            evidence_refs=(ref,),
        )
        return ValidatorOutput(result, tuple(findings))

    def _run(
        self,
        execution: Execution,
        template: tuple[str, ...],
        paths: list[str],
        root: Path,
    ) -> ResultStatus | None:
        argv: list[str] = []
        for item in template:
            if item == "{paths}":
                argv.extend(paths)
            else:
                argv.append(item)
        hub = self.ladder
        engine = hub.engine
        actor = Actor(actor_type=ActorType.TOOL, actor_id=f"validator.{MUTATION_ID}", version="1")
        try:
            result = SafeProcessRunner(root).run(
                CommandSpec(
                    argv=tuple(argv),
                    cwd=root,
                    timeout_seconds=float(engine._bounded_timeout(600)),
                    max_output_bytes=hub.project.runtime.max_output_bytes,
                ),
                actor=actor,
                grants=grants_from_rules(
                    execution.execution_id, actor, hub.s.resolved.effective_capabilities
                ),
                cancellation=CancellationToken(lambda: engine.is_cancelled(execution.execution_id)),
            )
        except Exception:  # noqa: BLE001 - a command that cannot start gives no evidence
            return None
        if result.status in {ResultStatus.PASSED, ResultStatus.FAILED}:
            return result.status
        return None


__all__ = [
    "BROKEN_RULE",
    "MUTATION_ID",
    "NOT_EXERCISED_RULE",
    "WEAK_RULE",
    "Mutation",
    "looks_like_test",
]
