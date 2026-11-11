"""Remedies printed next to an error: what to run to fix it.

The error text and the exit code stay as they are (scripts and tests rely on them); the hint is
an extra ``hint`` field in the JSON error and a ``Hint:`` line on a terminal."""

from __future__ import annotations

from governed_harness.domain.errors import ConfigurationError, NotFoundError

_HINTS: tuple[tuple[type[BaseException], str, str], ...] = (
    (
        ConfigurationError,
        "no .harness/project.yaml found",
        "Run `harness init` in the project directory, or pass --path to a project that has one.",
    ),
    (
        ConfigurationError,
        "configuration already exists",
        "The project is already initialized; pass --force to replace its project.yaml.",
    ),
    (
        NotFoundError,
        "execution not found",
        "List the runs with `harness run list`; `--run latest` selects the newest run and a "
        "unique prefix such as `run_1a2b` is accepted.",
    ),
    (
        NotFoundError,
        "task not found",
        "List the tasks with `harness task list`, or create one with "
        "`harness task create --file task.yaml`.",
    ),
    (
        NotFoundError,
        "artifact not found",
        "Use the full `artifact://sha256/...` reference or a longer digest prefix shown by "
        "`harness evidence list`.",
    ),
)


def default_hint(error: BaseException) -> str | None:
    message = str(error)
    for kind, fragment, hint in _HINTS:
        if isinstance(error, kind) and fragment in message:
            return hint
    return None
