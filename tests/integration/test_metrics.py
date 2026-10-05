"""``harness metrics`` (#58, items 8 to 12): the report of one repository and of the run
registry, the price table, the formats, the self-contained HTML and the narrative command."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import ConfigurationError
from governed_harness.metrics import Filters, render

GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
USAGE_AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
for patch in request["task"]["implementation"]["patches"]:
    Path(patch["path"]).write_text(patch["content"])
print(json.dumps({"status": "PASSED", "summary": "done",
                  "usage": {"inputTokens": 1000, "outputTokens": 200, "cacheTokens": 400}}))
"""


def task_file(tmp_path: Path, task_id: str, **extra: Any) -> Path:
    value: dict[str, Any] = {
        "taskId": task_id,
        "title": "Discount at the threshold, closes #7",
        "intent": "Apply the configured discount at or above the threshold.",
        "acceptanceCriteria": [
            {"criterionId": "ac_at", "text": "apply_discount(100, 100, 0.1) returns 90."}
        ],
        "implementation": {
            "mode": "patch",
            "patches": [{"path": "src/sample/pricing.py", "operation": "replace", "content": GOOD}],
        },
    }
    value.update(extra)
    path = tmp_path / f"{task_id}.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def edit_config(workspace: Path, **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def closed_run(workspace: Path, tmp_path: Path, task_id: str = "task_metrics") -> str:
    application = HarnessApplication()
    task = application.create_task(workspace, task_file(tmp_path, task_id))
    run = application.start_run(workspace, task.task_id)
    digest = application.status(workspace, run.execution_id)["execution"]["changeSetDigest"]
    application.decide_gate(
        workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.APPROVE,
        change_set_digest=digest,
        actor_id="human.tester",
        rationale="Covered by the test",
    )
    return run.execution_id


def test_report_of_a_closed_run(python_workspace: Path, tmp_path: Path) -> None:
    run = closed_run(python_workspace, tmp_path)
    report, _settings = HarnessApplication().metrics(python_workspace, filters=Filters())
    totals = report["totals"]
    assert totals["runs"] == 1 and totals["tasks"] == 1
    assert totals["featuresDelivered"] == 1 and totals["issuesResolved"] == 1
    assert report["delivery"]["issuesResolved"][0]["issue"] == "#7"
    assert totals["approvals"] == 1 and totals["humanInteractions"] == 1
    assert totals["linesAdded"] >= 1
    assert report["quality"]["approvedFirstTime"] == 1
    assert report["runs"][0]["runId"] == run
    assert report["friction"]["perTask"][0]["size"] == "S"
    assert report["trends"]["daily"][0]["runs"] == 1
    assert {row["phase"] for row in report["time"]["perPhase"]} >= {"INTENT", "CLOSURE"}
    # No per-person indicator: the decider's id appears nowhere in the report.
    assert "human.tester" not in json.dumps(report)


def test_costs_are_reported_or_estimated(python_workspace: Path, tmp_path: Path) -> None:
    (python_workspace / "agent.py").write_text(USAGE_AGENT, encoding="utf-8")
    edit_config(
        python_workspace,
        agentProvider="usage",
        agentProviders={"usage": {"kind": "command", "command": ["python", "agent.py"]}},
        runtime={"agentSandbox": "off"},
        metrics={"prices": {"usage": {"input": 2.0, "output": 10.0, "cache": 0.5}}},
    )
    closed_run(python_workspace, tmp_path)
    report, _settings = HarnessApplication().metrics(python_workspace, filters=Filters())
    cost = report["totals"]["cost"]
    assert cost["reportedUsd"] == 0
    # 600 input at 2, 400 cached at 0.5 and 200 output at 10, per million tokens.
    assert cost["estimatedUsd"] == pytest.approx((600 * 2 + 400 * 0.5 + 200 * 10) / 1_000_000)
    assert report["totals"]["tokens"]["cache"] == 400
    assert report["models"][0]["cost"] == "estimated"
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"usage": {"input": 0, "output": 0}}), encoding="utf-8")
    overridden, _ = HarnessApplication().metrics(
        python_workspace, filters=Filters(), prices_file=prices
    )
    assert overridden["totals"]["cost"]["estimatedUsd"] == 0
    filtered, _ = HarnessApplication().metrics(python_workspace, filters=Filters(agent="other"))
    assert filtered["totals"]["runs"] == 0


def test_html_is_self_contained(python_workspace: Path, tmp_path: Path) -> None:
    closed_run(python_workspace, tmp_path)
    report, _settings = HarnessApplication().metrics(python_workspace, filters=Filters())
    page = render(report, "html")
    assert page.startswith("<!doctype html>") and '<html lang="en">' in page
    assert not re.search(r"""(src|href)\s*=\s*["']?https?:""", page)
    assert "<script" not in page and "<link" not in page and "@import" not in page
    assert '<svg viewBox="0 0' in page and 'role="img"' in page and "<desc " in page
    assert "<table>" in page and "prefers-color-scheme: dark" in page


def test_metrics_command_formats_and_filters(python_workspace: Path, tmp_path: Path) -> None:
    closed_run(python_workspace, tmp_path)
    runner = CliRunner()
    base = ["metrics", "--path", str(python_workspace)]
    written = runner.invoke(app, [*base, "--output-dir", str(tmp_path / "out")])
    assert written.exit_code == 0, written.output
    files = json.loads(written.output)["files"]
    assert sorted(Path(item).name for item in files.values()) == [
        "metrics.html",
        "metrics.json",
        "metrics.md",
    ]
    csv = runner.invoke(app, [*base, "--format", "csv"])
    assert csv.output.splitlines()[0].startswith("projectId,runId,taskId,status")
    prometheus = runner.invoke(app, [*base, "--format", "prometheus"])
    assert "harness_runs 1" in prometheus.output
    markdown = runner.invoke(app, [*base, "--format", "md"])
    assert markdown.output.startswith("# Harness metrics")
    future = runner.invoke(app, [*base, "--format", "json", "--since", "2999-01-01"])
    assert json.loads(future.output)["totals"]["runs"] == 0
    other = runner.invoke(app, [*base, "--format", "json", "--task", "task_other"])
    assert json.loads(other.output)["totals"]["runs"] == 0
    assert runner.invoke(app, [*base, "--format", "pdf"]).exit_code == 2
    assert runner.invoke(app, [*base, "--since", "soon"]).exit_code == 2
    html = runner.invoke(
        app, [*base, "--format", "html", "--output", str(tmp_path / "report.html")]
    )
    assert html.exit_code == 0 and (tmp_path / "report.html").read_text().startswith("<!doctype")


def test_all_repositories_of_the_registry(python_workspace: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-project"
    shutil.copytree(python_workspace, other)
    for workspace, project_id in ((python_workspace, "project_one"), (other, "project_two")):
        edit_config(workspace, projectId=project_id, runtime={"stateDir": "auto"})
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    closed_run(python_workspace, tmp_path / "one")
    closed_run(other, tmp_path / "two")
    alone, _ = HarnessApplication().metrics(python_workspace, filters=Filters())
    together, _ = HarnessApplication().metrics(python_workspace, filters=Filters(all_repos=True))
    assert alone["totals"]["runs"] == 1
    assert together["totals"]["runs"] == 2
    assert {item["projectId"] for item in together["projects"]} == {"project_one", "project_two"}


def test_narrative_is_one_call_on_demand(python_workspace: Path, tmp_path: Path) -> None:
    closed_run(python_workspace, tmp_path)
    with pytest.raises(ConfigurationError, match="metrics.narrative"):
        HarnessApplication().metrics(python_workspace, filters=Filters(), narrative=True)
    script = tmp_path / "narrate.py"
    script.write_text(
        "import sys\ndata = sys.stdin.read()\nprint('One run closed.' if 'totals' in data else '')\n",
        encoding="utf-8",
    )
    edit_config(python_workspace, metrics={"narrative": {"command": ["python", str(script)]}})
    report, _ = HarnessApplication().metrics(python_workspace, filters=Filters(), narrative=True)
    assert report["narrative"]["text"] == "One run closed."
    assert "Narrative summary" in render(report, "md")
