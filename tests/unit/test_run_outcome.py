"""What a failing test run tells the differential verification (#190)."""

from __future__ import annotations

import pytest

from governed_harness.orchestration.run_outcome import (
    changed_test_files,
    not_run,
    read_test_run,
    runner_of,
)

COLLECTION_ERROR = (
    "==================================== ERRORS ====================================\n"
    "___________________ ERROR collecting tests/test_needs_dep.py ___________________\n"
    "E   ModuleNotFoundError: No module named 'missing_dependency'\n"
    "=========================== short test summary info ============================\n"
    "ERROR tests/test_needs_dep.py\n"
    "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
    "1 error in 0.12s\n"
)
KEY_ERROR = "test-error|tests/test_needs_dep.py|tests/test_needs_dep.py error"
KEY_FAILED = "test-failed|tests/test_legacy.py|tests/test_legacy.py::test_legacy failed"


@pytest.mark.parametrize(
    ("validator_id", "argv", "runner"),
    [
        ("python.pytest", ("python", "-m", "pytest", "-q"), "pytest"),
        ("project.tests", ("uv", "run", "pytest"), "pytest"),
        ("project.tests", ("C:\\venv\\Scripts\\pytest.exe",), "pytest"),
        ("node.test", ("npm", "test", "--silent"), "other"),
        ("android.unit", ("./gradlew", "testDebugUnitTest", "--quiet"), "other"),
        ("go.test", ("go", "test", "./..."), "other"),
        ("python.ruff", ("python", "-m", "ruff", "check", "--no-fix", "."), None),
        ("python.mypy", ("python", "-m", "mypy", "."), None),
        ("go.vet", ("go", "vet", "./..."), None),
    ],
)
def test_runner_of(validator_id: str, argv: tuple[str, ...], runner: str | None) -> None:
    assert runner_of(validator_id, argv) == runner


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_pytest_exit_codes_without_an_outcome(code: int) -> None:
    outcome = read_test_run("pytest", code, COLLECTION_ERROR, "", (KEY_ERROR,))
    assert not outcome.ran
    assert f"exit code {code}" in outcome.reason


def test_only_collection_errors_is_no_outcome() -> None:
    """``--continue-on-collection-errors`` exits 1, but no test passed or failed."""
    outcome = read_test_run("pytest", 1, COLLECTION_ERROR, "", (KEY_ERROR,))
    assert not outcome.ran
    assert "no test that passed or failed" in outcome.reason


def test_a_failed_test_is_an_outcome_and_the_list_of_ran_tests_may_be_unknown() -> None:
    output = (
        "F.\nFAILED tests/test_legacy.py::test_legacy - assert 2 == 3\n1 failed, 1 passed in 0.1s\n"
    )
    outcome = read_test_run("pytest", 1, output, "", (KEY_FAILED,))
    assert outcome.ran
    assert outcome.executed is None


def test_the_ran_tests_are_known_when_every_test_is_listed() -> None:
    output = (
        "PASSED tests/test_pricing.py::test_below_threshold\n"
        "FAILED tests/test_legacy.py::test_legacy - assert 2 == 3\n"
        "1 failed, 1 passed in 0.1s\n"
    )
    outcome = read_test_run("pytest", 1, output, "", (KEY_FAILED,))
    assert outcome.executed == frozenset({"tests/test_pricing.py", "tests/test_legacy.py"})
    verbose = (
        "tests/test_pricing.py::test_below_threshold PASSED [ 50%]\n"
        "tests/test_legacy.py::test_legacy FAILED [100%]\n"
        "========== 1 failed, 1 passed in 0.10s ==========\n"
    )
    assert read_test_run("pytest", 1, verbose, "", (KEY_FAILED,)).executed == frozenset(
        {"tests/test_pricing.py", "tests/test_legacy.py"}
    )


def test_without_a_summary_a_failed_key_is_still_an_outcome() -> None:
    assert read_test_run("pytest", 1, "", "", (KEY_FAILED,)).ran
    assert not read_test_run("pytest", 1, "", "", (KEY_ERROR,)).ran


def test_another_runner_needs_a_failed_test() -> None:
    assert read_test_run("other", 1, "", "", (KEY_FAILED,)).ran
    outcome = read_test_run("other", 1, "boom", "", ("cargo|src/lib.rs|x",))
    assert not outcome.ran
    assert outcome.executed is None


def test_changed_test_files_and_not_run() -> None:
    changes = [
        ("tests/test_new.py", "ADDED"),
        ("tests/conftest.py", "MODIFIED"),
        ("tests/helpers.py", "MODIFIED"),
        ("tests/test_gone.py", "DELETED"),
        ("src/app.py", "MODIFIED"),
        ("pkg/thing_test.py", "MODIFIED"),
    ]
    assert changed_test_files("pytest", changes) == ["pkg/thing_test.py", "tests/test_new.py"]
    assert "tests/helpers.py" in changed_test_files("other", changes)
    executed = frozenset({"test_new.py"})
    assert not_run(["pkg/thing_test.py", "tests/test_new.py"], executed) == ["pkg/thing_test.py"]
