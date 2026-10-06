"""Capabilities per phase in a governed run (#4, governance.phaseCapabilities)."""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration.resolver import ConfigurationResolver
from governed_harness.domain.enums import PhaseId

TASK = (
    "taskId: task_phase\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "implementation:\n"
    "  mode: patch\n"
    "  patches:\n"
    "    - path: src/sample/pricing.py\n"
    "      operation: replace\n"
    "      content: |\n"
    "        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "            return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)


def _enable(root: Path, grants: list[dict[str, object]] | None = None) -> None:
    path = root / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.setdefault("governance", {})["phaseCapabilities"] = True
    if grants is not None:
        config["capabilities"]["grants"] = grants
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_without_the_key_grants_are_per_run_as_before(python_workspace: Path) -> None:
    before = ConfigurationResolver().resolve(python_workspace)
    _enable(python_workspace)
    after = ConfigurationResolver().resolve(python_workspace)
    # The profile's grants are the same; the phases now also allow what the harness runs.
    assert before.effective_capabilities == after.effective_capabilities
    phases = {item.phase_id: item.allowed_capabilities for item in after.workflow.phases}
    assert "process.execute" in phases[PhaseId.SPECIFICATION]
    assert phases[PhaseId.INTENT] == ("filesystem.read",)


def test_the_project_narrows_the_profile(python_workspace: Path) -> None:
    _enable(
        python_workspace,
        [
            {"capability": "filesystem.write", "scope": ["src/**"]},
            {"capability": "process.execute", "scope": ["make"]},
        ],
    )
    resolved = ConfigurationResolver().resolve(python_workspace)
    rules = {rule.capability: rule.scope for rule in resolved.effective_capabilities}
    assert rules["filesystem.write"] == ("src/**",)
    assert "make" not in rules.get("process.execute", ())
    # A scope no profile grants is added explicitly with capabilities.extend.
    path = python_workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["capabilities"]["extend"] = [{"capability": "process.execute", "scope": ["make"]}]
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    resolved = ConfigurationResolver().resolve(python_workspace)
    rules = {rule.capability: rule.scope for rule in resolved.effective_capabilities}
    assert "make" in rules["process.execute"]


def test_each_phase_records_its_resolved_grants(python_workspace: Path, tmp_path: Path) -> None:
    _enable(python_workspace)
    source = tmp_path / "task.yaml"
    source.write_text(TASK, encoding="utf-8")
    application = HarnessApplication()
    application.create_task(python_workspace, source)
    run = application.start_run(python_workspace, "task_phase")
    assert run.current_phase is PhaseId.DECISION
    with application._services(python_workspace) as services:
        events = [
            item.payload
            for item in services.events.list(run.execution_id)
            if item.event_type == "capabilities.resolved"
        ]
    by_phase = {item["phase"]: item for item in events}
    assert {"INTENT", "IMPLEMENTATION", "VERIFICATION", "INDEPENDENT_REVIEW"} <= set(by_phase)
    intent = {item["capability"] for item in by_phase["INTENT"]["validators"]}
    assert intent == {"filesystem.read"}
    implementation = {item["capability"] for item in by_phase["IMPLEMENTATION"]["agent"]["grants"]}
    assert "filesystem.write" in implementation
    review = {item["capability"] for item in by_phase["INDEPENDENT_REVIEW"]["agent"]["grants"]}
    assert "filesystem.write" not in review and "process.execute" not in review
    assert all(item["evidenceRef"].startswith("artifact://") for item in events)
