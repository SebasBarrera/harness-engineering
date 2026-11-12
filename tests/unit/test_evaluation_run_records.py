"""Run records of the evaluation runner (``evaluation/run_eval.py``) for runs that never ran.

Amendment of 2026-11-12 to the pre-registration: a governed run whose ``harness run start`` fails
is recorded as an apparatus failure (never dropped), and a task the harness refuses at ``task
create`` is recorded as refused, with no hidden-test score."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import conditions  # noqa: E402
import run_eval  # noqa: E402


def _proc(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "run" / "ws"
    (ws / ".harness").mkdir(parents=True)
    (ws / ".harness" / "project.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(conditions, "write_config", lambda *a, **k: None)
    return ws


def test_a_failed_run_start_is_an_invalid_record(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = {
        "task": _proc(0, json.dumps({"taskId": "task_x"})),
        "run": _proc(4, "", "Traceback ...\nOSError: path too long\n"),
    }
    monkeypatch.setattr(run_eval, "harness", lambda ws, *args: replies[args[0]])
    result = run_eval.run_harness(
        workspace, workspace.parent, Path("task.yaml"), "m", condition="harness"
    )
    assert result["invalid"] == run_eval.RUN_START_FAILED
    assert result["outcome"] == "run-start-failed-exit-4"
    assert result["runStartError"]["exitCode"] == 4
    assert "path too long" in result["runStartError"]["stderr"]
    assert [c["command"] for c in result["commands"]] == [["task", "create"], ["run", "start"]]
