"""Deterministic checks of the agent-results settings (#40, #52): pure functions over source
text and the ChangeSet diff that return ``Issue`` values."""

from .model import DiffFile, DiffLine, Issue, is_test_path, parse_unified_diff

__all__ = ["DiffFile", "DiffLine", "Issue", "is_test_path", "parse_unified_diff"]
