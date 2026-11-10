"""Capability errors and the command scope rule, kept apart so that the authorizer and the
phase policy can both use them without importing each other."""

from __future__ import annotations

from collections.abc import Sequence


class CapabilityDenied(PermissionError):
    pass


def command_in_scope(argv: Sequence[str], scope: str) -> bool:
    """Whether a ``process.execute`` scope allows the command ``argv``: every command (``**``),
    its executable, the whole command, or a prefix of it ending at an argument boundary."""
    resource = " ".join(argv)
    return (
        scope == "**"
        or (bool(argv) and argv[0] == scope)
        or resource == scope
        or resource.startswith(f"{scope} ")
    )
