"""Capability errors and the command scope rule, kept apart so that the authorizer and the
phase policy can both use them without importing each other."""

from __future__ import annotations

from collections.abc import Sequence


class CapabilityDenied(PermissionError):
    pass


def command_in_scope(argv: Sequence[str], scope: str) -> bool:
    """Whether a ``process.execute`` scope allows the command ``argv``: every command (``**``),
    its executable, the whole command, or its first arguments. The scope's words are compared
    with whole arguments: ``python -m pytest`` allows ``python -m pytest -q`` but not the single
    argument ``-m pytest``, and ``python`` does not allow an executable named ``python evil.py``
    (comparing the joined text did)."""
    if scope == "**":
        return True
    if not argv:
        return False
    if argv[0] == scope:
        return True
    if not scope.startswith(f"{argv[0]} "):
        return False
    words = scope[len(argv[0]) + 1 :].split(" ")
    return tuple(argv[1 : 1 + len(words)]) == tuple(words)
