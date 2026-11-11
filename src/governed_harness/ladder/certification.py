"""Certification of a ChangeSet against the verification ladder (#55, item 1).

For each acceptance criterion: the rung it requires (its declaration, else the project's
``defaultLevel``), the rungs its recorded evidence reaches, and a status. Evidence per rung:

* L0: every mandatory validator of the gate passed on the digest (VERIFICATION passed);
* L1: an L1 validator (the unit test command) passed and a test of the workspace names the
  criterion (by its id or by a name its declaration lists), or a frozen acceptance test that
  names it passed;
* L2: the same, for a test under an integration directory with an L2 validator, or an L2 probe;
* L3: a probe linked to the criterion ran on every variant and its assertions held;
* L4: an L4 probe passed, or a deferred verification was closed with passing evidence;
* L5: a person ticked the criterion's manual item in a decision on this digest.

Nothing is credited by omission: a check that did not run, was skipped, was waived or is
unavailable adds no rung. The status of a criterion is ``CERTIFIED`` (the required rung is
reached), ``PENDING`` (it can only be reached by deferred evidence or a manual check that is
not recorded yet), ``WAIVED`` (a person decided in preflight to continue without it) or
``NOT_CERTIFIED``. The run is ``CERTIFIED`` when every criterion is, ``PARTIAL`` when some
criterion is certified or pending, ``NOT_CERTIFIED`` otherwise."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from governed_harness.domain.enums import VerificationLevel
from governed_harness.domain.models import (
    AcceptanceCriterion,
    CertificationStatus,
    CriterionCertification,
    DeferredVerification,
    LevelEvidence,
)

INTEGRATION_DIRECTORIES = frozenset({"integration", "integration_tests", "integration-tests", "it"})


@dataclass(frozen=True)
class NamedTest:
    node_id: str
    path: str

    @property
    def integration(self) -> bool:
        return any(part in INTEGRATION_DIRECTORIES for part in self.path.split("/")[:-1])


@dataclass(frozen=True)
class ProbeOutcome:
    probe_id: str
    level: VerificationLevel
    criteria: tuple[str, ...]
    passed: bool
    readiness: str
    ref: str | None = None


@dataclass
class CertificationInputs:
    default_level: VerificationLevel
    verification_passed: bool
    digest: str
    passed_validators: set[str] = field(default_factory=set)
    """Validators whose latest result on the digest passed."""
    level_validators: Mapping[VerificationLevel, set[str]] = field(default_factory=dict)
    """Validators that establish a rung, from the available capabilities of the profiles."""
    tests: Mapping[str, list[NamedTest]] = field(default_factory=dict)
    """Tests of the workspace that name each criterion."""
    acceptance_passed: bool = False
    acceptance_tests: Mapping[str, list[NamedTest]] = field(default_factory=dict)
    probes: list[ProbeOutcome] = field(default_factory=list)
    deferred: list[DeferredVerification] = field(default_factory=list)
    checked: set[str] = field(default_factory=set)
    """Manual items a person ticked in a decision on this digest."""
    decided: bool = False
    """A person already decided on this digest (a manual item not ticked then stays unticked)."""
    waived: set[str] = field(default_factory=set)
    validation_refs: Mapping[str, str] = field(default_factory=dict)


def required_level(criterion: AcceptanceCriterion, default: VerificationLevel) -> VerificationLevel:
    return criterion.verification.level if criterion.verification else default


def _evidence(criterion: AcceptanceCriterion, inputs: CertificationInputs) -> list[LevelEvidence]:
    return [
        *_test_evidence(criterion.criterion_id, inputs),
        *_probe_evidence(criterion, inputs),
        *_deferred_evidence(criterion.criterion_id, inputs),
        *_manual_evidence(criterion, inputs),
    ]


def _test_evidence(cid: str, inputs: CertificationInputs) -> list[LevelEvidence]:
    """L0 from the verification, L1 from the tests and acceptance tests that name the
    criterion, L2 from its integration tests."""
    found: list[LevelEvidence] = []
    if inputs.verification_passed:
        found.append(
            LevelEvidence(
                level=VerificationLevel.L0,
                source="verification",
                detail=f"every mandatory validator passed on {inputs.digest}",
            )
        )
    l1 = set(inputs.level_validators.get(VerificationLevel.L1, set()))
    l2 = set(inputs.level_validators.get(VerificationLevel.L2, set()))
    l1_passed = sorted(l1 & inputs.passed_validators)
    l2_passed = sorted(l2 & inputs.passed_validators)
    named = inputs.tests.get(cid, [])
    if inputs.verification_passed and l1_passed and named:
        found.append(
            LevelEvidence(
                level=VerificationLevel.L1,
                source=", ".join(l1_passed),
                detail="passing test(s) that name the criterion: "
                + ", ".join(item.node_id for item in named[:5]),
                refs=tuple(
                    inputs.validation_refs[item]
                    for item in l1_passed
                    if item in inputs.validation_refs
                ),
            )
        )
    accepted = inputs.acceptance_tests.get(cid, [])
    if inputs.acceptance_passed and accepted:
        found.append(
            LevelEvidence(
                level=VerificationLevel.L1,
                source="harness.acceptance-tests",
                detail="frozen acceptance test(s) that name the criterion passed: "
                + ", ".join(item.node_id for item in accepted[:5]),
            )
        )
    integration = [item for item in named if item.integration]
    if inputs.verification_passed and l2_passed and integration:
        found.append(
            LevelEvidence(
                level=VerificationLevel.L2,
                source=", ".join(l2_passed),
                detail="passing integration test(s) that name the criterion: "
                + ", ".join(item.node_id for item in integration[:5]),
            )
        )
    return found


def _probe_evidence(
    criterion: AcceptanceCriterion, inputs: CertificationInputs
) -> list[LevelEvidence]:
    """The level of every ready probe of the criterion that passed."""
    found: list[LevelEvidence] = []
    cid = criterion.criterion_id
    declared_probe = criterion.verification.probe if criterion.verification else None
    for probe in inputs.probes:
        if probe.probe_id != declared_probe and cid not in probe.criteria:
            continue
        if probe.passed and probe.readiness == "READY":
            found.append(
                LevelEvidence(
                    level=probe.level,
                    source=f"probe.{probe.probe_id}",
                    detail="the probe ran on every variant and its assertions held",
                    refs=(probe.ref,) if probe.ref else (),
                )
            )
    return found


def _deferred_evidence(cid: str, inputs: CertificationInputs) -> list[LevelEvidence]:
    return [
        LevelEvidence(
            level=item.level,
            source=f"deferred.{item.item_id}",
            detail=item.summary or f"deferred verification closed ({item.where})",
            refs=(item.evidence_ref,) if item.evidence_ref else (),
        )
        for item in inputs.deferred
        if item.criterion_id == cid and item.status == "PASSED"
    ]


def _manual_evidence(
    criterion: AcceptanceCriterion, inputs: CertificationInputs
) -> list[LevelEvidence]:
    verification = criterion.verification
    if verification and verification.manual and criterion.criterion_id in inputs.checked:
        return [
            LevelEvidence(
                level=VerificationLevel.L5,
                source="decision",
                detail=f"a person checked: {verification.manual}",
            )
        ]
    return []


def _pending(
    criterion: AcceptanceCriterion, required: VerificationLevel, inputs: CertificationInputs
) -> list[str]:
    declaration = criterion.verification
    if declaration is None:
        return []
    pending: list[str] = []
    for item in inputs.deferred:
        if (
            item.criterion_id == criterion.criterion_id
            and item.status == "PENDING"
            and item.level.rank >= required.rank
        ):
            pending.append(item.item_id)
    if (
        declaration.manual
        and required is VerificationLevel.L5
        and criterion.criterion_id not in inputs.checked
        and not inputs.decided
    ):
        pending.append(criterion.criterion_id)
    return pending


def certify_criterion(
    criterion: AcceptanceCriterion, inputs: CertificationInputs
) -> CriterionCertification:
    required = required_level(criterion, inputs.default_level)
    evidence = _evidence(criterion, inputs)
    achieved = max((item.level for item in evidence), key=lambda level: level.rank, default=None)
    declared = criterion.verification is not None
    pending = _pending(criterion, required, inputs)
    if achieved is not None and achieved.rank >= required.rank:
        status, reason = "CERTIFIED", f"reached {achieved.value} (requires {required.value})"
    elif criterion.criterion_id in inputs.waived:
        status = "WAIVED"
        reason = (
            f"requires {required.value}; a person decided to continue without it "
            f"(reached {achieved.value if achieved else 'no rung'})"
        )
    elif pending:
        status = "PENDING"
        reason = f"requires {required.value}; waiting for {', '.join(pending)}"
    else:
        status = "NOT_CERTIFIED"
        reason = (
            f"requires {required.value}, reached {achieved.value if achieved else 'no rung'}"
            + ("" if declared else " (default level, not declared)")
        )
    return CriterionCertification(
        criterion_id=criterion.criterion_id,
        required=required,
        declared=declared,
        achieved=achieved,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        evidence=tuple(evidence),
        pending=tuple(pending),
    )


def overall(criteria: Iterable[CriterionCertification]) -> CertificationStatus:
    items = list(criteria)
    if items and all(item.status == "CERTIFIED" for item in items):
        return "CERTIFIED"
    if any(item.status in {"CERTIFIED", "PENDING"} for item in items):
        return "PARTIAL"
    return "NOT_CERTIFIED"


def certify(
    criteria: Iterable[AcceptanceCriterion], inputs: CertificationInputs
) -> tuple[CertificationStatus, tuple[CriterionCertification, ...]]:
    results = tuple(certify_criterion(item, inputs) for item in criteria)
    return overall(results), results


def summary(status: str, criteria: Iterable[CriterionCertification]) -> dict[str, Any]:
    items = list(criteria)
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return {"status": status, "criteria": len(items), "byStatus": counts}


__all__ = [
    "INTEGRATION_DIRECTORIES",
    "CertificationInputs",
    "NamedTest",
    "ProbeOutcome",
    "certify",
    "certify_criterion",
    "overall",
    "required_level",
    "summary",
]
