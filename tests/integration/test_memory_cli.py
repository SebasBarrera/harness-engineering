from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from governed_harness.application import HarnessApplication
from governed_harness.cli.main import app
from governed_harness.domain.enums import MemoryLevel


def invoke(workspace: Path, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(app, [*args, "--path", str(workspace)])
    return result.exit_code, json.loads(result.stdout) if result.stdout.strip() else None


def statuses(workspace: Path) -> dict[str, str]:
    code, listing = invoke(workspace, "memory", "list")
    assert code == 0
    assert isinstance(listing, list)
    return {item["record"]["memoryId"]: item["status"] for item in listing}


def test_memory_lifecycle_through_the_cli(python_workspace: Path) -> None:
    """A proposal enters no context until a person approves it, and an invalidation removes it
    again; every step stays on record with its actor."""
    code, proposal = invoke(
        python_workspace,
        "memory",
        "add",
        "--level",
        "project",
        "--key",
        "money.rounding",
        "--value",
        "Round money half up to two places.",
        "--actor",
        "human.author",
    )
    assert code == 0
    assert isinstance(proposal, dict)
    assert proposal["value"] == {"text": "Round money half up to two places."}
    assert proposal["approved"] is False
    assert proposal["provenance"]["actor"]["actorId"] == "human.author"
    assert statuses(python_workspace) == {proposal["memoryId"]: "unapproved"}

    code, approved = invoke(
        python_workspace,
        "memory",
        "approve",
        "--memory",
        proposal["memoryId"],
        "--actor",
        "human.lead",
    )
    assert code == 0
    assert isinstance(approved, dict)
    assert approved["supersedes"] == proposal["memoryId"]
    assert approved["provenance"]["actor"]["actorId"] == "human.lead"
    assert statuses(python_workspace) == {
        proposal["memoryId"]: "superseded",
        approved["memoryId"]: "active",
    }

    code, tombstone = invoke(
        python_workspace,
        "memory",
        "invalidate",
        "--memory",
        approved["memoryId"],
        "--reason",
        "The pricing rules changed.",
        "--actor",
        "human.lead",
    )
    assert code == 0
    assert isinstance(tombstone, dict)
    assert tombstone["value"] == {"invalidated": True, "reason": "The pricing rules changed."}
    assert statuses(python_workspace) == {
        proposal["memoryId"]: "superseded",
        approved["memoryId"]: "superseded",
        tombstone["memoryId"]: "expired",
    }


def test_memory_commands_fail_closed(python_workspace: Path) -> None:
    runner = CliRunner()
    path = ["--path", str(python_workspace)]
    missing_task = runner.invoke(
        app, ["memory", "add", "--level", "task", "--key", "k", "--value", "v", *path]
    )
    assert missing_task.exit_code == 2
    unknown = runner.invoke(app, ["memory", "approve", "--memory", "mem_unknown", *path])
    assert unknown.exit_code == 3
    bad_date = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--level",
            "project",
            "--key",
            "k",
            "--value",
            "v",
            "--valid-until",
            "tomorrow",
            *path,
        ],
    )
    assert bad_date.exit_code == 2
    created = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--level",
            "project",
            "--key",
            "k",
            "--value",
            '{"rule": 1}',
            "--approve",
            *path,
        ],
    )
    assert created.exit_code == 0
    record = json.loads(created.stdout)
    assert record["value"] == {"rule": 1}
    again = runner.invoke(app, ["memory", "approve", "--memory", record["memoryId"], *path])
    assert again.exit_code == 5
    no_reason = runner.invoke(
        app, ["memory", "invalidate", "--memory", record["memoryId"], "--reason", " ", *path]
    )
    assert no_reason.exit_code == 2


def test_memory_manifest_shows_what_a_run_applied(python_workspace: Path, tmp_path: Path) -> None:
    application = HarnessApplication()
    rule = application.add_memory(
        python_workspace,
        level=MemoryLevel.PROJECT,
        key="money.rounding",
        value={"text": "Round money half up to two places."},
        actor_id="human.lead",
        approved=True,
    )
    proposal = application.add_memory(
        python_workspace,
        level=MemoryLevel.PROJECT,
        key="naming",
        value={"text": "Prefer long names."},
        actor_id="human.author",
    )
    task_path = tmp_path / "task.yaml"
    task_path.write_text(
        "title: Example\nintent: Add a regression test\n"
        "requirements:\n  - A subtotal below the threshold is unchanged.\n"
        "acceptanceCriteria:\n  - apply_discount(1, 100, 0.1) returns 1.\n"
        "implementation:\n  mode: patch\n  patches:\n"
        "    - path: tests/test_pricing.py\n      operation: append\n      content: |\n"
        "\n        def test_extra() -> None:\n            assert apply_discount(1, 100, 0.1) == 1\n",
        encoding="utf-8",
    )
    task = application.create_task(python_workspace, task_path)
    run = application.start_run(python_workspace, task.task_id)
    code, manifest = invoke(python_workspace, "memory", "manifest", "--run", run.execution_id)
    assert code == 0
    assert isinstance(manifest, dict)
    assert [item["memoryId"] for item in manifest["records"]] == [rule.memory_id]
    assert manifest["excluded"] == [
        {
            "memoryId": proposal.memory_id,
            "level": "PROJECT",
            "key": "naming",
            "reason": "unapproved",
        }
    ]
    assert manifest["manifestRef"].startswith("artifact://sha256/")
    # The manifest is a record of the run: approving the proposal later does not rewrite it.
    application.approve_memory(
        python_workspace, memory_id=proposal.memory_id, actor_id="human.lead"
    )
    _, later = invoke(python_workspace, "memory", "manifest", "--run", run.execution_id)
    assert later == manifest
    unknown = CliRunner().invoke(
        app, ["memory", "manifest", "--run", "run_unknown", "--path", str(python_workspace)]
    )
    assert unknown.exit_code == 3
