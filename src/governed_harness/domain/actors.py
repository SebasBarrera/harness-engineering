"""Who may record a human act, and the identity recorded for the person who decides."""

from __future__ import annotations

import re
from typing import Literal

from .errors import ConfigurationError, NonHumanActorError

NON_HUMAN_ACTOR_PREFIXES = ("agent.", "validator.", "harness.")
"""Actor id namespaces the harness assigns to agents, validators and itself."""

_NON_HUMAN_ACTOR_NAMES = frozenset(prefix.rstrip(".") for prefix in NON_HUMAN_ACTOR_PREFIXES)

IdentitySource = Literal["explicit", "git", "fallback", "default"]
"""Where the actor id of a human act came from (``governance.deciderIdentity``)."""

DEFAULT_CLI_ACTOR = "human.local"
DEFAULT_API_ACTOR = "human.web"

_ACTOR_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


def is_non_human_actor_id(actor_id: str) -> bool:
    normalized = actor_id.strip().lower()
    return normalized in _NON_HUMAN_ACTOR_NAMES or normalized.startswith(NON_HUMAN_ACTOR_PREFIXES)


def require_human_actor(actor_id: str, act: str = "record a human decision") -> None:
    """Refuse an actor id in the namespace of an agent, a validator or the harness itself.

    The actor id is not authenticated; the check stops a process that runs with the person's
    terminal (an agent CLI, a validator, the harness) from recording a decision under its own,
    recognisable identity, as ``task clarify`` already did."""
    if is_non_human_actor_id(actor_id):
        raise NonHumanActorError(
            f"actor {actor_id!r} belongs to the agent, validator or harness namespace "
            f"({', '.join(NON_HUMAN_ACTOR_PREFIXES)}); only a person may {act}"
        )


def actor_id_from_identity(name: str | None, email: str | None) -> str:
    """A valid actor id for a Git user identity: the e-mail address (or the name) in lower case
    with every character outside ``[a-z0-9_.-]`` replaced by ``-``."""
    for source in (email, name):
        if not source or not source.strip():
            continue
        candidate = re.sub(r"[^a-z0-9_.-]+", "-", source.strip().lower())
        candidate = re.sub(r"^[^a-z]+", "", candidate)[:128].rstrip("-.")
        if _ACTOR_ID.match(candidate):
            return candidate
    raise ConfigurationError(
        "governance.deciderIdentity is git but Git has no usable user.email or user.name; "
        "set them with git config or pass --actor"
    )


def identity_display_name(name: str | None, email: str | None) -> str | None:
    address = f"<{email.strip()}>" if email and email.strip() else None
    parts = [part.strip() for part in (name, address) if part and part.strip()]
    return " ".join(parts) or None
