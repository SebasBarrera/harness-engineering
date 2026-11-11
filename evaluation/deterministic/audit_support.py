"""Shared pieces of the audit suites H13, H16 and H17 (no model is called).

* ``feature_task`` and ``feature_spec``: a run of its own for case ``n``: the module
  ``src/sample/feature_NN.py`` with ``value_NN()`` returning ``n`` and its test
  ``tests/test_feature_NN.py`` naming the requirement and criterion ids, so every change is
  distinct, names no money word (``floatMoney``) and removes no public definition. Variants: a
  ``pickle.loads`` in the module (a blocking ``security.unsafe-deserialization`` finding), a new
  ``requirements-NN.txt`` (the ``newDependency`` risk factor) and a second requirement no test
  names (``traceability.requirement-untested``).
* ``ShiftedHarness``: the harness CLI run through ``timeshift.py`` with the harness's own
  interpreter (the ``python`` next to the ``--harness`` executable), its wall clock moved.
* ``decide``: ``gate decide`` with the instants measured around it and the decisions the run
  recorded before and after (``human.decision.recorded`` events).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from common import GIT_ENV, Harness  # noqa: E402
from resource_support import Utf8Harness  # noqa: E402
from suite_support import WINDOWS, events  # noqa: E402

TIMESHIFT = HERE / "timeshift.py"
DECISION_EVENT = "human.decision.recorded"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def harness_python(args: argparse.Namespace) -> str:
    """The interpreter of the harness under test: the ``python`` next to its executable."""
    found = shutil.which(args.harness) or args.harness
    python = Path(found).resolve().parent / ("python.exe" if WINDOWS else "python")
    if not python.exists():
        raise RuntimeError(f"no python next to the harness executable {found}")
    return str(python)


class ShiftedHarness(Utf8Harness):
    """The harness CLI with its wall clock moved by ``hours`` (``timeshift.py``)."""

    def __init__(self, python: str, env: dict[str, str], hours: float, steps: list[Any]) -> None:
        super().__init__(python, env)
        self.hours = hours
        self.steps = steps  # shared with the plain harness of the case: one ordered record

    def run(
        self, cwd: Path, *args: str, label: str = "", timeout: float | None = None
    ) -> tuple[int, Any, float]:
        return super().run(
            cwd,
            str(TIMESHIFT),
            "--hours",
            f"{self.hours:g}",
            "--",
            *args,
            label=f"{label or ' '.join(args[:2])} (clock {self.hours:+g} h)",
            timeout=timeout,
        )


def shifted(h: Harness, args: argparse.Namespace, hours: float) -> ShiftedHarness:
    return ShiftedHarness(harness_python(args), h.env, hours, h.steps)


# ----- the run of case n -----------------------------------------------------------------------
def module_path(n: int) -> str:
    return f"src/sample/feature_{n:02d}.py"


def feature_test_path(n: int) -> str:
    return f"tests/test_feature_{n:02d}.py"


def requirements_path(n: int) -> str:
    return f"requirements-{n:02d}.txt"


def feature_task(
    prefix: str, n: int, *, untraced: bool = False, risk: bool = False
) -> dict[str, Any]:
    requirements = [{"requirementId": "req_value", "text": f"value_{n:02d}() returns {n}."}]
    if untraced:
        requirements.append(
            {"requirementId": "req_extra", "text": f"value_{n:02d}() returns an int."}
        )
    owned = [module_path(n), feature_test_path(n)]
    if risk:
        owned.append(requirements_path(n))
    return {
        "taskId": f"task_{prefix}_{n:02d}",
        "title": f"Feature {n:02d} value",
        "intent": f"Add value_{n:02d}() that returns {n}.",
        "requirements": requirements,
        "acceptanceCriteria": [
            {"criterionId": "ac_value", "text": f"value_{n:02d}() returns {n}."}
        ],
        "metadata": {"ownedPaths": owned},
    }


def feature_files(n: int, *, pickle: bool = False, risk: bool = False) -> dict[str, str]:
    """The files the change writes (the same for H and D*)."""
    code = f"def value_{n:02d}() -> int:\n    return {n}\n"
    if pickle:
        code = (
            "import pickle\n\n\n"
            + code
            + f"\n\ndef load_{n:02d}(data: bytes) -> object:\n    return pickle.loads(data)\n"
        )
    test = (
        f"from sample.feature_{n:02d} import value_{n:02d}\n\n\n"
        f"def test_req_value() -> None:\n    assert value_{n:02d}() == {n}\n\n\n"
        f"def test_ac_value() -> None:\n    assert value_{n:02d}() == {n}\n"
    )
    files = {module_path(n): code, feature_test_path(n): test}
    if risk:
        files[requirements_path(n)] = "requests==2.32.3\n"
    return files


def feature_spec(n: int, *, pickle: bool = False, risk: bool = False) -> dict[str, Any]:
    return {"change": feature_files(n, pickle=pickle, risk=risk)}


# ----- decisions -------------------------------------------------------------------------------
def decisions(database: Path, run_id: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events(database, run_id) if e["type"] == DECISION_EVENT]


def decide(
    h: Harness,
    ws: Path,
    database: Path,
    run_id: str,
    decision: str,
    digest: str,
    *,
    rationale: str,
    actor: str | None = None,
    extra: tuple[str, ...] = (),
    label: str = "",
) -> dict[str, Any]:
    """``gate decide`` and what it recorded."""
    before = len(decisions(database, run_id))
    command = [
        "gate",
        "decide",
        "--path",
        ".",
        "--run",
        run_id,
        "--decision",
        decision,
        "--change-set-digest",
        digest,
        "--rationale",
        rationale,
    ]
    if actor is not None:
        command += ["--actor", actor]
    command += list(extra)
    started = now_iso()
    code, payload, _ = h.run(ws, *command, label=label or f"gate decide {decision}")
    finished = now_iso()
    recorded = decisions(database, run_id)
    return {
        "exitCode": code,
        "startedAt": started,
        "finishedAt": finished,
        "decisionsBefore": before,
        "decisionsAfter": len(recorded),
        "recorded": recorded[before:],
        "output": payload if isinstance(payload, dict) else {"raw": str(payload)[:400]},
    }


# ----- Git with a given identity ---------------------------------------------------------------
def git_as(
    root: Path,
    *args: str,
    committer: tuple[str, str] = ("dev", "dev@example.invalid"),
    author: tuple[str, str] | None = None,
    check: bool = True,
) -> str:
    """Git with an explicit author and committer (the variables override any configuration)."""
    who = author or committer
    env = {
        **os.environ,
        **GIT_ENV,
        "GIT_AUTHOR_NAME": who[0],
        "GIT_AUTHOR_EMAIL": who[1],
        "GIT_COMMITTER_NAME": committer[0],
        "GIT_COMMITTER_EMAIL": committer[1],
    }
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )
    return proc.stdout


def head(root: Path) -> str:
    return git_as(root, "rev-parse", "HEAD").strip()


def reset_tree(root: Path) -> None:
    """Back to ``HEAD``: the closure commit of ``closureCommit: branch`` leaves the change in the
    working tree, and the next run of the same workspace must start from the baseline. Ignored
    files (``.harness/``) stay."""
    git_as(root, "reset", "-q", "--hard", "HEAD")
    git_as(root, "clean", "-qfd")


def email_person(person: str) -> tuple[str, str]:
    return person, f"{person}@example.invalid"


__all__ = [
    "DECISION_EVENT",
    "ShiftedHarness",
    "decide",
    "decisions",
    "email_person",
    "feature_files",
    "feature_spec",
    "feature_task",
    "git_as",
    "harness_python",
    "head",
    "module_path",
    "now_iso",
    "requirements_path",
    "reset_tree",
    "shifted",
    "feature_test_path",
]
