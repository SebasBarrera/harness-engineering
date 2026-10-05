"""The digest a plan approval is bound to (#8 through #58).

A PLANNING attempt rebuilds its plan with fresh step identifiers, so the digest covers what
the plan says, not its identifiers: the task revision, each step's description, capabilities
and expected evidence, the validators and the risks. The same task and configuration give the
same digest on every attempt; any change to them gives another one, and an approval of the
earlier plan no longer applies."""

from __future__ import annotations

from governed_harness.domain.models import Plan
from governed_harness.evidence.hashing import sha256_json


def plan_digest(plan: Plan, task_digest: str) -> str:
    return sha256_json(
        {
            "taskDigest": task_digest,
            "steps": [
                {
                    "description": step.description,
                    "capabilities": list(step.capabilities),
                    "expectedEvidence": list(step.expected_evidence),
                }
                for step in plan.steps
            ],
            "validators": list(plan.validator_ids),
            "risks": list(plan.risks),
        }
    )


__all__ = ["plan_digest"]
