"""Capabilities per phase (issue #4, ``governance.phaseCapabilities``).

Before, every grant of a run was the union of the profile's and the project's capabilities,
issued for the whole run whatever the phase; a project could only widen a profile, and the
workflow's ``allowedCapabilities`` had no effect. Under ``governance.phaseCapabilities``:

* the resolver narrows instead of widening: a capability the project declares keeps only the
  scopes that both the profiles and the project allow (``profile ∩ project``); a capability the
  project does not mention keeps the profiles' scopes, and one only the project declares is
  dropped. The scopes the harness derives from the validators the project selects (its own
  toolchain validators, the interpreter, the standards tools) are added after it;
* every grant issued while a phase runs keeps only the capabilities that phase allows (the
  workflow's ``allowedCapabilities`` plus what the harness itself runs in it, see
  ``HARNESS_PHASE_CAPABILITIES``);
* an agent call outside IMPLEMENTATION is read-only (no ``filesystem.write``) and the only
  process it may start is its own configured command; a reviewer is one such call.

The policy of the running phase lives in a context variable, so every grant made while the
phase runs (validators, probes, agent calls) goes through it without each caller passing it."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import Actor

WRITE = "filesystem.write"
EXECUTE = "process.execute"

HARNESS_PHASE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "SPECIFICATION": (EXECUTE,),
    "PLANNING": (EXECUTE,),
    "INDEPENDENT_REVIEW": (EXECUTE,),
}
"""What the harness itself runs in a phase beyond the workflow's 1.0.0 declaration: the frozen
acceptance tests before the change (SPECIFICATION), the preflight probes on a copy of the
baseline (PLANNING), the project's consistency checks before the reviewers (INDEPENDENT_REVIEW).
Applied only under ``governance.phaseCapabilities`` and recorded in the resolved workflow."""


@dataclass(frozen=True)
class PhasePolicy:
    phase: str
    allowed: frozenset[str]
    launch: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """``agent.ID`` -> the command of that configured provider (its own launch)."""

    def apply(self, actor: Actor, rules: Iterable[CapabilityRule]) -> list[CapabilityRule]:
        kept = [rule for rule in rules if rule.capability in self.allowed]
        if actor.actor_type is ActorType.AGENT:
            if self.phase != "IMPLEMENTATION":
                kept = [rule for rule in kept if rule.capability not in {WRITE, EXECUTE}]
            launch = self.launch.get(actor.actor_id)
            if launch:
                kept.append(CapabilityRule(capability=EXECUTE, scope=launch))
        return kept

    def describe(self, rules: Iterable[CapabilityRule], agent: str | None) -> dict[str, Any]:
        tool = Actor(actor_type=ActorType.TOOL, actor_id="validator.phase", version="1")
        value: dict[str, Any] = {
            "phase": self.phase,
            "allowed": sorted(self.allowed),
            "validators": [_rule(item) for item in self.apply(tool, rules)],
        }
        if agent is not None:
            actor = Actor(actor_type=ActorType.AGENT, actor_id=agent, version="1")
            value["agent"] = {
                "actor": agent,
                "grants": [_rule(item) for item in self.apply(actor, rules)],
            }
        return value


def _rule(rule: CapabilityRule) -> dict[str, Any]:
    value: dict[str, Any] = {"capability": rule.capability, "scope": list(rule.scope)}
    if rule.approval_required:
        value["approvalRequired"] = True
    return value


_CURRENT: ContextVar[PhasePolicy | None] = ContextVar("harness_phase_policy", default=None)


def current_policy() -> PhasePolicy | None:
    return _CURRENT.get()


@contextmanager
def phase_scope(policy: PhasePolicy | None) -> Iterator[None]:
    token = _CURRENT.set(policy)
    try:
        yield
    finally:
        _CURRENT.reset(token)


# ----- profile ∩ project ----------------------------------------------------------------------
def _within(narrow: str, wide: str) -> bool:
    """Whether every resource ``narrow`` matches is also matched by ``wide`` (conservative:
    ``False`` when it cannot tell)."""
    if wide == "**" or narrow == wide:
        return True
    if narrow == "**":
        return False
    if wide.endswith("/**"):
        base = wide[:-3].rstrip("/")
        stem = narrow[:-3].rstrip("/") if narrow.endswith("/**") else narrow
        return stem == base or stem.startswith(f"{base}/")
    if not any(char in narrow for char in "*?["):
        return fnmatch.fnmatchcase(narrow, wide) or narrow.startswith(f"{wide} ")
    return False


def intersect_scopes(left: Iterable[str], right: Iterable[str]) -> tuple[str, ...]:
    """The scopes allowed by both lists: each scope of one list that lies within a scope of
    the other."""
    first, second = list(dict.fromkeys(left)), list(dict.fromkeys(right))
    kept = [item for item in first if any(_within(item, other) for other in second)]
    kept += [item for item in second if any(_within(item, other) for other in first)]
    return tuple(sorted(dict.fromkeys(kept)))


def intersect_capabilities(
    profile: Iterable[CapabilityRule], project: Iterable[CapabilityRule]
) -> tuple[CapabilityRule, ...]:
    """``profile ∩ project`` by capability (see the module docstring)."""
    merged: dict[tuple[str, bool], set[str]] = {}
    for rule in profile:
        merged.setdefault((rule.capability, rule.approval_required), set()).update(rule.scope)
    declared: dict[tuple[str, bool], set[str]] = {}
    for rule in project:
        declared.setdefault((rule.capability, rule.approval_required), set()).update(rule.scope)
    result: list[CapabilityRule] = []
    for key in sorted(set(merged) | set(declared)):
        if key not in merged:
            continue
        scopes = (
            intersect_scopes(merged[key], declared[key])
            if key in declared
            else tuple(sorted(merged[key]))
        )
        if scopes:
            result.append(CapabilityRule(capability=key[0], scope=scopes, approvalRequired=key[1]))
    return tuple(result)


__all__ = [
    "HARNESS_PHASE_CAPABILITIES",
    "PhasePolicy",
    "current_policy",
    "intersect_capabilities",
    "intersect_scopes",
    "phase_scope",
]
