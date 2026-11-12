"""Run records of the evaluation runner (``evaluation/run_eval.py``) for runs that never ran.

Amendment of 2026-11-12 to the pre-registration: a governed run whose ``harness run start`` fails
is recorded as an apparatus failure (never dropped), and a task the harness refuses at ``task
create`` is recorded as refused, with no hidden-test score."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def test_a_refused_task_is_marked_refused(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_eval, "harness", lambda ws, *args: _proc(2, "", "no criteria"))
    result = run_eval.run_harness(
        workspace, workspace.parent, Path("task.yaml"), "m", condition="harness-core"
    )
    assert result["outcome"] == "task-refused-exit-2"
    assert result["refused"] is True
    assert "invalid" not in result


def test_refused_measures_have_no_hidden_score() -> None:
    measures: dict[str, Any] = run_eval.refused_measures("task-refused-exit-2")
    assert measures["hidden"] is None
    assert measures["refused"] is True
    assert measures["measuredOn"] is None


def _record(condition: str, outcome: str, passed: int, **extra: Any) -> dict[str, Any]:
    change = {"addedLines": 0, "testFiles": 0, "outOfScopeFiles": 0, "paths": []}
    hidden = {"passed": passed, "tests": 24, "failures": 0, "errors": 0}
    return {
        "scenario": "brownfield",
        "condition": condition,
        "model": "m",
        "prompt": "casual",
        "rep": 1,
        "wallSeconds": 1.0,
        "harness": {"outcome": outcome, "delivered": outcome == "approved"},
        "measures": {"hidden": hidden, "change": change},
        "agent": {
            k: 0
            for k in (
                "calls",
                "costUsd",
                "outputTokens",
                "inputTokens",
                "cacheReadTokens",
                "cacheCreationTokens",
                "errors",
                "permissionDenials",
                "wallSeconds",
            )
        },
        **extra,
    }


def test_reports_show_a_refused_cell_as_refused() -> None:
    import report
    import report_prompts

    old = _record("harness-core", "task-refused-exit-2", 18)  # written before 2026-11-12
    new = _record("harness-core", "task-refused-exit-2", 0, refused="task-refused-exit-2")
    new["measures"] = run_eval.refused_measures("task-refused-exit-2")
    cell = report.summarize_cell_v2([old, new])
    assert cell["refused"] == 2
    assert cell["measured"] == 0
    assert [row["hidden"] for row in cell["perRun"]] == ["rechazada", "rechazada"]
    assert cell["hiddenRatio"]["n"] == 0
    assert cell["harness"]["outcomes"] == {"task-refused-exit-2": 2}
    summary = report_prompts.summarize([old, new])
    assert summary["refused"] == 2
    assert summary["deliveredDefective"] == 0
    assert summary["blockedDefective"] == 0


@pytest.mark.parametrize("scenario", ["greenfield", "security"])
def test_a_poor_prompt_level_finds_no_specification(tmp_path: Path, scenario: str) -> None:
    site = tmp_path / "site"
    site.mkdir()
    poor = tmp_path / "poor"
    run_eval.prepare(scenario, poor, tmp_path, site, with_spec=False)
    assert not (poor / "SPEC.md").exists()
    assert "SPEC.md" not in (poor / "README.md").read_text(encoding="utf-8")
    assert run_eval.git(poor, "ls-files").split() == sorted(
        [".gitignore", "README.md", "pyproject.toml"]
    )
    full = tmp_path / "full"
    run_eval.prepare(scenario, full, tmp_path, site)
    assert (full / "SPEC.md").is_file()


def test_the_poor_and_casual_prompts_do_not_point_at_the_specification() -> None:
    tasks = EVALUATION / "tasks"
    for path in [*tasks.glob("*-casual.txt"), *tasks.glob("*-poor.yaml")]:
        assert "SPEC.md" not in path.read_text(encoding="utf-8"), path.name


def test_direct_po_asks_and_passes_the_answers_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import direct_po

    def fake_claude(prompt: str, cwd: Path, model: str, log: Path, **kw: Any) -> dict[str, Any]:
        assert kw["read_only"] is True
        return {"resultText": "```yaml\nquestions:\n  - |\n    ¿Cupones?\n```", "wallSeconds": 1}

    monkeypatch.setattr(direct_po, "run_claude", fake_claude)
    asked: list[dict[str, Any]] = []

    def owner(task: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        asked.append(request)
        return {"answers": {"Q-1": "SAVE10 descuenta 10 %."}, "addCriteria": ["Cupón SAVE10."]}

    prompt, info = direct_po.ask("haz shipping_cost", {}, tmp_path, "m", tmp_path, owner)
    assert asked == [{"questions": [{"questionId": "Q-1", "text": "¿Cupones?"}]}]
    assert info == {"questions": ["¿Cupones?"], "answered": 1, "addedCriteria": 1}
    assert "Respuesta: SAVE10 descuenta 10 %." in prompt
    assert "- Cupón SAVE10." in prompt
    call = json.loads((tmp_path / "agent-calls" / "call-1.json").read_text(encoding="utf-8"))
    assert call["kind"] == direct_po.QUESTIONS_KIND


def test_direct_po_is_not_a_default_condition() -> None:
    import run_matrix

    assert "direct-po" in run_eval.CONDITION_CHOICES
    launchers = (EVALUATION / "launchers").glob("*.sh")
    assert all("direct-po" not in path.read_text(encoding="utf-8") for path in launchers)
    assert "direct-po" not in run_matrix.plan("m", 1, 1, ["greenfield"])[0]
