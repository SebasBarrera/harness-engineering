"""Capability errors and the command scope rule, kept apart so that the authorizer and the
phase policy can both use them without importing each other."""

from __future__ import annotations

from collections.abc import Sequence


class CapabilityDenied(PermissionError):
    pass


def command_in_scope(argv: Sequence[str], scope: str) -> bool:
    """Whether a ``process.execute`` scope allows the command ``argv``: every command (``**``),
    its executable, or its first arguments joined by spaces. Only the executable may itself
    contain spaces (``C:/Users/John Doe/agent.exe``): ``python -m pytest`` allows
    ``python -m pytest -q`` but not the single argument ``-m pytest``, and ``python`` does not
    allow an executable named ``python evil.py`` (comparing the joined text did)."""
    if scope == "**":
        return True
    if not argv:
        return False
    if argv[0] == scope:
        return True
    for count in range(2, len(argv) + 1):
        if " " in argv[count - 1]:
            return False
        if " ".join(argv[:count]) == scope:
            return True
    return False
