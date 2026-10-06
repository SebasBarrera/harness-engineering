from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from governed_harness.configuration.models import CapabilityRule
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Actor, CapabilityGrant


class CapabilityDenied(PermissionError):
    pass


class CapabilityAuthorizer:
    def authorize(
        self,
        *,
        actor: Actor,
        capability: str,
        resource: str,
        grants: Iterable[CapabilityGrant],
        now: datetime | None = None,
        allow_approval_required: bool = False,
    ) -> CapabilityGrant:
        instant = now or datetime.now(UTC)
        for grant in grants:
            if (
                grant.actor != actor
                or grant.capability != capability
                or not grant.active_at(instant)
            ):
                continue
            if grant.approval_required and not allow_approval_required:
                continue
            if any(self._matches(resource, scope) for scope in grant.scope):
                return grant
        raise CapabilityDenied(f"{actor.actor_id} lacks {capability} for {resource}")

    def authorize_command(
        self,
        *,
        actor: Actor,
        argv: Sequence[str],
        grants: Iterable[CapabilityGrant],
        now: datetime | None = None,
    ) -> CapabilityGrant:
        if not argv:
            raise CapabilityDenied("empty command is not authorized")
        resource = " ".join(argv)
        instant = now or datetime.now(UTC)
        for grant in grants:
            if (
                grant.actor != actor
                or grant.capability != "process.execute"
                or not grant.active_at(instant)
            ):
                continue
            for scope in grant.scope:
                if (
                    scope == "**"
                    or argv[0] == scope
                    or resource == scope
                    or resource.startswith(f"{scope} ")
                ):
                    return grant
        raise CapabilityDenied(f"{actor.actor_id} lacks process.execute for {resource}")

    @staticmethod
    def _matches(resource: str, scope: str) -> bool:
        normalized_resource = resource.replace(os.sep, "/").lstrip("./")
        normalized_scope = scope.replace(os.sep, "/").lstrip("./")
        if normalized_scope == "**":
            return True
        if fnmatch.fnmatchcase(normalized_resource, normalized_scope):
            return True
        if normalized_scope.endswith("/**"):
            base = normalized_scope[:-3].rstrip("/")
            return normalized_resource == base or normalized_resource.startswith(f"{base}/")
        return normalized_resource == normalized_scope


def grants_from_rules(
    execution_id: str,
    actor: Actor,
    rules: Iterable[CapabilityRule],
    *,
    lifetime: timedelta = timedelta(hours=8),
) -> list[CapabilityGrant]:
    from governed_harness.capabilities.phase import current_policy

    policy = current_policy()
    if policy is not None:
        # governance.phaseCapabilities (#4): only what the running phase allows.
        rules = policy.apply(actor, rules)
    issued = datetime.now(UTC)
    return [
        CapabilityGrant(
            grant_id=new_id("grant"),
            execution_id=execution_id,
            actor=actor,
            capability=rule.capability,
            scope=rule.scope,
            issued_at=issued,
            expires_at=issued + lifetime,
            conditions=rule.conditions,
            approval_required=rule.approval_required,
        )
        for rule in rules
    ]


def contained_path(root: Path, candidate: Path, *, allow_missing: bool = True) -> Path:
    resolved_root = root.resolve(strict=True)
    unresolved = candidate if candidate.is_absolute() else resolved_root / candidate
    resolved_candidate = unresolved.resolve(strict=not allow_missing)
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise CapabilityDenied(f"path escapes workspace: {candidate}")
    # Reject any existing symlink in the path from the workspace root to the candidate. The
    # candidate may reach the workspace through a symlinked ancestor outside it (macOS: /var ->
    # /private/var), so the workspace part is taken after the first ancestor, from the top, that
    # resolves to the workspace root; links inside the workspace are still checked below.
    workspace_prefix = next(
        (
            ancestor
            for ancestor in reversed((unresolved, *unresolved.parents))
            if ancestor.resolve() == resolved_root
        ),
        None,
    )
    if workspace_prefix is None:
        raise CapabilityDenied(f"path escapes workspace: {candidate}")
    relative = unresolved.relative_to(workspace_prefix)
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise CapabilityDenied(f"symlink path is not allowed: {current}")
    return resolved_candidate
