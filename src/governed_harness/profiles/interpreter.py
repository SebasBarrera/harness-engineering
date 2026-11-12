"""The project's Python interpreter (``toolchain.interpreter: auto``, since 2.0).

The built-in Python validators run ``python -m pytest`` with the ``python`` on ``PATH``, which
in a project managed by uv or Poetry, or with a local virtual environment, is not the one that
has the project's dependencies. Discovery, in order:

1. ``.venv`` or ``venv`` in the workspace (``bin/python``, or ``Scripts/python.exe`` on
   Windows): the interpreter itself, by absolute path;
2. ``uv.lock`` with ``uv`` on ``PATH``: ``uv run --no-sync python`` (uses the project
   environment without installing anything);
3. ``poetry.lock`` with ``poetry`` on ``PATH``: ``poetry run python``.

Nothing found keeps ``python``."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

PYTHON_NAMES = frozenset({"python", "python3"})


@dataclass(frozen=True)
class DiscoveredInterpreter:
    argv: tuple[str, ...]
    source: str

    @property
    def grant_scope(self) -> str:
        """The ``process.execute`` scope that authorizes exactly this interpreter prefix."""
        return " ".join(self.argv)


def discover_python(workspace: Path) -> DiscoveredInterpreter | None:
    root = workspace.resolve()
    for directory in (".venv", "venv"):
        for relative in (("bin", "python"), ("Scripts", "python.exe")):
            candidate = root.joinpath(directory, *relative)
            if candidate.is_file():
                return DiscoveredInterpreter((str(candidate),), f"{directory}/{'/'.join(relative)}")
    if (root / "uv.lock").is_file() and shutil.which("uv"):
        return DiscoveredInterpreter(("uv", "run", "--no-sync", "python"), "uv.lock")
    if (root / "poetry.lock").is_file() and shutil.which("poetry"):
        return DiscoveredInterpreter(("poetry", "run", "python"), "poetry.lock")
    return None


def with_interpreter(
    command: tuple[str, ...] | None, interpreter: DiscoveredInterpreter | None
) -> tuple[str, ...] | None:
    """``command`` with a leading ``python``/``python3`` replaced by the interpreter."""
    if not command or interpreter is None or command[0] not in PYTHON_NAMES:
        return command
    return (*interpreter.argv, *command[1:])
