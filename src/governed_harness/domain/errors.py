from __future__ import annotations


class HarnessError(Exception):
    """Base error with a stable exit code for CLI/API translation."""

    exit_code = 1


class ConfigurationError(HarnessError):
    exit_code = 2


class NotFoundError(HarnessError):
    exit_code = 3


class GatePendingError(HarnessError):
    exit_code = 4


class PolicyViolationError(HarnessError):
    exit_code = 5


class ExecutionBlockedError(HarnessError):
    exit_code = 6


class CancelledError(HarnessError):
    exit_code = 130


class NonHumanActorError(PolicyViolationError):
    """A human act (a decision, an approval, an answer) named an agent, validator or harness
    actor; exit code 5."""


class IntegrityError(HarnessError):
    """The record of a run did not verify (event chain, records, artifacts or anchor) and the
    command refuses to use it; exit code 6."""

    exit_code = 6
