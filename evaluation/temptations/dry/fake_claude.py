#!/usr/bin/env python3
"""``evaluation/fake_claude.py`` for the temptation tasks (dry runs of run_temptations.py).

Every call is answered by ``evaluation/fake_claude.py`` except two: an implementation lays the
task's ``dry/<mode>/`` over the working directory (``tempted`` by default, ``clean`` when the
``fake-mode`` file next to the workspace says so), and a request for acceptance tests gets the
task's ``dry/acceptance.py``. The task is the one named in the ``temptation-task`` file next to
the workspace. It keeps the name ``fake_claude.py``: ``agentlib.is_fake`` recognizes a dry-run CLI
by that name and then skips the account check. It never calls a model.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TASKS = HERE.parent / "tasks"
_spec = importlib.util.spec_from_file_location(
    "eval_fake_claude", HERE.parents[1] / "fake_claude.py"
)
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
_base_call = base._call


def _task(cwd: Path) -> Path | None:
    marker = cwd.parent / "temptation-task"
    return TASKS / marker.read_text(encoding="utf-8").strip() if marker.is_file() else None


def _implement(cwd: Path, prompt: str) -> str:
    task = _task(cwd)
    if task is None:
        return "Nothing to implement: no temptation task."
    mode = base._mode(cwd) or "tempted"
    shutil.copytree(task / "dry" / mode, cwd, dirs_exist_ok=True)
    return f"Applied the {mode} variant of {task.name}."


def _call(prompt: str, cwd: Path) -> dict[str, Any] | None:
    task = _task(cwd)
    if task is not None and "Write acceptance tests for the task below" in prompt:
        request = base._request(prompt)
        criteria = [
            c.get("criterion_id")
            for c in (request.get("task") or {}).get("acceptance_criteria") or []
        ] or ["ac"]
        directory = request.get("directory") or "tests/acceptance"
        content = (task / "dry" / "acceptance.py").read_text(encoding="utf-8")
        return {
            "tests": [{"path": f"{directory}/test_acceptance_{criteria[0]}.py", "content": content}]
        }
    return _base_call(prompt, cwd)


base._implement = _implement
base._call = _call

if __name__ == "__main__":
    raise SystemExit(base.main(sys.argv[1:]))
