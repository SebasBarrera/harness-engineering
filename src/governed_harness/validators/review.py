from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from governed_harness.domain.enums import ActorType, FindingSeverity, ResultStatus, ValidationKind
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Actor, Finding, FindingLocation, ValidationResult
from governed_harness.validators.base import ValidationContext, ValidatorOutput


@dataclass(frozen=True)
class ReviewRule:
    rule_id: str
    category: str
    severity: FindingSeverity
    pattern: re.Pattern[str]
    message: str
    recommendation: str


RULES = (
    ReviewRule(
        "review.possible-secret",
        "security",
        FindingSeverity.CRITICAL,
        re.compile(r"(?i)(github_pat_|gh[pousr]_|AKIA[A-Z0-9]{16}|password\s*[:=]\s*['\"][^'\"]{4,})"),
        "The added content appears to contain a credential or secret.",
        "Remove the secret, rotate it when applicable, and use an approved secret handle.",
    ),
    ReviewRule(
        "review.shell-true",
        "security",
        FindingSeverity.HIGH,
        re.compile(r"\bshell\s*=\s*True\b"),
        "A subprocess is executed through a shell.",
        "Use an argv vector with shell disabled and validate every argument.",
    ),
    ReviewRule(
        "review.dynamic-eval",
        "security",
        FindingSeverity.HIGH,
        re.compile(r"\b(?:eval|exec)\s*\("),
        "Dynamic code execution was added.",
        "Replace dynamic execution with a constrained parser or explicit dispatch table.",
    ),
    ReviewRule(
        "review.todo",
        "maintainability",
        FindingSeverity.MEDIUM,
        re.compile(r"\b(?:TODO|FIXME)\b"),
        "A TODO or FIXME was introduced in changed code.",
        "Resolve it now or link it to a tracked, owned follow-up with rationale.",
    ),
)


class IndependentReviewValidator:
    validator_id = "review.independent"

    def execute(self, context: ValidationContext) -> ValidatorOutput:
        actor = Actor(actor_type=ActorType.TOOL, actor_id="validator.independent-review", version="1")
        started = datetime.now(UTC)
        if context.raw_diff is not None:
            diff = context.raw_diff
        else:
            # Backward-compatible fallback for callers that do not provide an
            # ephemeral raw diff.  Persisted evidence may be redacted, so the
            # orchestration engine should always supply raw_diff for security review.
            diff_bytes = context.artifact_store.get(context.change_set.diff_ref)
            diff = diff_bytes.decode("utf-8", "replace")
        findings = list(self._scan(diff, context, actor))
        source_changed = any(
            change.path.startswith(("src/", "lib/", "app/")) for change in context.change_set.files
        )
        tests_changed = any(
            change.path.startswith(("test/", "tests/")) or "/test/" in change.path or "/tests/" in change.path
            for change in context.change_set.files
        )
        if source_changed and not tests_changed:
            findings.append(
                Finding(
                    finding_id=new_id("finding"),
                    execution_id=context.execution_id,
                    validator_id=self.validator_id,
                    rule_id="review.source-without-test-change",
                    category="tests",
                    severity=FindingSeverity.MEDIUM,
                    message="Production source changed without a corresponding test-file change.",
                    recommendation="Confirm existing tests cover the behavior or add a targeted regression test.",
                    evidence_refs=(context.change_set.diff_ref,),
                    introduced=True,
                    provenance=context.provenance.model_copy(update={"actor": actor}),
                )
            )
        report = context.artifact_store.put_json(
            {
                "validatorId": self.validator_id,
                "changeSetDigest": context.change_set.digest,
                "findingIds": [finding.finding_id for finding in findings],
                "rulesEvaluated": [rule.rule_id for rule in RULES]
                + ["review.source-without-test-change"],
            },
            metadata={"kind": "independent-review"},
        )
        result = ValidationResult(
            validation_result_id=new_id("validation"),
            execution_id=context.execution_id,
            validator_id=self.validator_id,
            change_set_digest=context.change_set.digest,
            status=ResultStatus.PASSED,
            kind=ValidationKind.SUCCESS,
            mandatory=True,
            summary=f"Independent review completed with {len(findings)} finding(s)",
            finding_ids=tuple(finding.finding_id for finding in findings),
            evidence_refs=(context.change_set.diff_ref, report.uri),
            started_at=started,
            finished_at=datetime.now(UTC),
            provenance=context.provenance.model_copy(update={"actor": actor}),
        )
        return ValidatorOutput(result, tuple(findings))

    def _scan(self, diff: str, context: ValidationContext, actor: Actor) -> Iterator[Finding]:
        current_path: str | None = None
        current_line = 0
        for raw_line in diff.splitlines():
            if raw_line.startswith("+++ b/"):
                current_path = raw_line[6:]
                continue
            if raw_line.startswith("@@"):
                match = re.search(r"\+(\d+)", raw_line)
                current_line = int(match.group(1)) if match else 0
                continue
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                content = raw_line[1:]
                for rule in RULES:
                    if rule.pattern.search(content):
                        yield Finding(
                            finding_id=new_id("finding"),
                            execution_id=context.execution_id,
                            validator_id=self.validator_id,
                            rule_id=rule.rule_id,
                            category=rule.category,
                            severity=rule.severity,
                            message=rule.message,
                            location=FindingLocation(
                                path=current_path,
                                start_line=current_line or None,
                                end_line=current_line or None,
                            ),
                            evidence_refs=(context.change_set.diff_ref,),
                            recommendation=rule.recommendation,
                            introduced=True,
                            provenance=context.provenance.model_copy(update={"actor": actor}),
                        )
                current_line += 1
            elif raw_line.startswith(" "):
                current_line += 1
