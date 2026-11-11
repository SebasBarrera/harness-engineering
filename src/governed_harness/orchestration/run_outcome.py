"""What a failing test run tells the differential verification about the tests that ran (#190).

A test run that was interrupted before any test ran (pytest stopped at collection, a usage
error, no test collected) reports problems, but none of them is a test outcome: the collection
errors hide every other result, so a regression the change introduced cannot show up as a new
problem. The differential comparison treats such a run as not comparable and keeps the failure.

The tests that ran are also read from the output, so the comparison can check that the test
files the ChangeSet added or modified ran at all. pytest prints the node ids of the tests that
failed and, with ``-rA`` or ``-v``, of those that passed; the list is complete when it holds as
many tests as the summary line counts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from governed_harness.checks.model import is_test_path

__all__ = ["RunOutcome", "changed_test_files", "not_run", "read_test_run", "runner_of"]

# pytest exit codes: 2 interrupted (collection errors, KeyboardInterrupt), 3 internal error,
# 4 usage error, 5 no tests collected. None of them comes with a test outcome.
_PYTEST_NO_OUTCOME = {
    2: "pytest was interrupted (exit code 2), for example by an error during collection",
    3: "pytest stopped with an internal error (exit code 3)",
    4: "pytest was called wrongly (exit code 4)",
    5: "pytest collected no test (exit code 5)",
}
_SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|xfailed|xpassed|errors?|skipped|deselected)\b")
_SUMMARY_TIME = re.compile(r"\bin \d+(?:\.\d+)?s\b")
_SHORT_NODE = re.compile(r"^(?:PASSED|FAILED|XFAIL|XPASS) (?P<node>[^\s:]+::\S+)")
_VERBOSE_NODE = re.compile(r"^(?P<node>[^\s:]+::\S+) (?:PASSED|FAILED|XFAIL|XPASS)\b")
_RAN = ("passed", "failed", "xfailed", "xpassed")
_PYTEST_TEST_FILE = re.compile(r"^(test_.*|.*_test)\.py$")
_TEST_TOKEN = re.compile(r"(?i)test\w*")


@dataclass(frozen=True)
class RunOutcome:
    """What a test run's output says about the tests that ran."""

    ran: bool
    """At least one test passed or failed."""
    reason: str
    """Why the run has no test outcome (empty when ``ran``)."""
    executed: frozenset[str] | None
    """The workspace-relative paths of the test files with a test that ran; ``None`` when the
    output does not list every test that ran."""


def runner_of(validator_id: str, argv: tuple[str, ...]) -> str | None:
    """``pytest`` for a pytest command, ``other`` for another test runner, ``None`` for a
    validator that does not run tests (a linter, a type checker)."""
    names = [PurePosixPath(item.replace("\\", "/")).name.lower() for item in argv]
    if any(name.removesuffix(".exe") in {"pytest", "py.test"} for name in names):
        return "pytest"
    last = validator_id.rsplit(".", 1)[-1].lower()
    if "test" in last or last == "unit" or any(_TEST_TOKEN.fullmatch(item) for item in argv):
        return "other"
    return None


def read_test_run(
    runner: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    keys: tuple[str, ...],
) -> RunOutcome:
    """The outcome of a failing test run, from its exit code, its output and its parsed
    problem keys (``rule|path|message``)."""
    failed_keys = [key for key in keys if key.startswith("test-failed|")]
    if runner != "pytest":
        if failed_keys:
            return RunOutcome(True, "", None)
        return RunOutcome(False, "no test outcome could be read from the output", None)
    if exit_code in _PYTEST_NO_OUTCOME:
        return RunOutcome(False, _PYTEST_NO_OUTCOME[exit_code], frozenset())
    lines = f"{stdout}\n{stderr}".splitlines()
    counts = _summary_counts(lines)
    ran = sum(counts.get(name, 0) for name in _RAN) if counts is not None else None
    if ran == 0 or (ran is None and not failed_keys):
        return RunOutcome(
            False, "the pytest output reports no test that passed or failed", frozenset()
        )
    nodes = {
        found["node"]
        for line in lines
        if (found := _SHORT_NODE.match(line) or _VERBOSE_NODE.match(line))
    }
    files = frozenset(node.split("::", 1)[0].replace("\\", "/") for node in nodes)
    complete = ran is not None and len(nodes) >= ran
    return RunOutcome(True, "", files if complete else None)


def changed_test_files(runner: str, changes: list[tuple[str, str]]) -> list[str]:
    """The test files the ChangeSet added or modified (``(path, status)`` pairs) that the
    runner is expected to run."""
    found = []
    for path, status in changes:
        normalized = path.replace("\\", "/")
        if status == "DELETED" or not is_test_path(normalized):
            continue
        if runner == "pytest" and not _PYTEST_TEST_FILE.match(PurePosixPath(normalized).name):
            continue
        found.append(normalized)
    return sorted(set(found))


def not_run(changed: list[str], executed: frozenset[str]) -> list[str]:
    """The changed test files none of whose tests ran (a node path may be relative to a
    rootdir below the workspace)."""
    return [
        path
        for path in changed
        if not any(path == item or path.endswith(f"/{item}") for item in executed)
    ]


def _summary_counts(lines: list[str]) -> dict[str, int] | None:
    """The counts of the last pytest summary line (``1 failed, 2 passed in 0.12s``)."""
    for line in reversed(lines):
        if not _SUMMARY_TIME.search(line):
            continue
        found = _SUMMARY_COUNT.findall(line)
        if found:
            counts: dict[str, int] = {}
            for number, name in found:
                key = "errors" if name == "error" else name
                counts[key] = counts.get(key, 0) + int(number)
            return counts
        if "no tests ran" in line:
            return {}
    return None
