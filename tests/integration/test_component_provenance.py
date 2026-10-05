"""Provenance per component (``provenance.agentSnapshots``): every ChangeSet file is attributed to
the agent invocation that wrote it, and an edit no invocation made is recorded out of band."""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.models import AgentInvocation, ComponentProvenance

TASK = (
    "title: Implement threshold discount\n"
    "intent: Apply a discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - A subtotal of 100 with a ten percent rate returns 90.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
    "    - path: tests/test_pricing.py\n"
    "      operation: append\n"
    "      content: |\n"
    "\n"
    "        def test_at_threshold() -> None:\n"
    "            assert apply_discount(100, 100, 0.1) == 90\n"
)


def start(root: Path) -> tuple[HarnessApplication, str]:
    task_file = root.parent / "task.yaml"
    task_file.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    task = application.create_task(root, task_file)
    run = application.start_run(root, task.task_id)
    assert run.current_phase.value == "DECISION"
    return application, run.execution_id


def records(application: HarnessApplication, root: Path, run: str) -> list[ComponentProvenance]:
    with application._services(root) as services:
        return services.state.list("component_provenance", ComponentProvenance, execution_id=run)


def test_files_are_attributed_and_out_of_band_edits_recorded(python_workspace: Path) -> None:
    application, run = start(python_workspace)
    with application._services(python_workspace) as services:
        invocation = services.state.list("agent_invocation", AgentInvocation, execution_id=run)[-1]
    [first] = records(application, python_workspace, run)
    assert {item.path: item.source for item in first.files} == {
        "src/sample/pricing.py": "AGENT",
        "tests/test_pricing.py": "AGENT",
    }
    assert {item.invocation_id for item in first.files} == {invocation.invocation_id}
    assert first.out_of_band_edits == ()
    brief = application.review(python_workspace, run)
    assert brief["provenance"]["outOfBandFiles"] == 0

    # A person edits a ChangeSet file while the run waits for the decision.
    test_file = python_workspace / "tests" / "test_pricing.py"
    test_file.write_text(test_file.read_text(encoding="utf-8") + "# reviewed\n", encoding="utf-8")
    application.continue_run(python_workspace, run)
    latest = records(application, python_workspace, run)[-1]
    assert latest.change_set_digest != first.change_set_digest
    sources = {item.path: item.source for item in latest.files}
    assert sources == {"src/sample/pricing.py": "AGENT", "tests/test_pricing.py": "OUT_OF_BAND"}
    [edit] = latest.out_of_band_edits
    assert edit.path == "tests/test_pricing.py"
    assert edit.status == "MODIFIED"
    assert edit.after_invocation_id == invocation.invocation_id
    with application._services(python_workspace) as services:
        events = [
            item.payload
            for item in services.events.list(run)
            if item.event_type == "provenance.out-of-band-edit"
        ]
    assert [item["path"] for item in events] == ["tests/test_pricing.py"]
    brief = application.review(python_workspace, run)
    assert brief["provenance"]["outOfBandPaths"] == ["tests/test_pricing.py"]


def test_without_the_key_nothing_is_recorded(python_workspace: Path) -> None:
    path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("provenance")
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    application, run = start(python_workspace)
    assert records(application, python_workspace, run) == []
    with application._services(python_workspace) as services:
        assert services.state.get_flag(f"agentsnapshots:{run}") is None
    brief = application.review(python_workspace, run)
    assert "provenance" not in brief and "selfReports" not in brief
