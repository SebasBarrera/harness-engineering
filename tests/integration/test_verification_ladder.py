"""The verification ladder and certification (#55, items 1-6 and 15): levels declared per
criterion, probes with a variant matrix, the preflight on the baseline and the decision to
continue uncertified, deferred verification closed by attached evidence, discriminating
evidence and light mutation, profile capabilities and the manual checklist."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from governed_harness.application import HarnessApplication
from governed_harness.configuration import ConfigurationResolver
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import (
    ConfigurationError,
    NotFoundError,
    PolicyViolationError,
)
from governed_harness.domain.models import DeferredVerification, Finding, utc_now
from governed_harness.orchestration.engine import EngineServices

GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
CLI = (
    "import json\nimport sys\n\nfrom sample import apply_discount\n\n"
    "print(json.dumps({'total': apply_discount(float(sys.argv[1]), 100, 0.1)}))\n"
)
JUNIT_PASSED = (
    '<?xml version="1.0"?>\n<testsuites><testsuite name="e2e" tests="1">'
    '<testcase classname="e2e" name="test_ac_e2e"/></testsuite></testsuites>\n'
)
JUNIT_FAILED = (
    '<?xml version="1.0"?>\n<testsuites><testsuite name="e2e" tests="1">'
    '<testcase classname="e2e" name="test_ac_e2e"><failure message="no"/></testcase>'
    "</testsuite></testsuites>\n"
)
PROBE: dict[str, Any] = {
    "id": "cli",
    "command": ["python", "-m", "sample.cli", "{amount}"],
    "output": "json",
    "variants": [
        {"name": "below", "values": {"amount": "50"}, "env": {"PYTHONPATH": "src"}},
        {"name": "above", "values": {"amount": "200"}, "env": {"PYTHONPATH": "src"}},
    ],
    "assertions": [
        {"kind": "exitCode", "equals": 0},
        {"kind": "jsonPath", "path": "$.total", "present": True},
        {"kind": "differs", "path": "$.total"},
    ],
}


def configure(workspace: Path, ladder: dict[str, Any] | None = None, **sections: Any) -> None:
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["verification"] = {
        "requirementTraceability": "off",
        "ladder": {
            "mode": "enforce",
            "defaultLevel": "L1",
            "preflight": True,
            "capabilityDetection": True,
            **(ladder or {}),
        },
    }
    config["review"] = {"manualChecklist": True}
    for key, value in sections.items():
        if key == "verification":
            config["verification"].update(value)
        else:
            config[key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (workspace / "src" / "sample" / "cli.py").write_text(CLI, encoding="utf-8")


def task(criteria: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "taskId": "task_ladder",
        "title": "Discount at the threshold",
        "intent": "Apply the configured discount at or above the threshold.",
        "acceptanceCriteria": criteria,
        "implementation": {
            "mode": "patch",
            "patches": [
                {"path": "src/sample/pricing.py", "operation": "replace", "content": GOOD},
                {
                    "path": "tests/test_pricing.py",
                    "operation": "append",
                    "content": "\n\ndef test_ac_unit_at_threshold() -> None:\n"
                    "    assert apply_discount(100, 100, 0.1) == 90\n",
                },
            ],
        },
    }
    value.update(extra)
    return value


def start(workspace: Path, tmp_path: Path, value: dict[str, Any]) -> tuple[HarnessApplication, Any]:
    source = tmp_path / "task.yaml"
    source.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    application = HarnessApplication()
    created = application.create_task(workspace, source)
    return application, application.start_run(workspace, created.task_id)


def digest(application: HarnessApplication, workspace: Path, run: str) -> str:
    return str(application.status(workspace, run)["execution"]["changeSetDigest"])


def findings(workspace: Path, run: str) -> list[Finding]:
    return HarnessApplication().list_findings(workspace, run)


def test_a_project_without_the_keys_keeps_its_configuration(python_workspace: Path) -> None:
    resolved = ConfigurationResolver().resolve(python_workspace)
    dumped = resolved.project.model_dump(mode="json", by_alias=True)
    for key in ("environment", "instructions"):
        assert key not in dumped
    assert "stateDir" not in dumped["runtime"]
    assert "isolation" not in dumped["workspace"]
    services = EngineServices.open(resolved)
    try:
        assert services.paths.database == python_workspace.resolve() / ".harness" / "state.db"
        from governed_harness.orchestration.engine import RunEngine

        assert not RunEngine(services).ladder.active
    finally:
        services.close()


def test_init_writes_the_ladder_settings(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n")
    HarnessApplication().init(root)
    settings = HarnessApplication().validate_config(root)["ladder"]
    assert settings["ladder"]["mode"] == "enforce"
    assert settings["ladder"]["defaultLevel"] == "L1"
    assert settings["mutation"] == "warn"
    assert settings["manualChecklist"] is True
    assert settings["operationalContract"] == "batch"
    assert settings["isolation"] == "none"
    assert settings["locate"] is True
    assert settings["state"]["stateDir"] == "auto"
    assert settings["delivery"] == {
        "stage": True,
        "push": False,
        "pullRequest": False,
        "comment": "notClean",
    }


def test_every_rung_is_reached_by_recorded_evidence(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, verification={"probes": [PROBE]})
    application, execution = start(
        python_workspace,
        tmp_path,
        task(
            [
                {
                    "criterionId": "ac_unit",
                    "text": "apply_discount(100, 100, 0.1) returns 90.",
                    "verification": {"level": "L1"},
                },
                {
                    "criterionId": "ac_cli",
                    "text": "The command line prints the discounted total.",
                    "verification": {"level": "L3", "probe": "cli"},
                },
                {
                    "criterionId": "ac_e2e",
                    "text": "The checkout shows the discount end to end.",
                    "verification": {"level": "L4", "deferred": "CI job e2e"},
                },
                {
                    "criterionId": "ac_look",
                    "text": "The receipt shows the discount line.",
                    "verification": {"level": "L5", "manual": "The receipt layout is right"},
                },
            ],
            checklist=["The receipt prints on the narrow printer"],
        ),
    )
    run = execution.execution_id
    state = application.verification(python_workspace, run)
    assert execution.current_phase is PhaseId.DECISION, state["preflight"]["reasons"]
    assert state["preflight"]["status"] == "PARTIAL"
    by_id = {item["criterionId"]: item for item in state["certification"]["criteria"]}
    assert by_id["ac_unit"]["status"] == "CERTIFIED"
    assert by_id["ac_unit"]["achieved"] == "L1"
    assert by_id["ac_cli"]["achieved"] == "L3"
    assert by_id["ac_e2e"]["status"] == "PENDING"
    assert by_id["ac_look"]["status"] == "PENDING"
    assert state["certification"]["status"] == "PARTIAL"
    brief = application.review(python_workspace, run)
    assert brief["gate"]["status"] == "PASSED"
    assert "CERTIFICATION_PARTIAL" in [item["code"] for item in brief["gate"]["reasons"]]
    assert [item["itemId"] for item in brief["checklist"]] == ["ac_look", "CL-1"]
    current = digest(application, python_workspace, run)
    with pytest.raises(PolicyViolationError, match="checklist items need a person"):
        application.decide_gate(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE,
            change_set_digest=current,
            actor_id="human.reviewer",
            rationale="ok",
            checked_items=("ac_look",),
        )
    with pytest.raises(ConfigurationError, match="unknown checklist item"):
        application.decide_gate(
            python_workspace,
            execution_id=run,
            decision=DecisionKind.APPROVE,
            change_set_digest=current,
            actor_id="human.reviewer",
            rationale="ok",
            checked_items=("nope",),
        )
    record, closed = application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=current,
        actor_id="human.reviewer",
        rationale="checked the receipt",
        checked_items=("ac_look", "CL-1"),
    )
    assert record.checked_items == ("ac_look", "CL-1")
    assert closed.status is ResultStatus.PASSED
    inbox = application.inbox(python_workspace)
    assert [(item["kind"], item["itemId"]) for item in inbox if item["kind"] == "deferred"] == [
        ("deferred", "D-ac_e2e")
    ]
    failing = tmp_path / "failing.xml"
    failing.write_text(JUNIT_FAILED, encoding="utf-8")
    report = application.attach_evidence(
        python_workspace, file=failing, execution_id=run, item="D-ac_e2e", actor_id="human.ci"
    )
    assert report["deferred"]["status"] == "FAILED"
    with pytest.raises(PolicyViolationError, match="already FAILED"):
        application.attach_evidence(
            python_workspace, file=failing, execution_id=run, item="D-ac_e2e", actor_id="human.ci"
        )
    state = application.verification(python_workspace, run)
    assert state["certification"]["status"] == "PARTIAL"
    assert {item["criterionId"]: item["status"] for item in state["certification"]["criteria"]}[
        "ac_e2e"
    ] == "NOT_CERTIFIED"


def test_deferred_evidence_closes_the_item_and_certifies(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    application, execution = start(
        python_workspace,
        tmp_path,
        task(
            [
                {
                    "criterionId": "ac_unit",
                    "text": "apply_discount(100, 100, 0.1) returns 90.",
                    "verification": {"level": "L1"},
                },
                {
                    "criterionId": "ac_e2e",
                    "text": "The checkout shows the discount end to end.",
                    "verification": {"level": "L4", "deferred": "CI job e2e"},
                },
            ]
        ),
    )
    run = execution.execution_id
    current = digest(application, python_workspace, run)
    application.decide_gate(
        python_workspace,
        execution_id=run,
        decision=DecisionKind.APPROVE,
        change_set_digest=current,
        actor_id="human.reviewer",
        rationale="ok",
    )
    status = json.dumps({"state": "success", "sha": "0" * 40, "context": "ci/e2e"})
    report_file = tmp_path / "status.json"
    report_file.write_text(status, encoding="utf-8")
    deferred = application.verification(python_workspace, run)["deferred"][0]
    assert deferred["commit"]  # bound to the closure commit at CLOSURE
    with pytest.raises(PolicyViolationError, match="is about commit"):
        application.attach_evidence(
            python_workspace,
            file=report_file,
            execution_id=run,
            item="D-ac_e2e",
            actor_id="human.ci",
        )
    passing = tmp_path / "e2e.xml"
    passing.write_text(JUNIT_PASSED, encoding="utf-8")
    with pytest.raises(NotFoundError):
        application.attach_evidence(
            python_workspace, file=passing, execution_id=run, item="D-other", actor_id="human.ci"
        )
    report = application.attach_evidence(
        python_workspace, file=passing, execution_id=run, item="D-ac_e2e", actor_id="human.ci"
    )
    assert report["deferred"]["status"] == "PASSED"
    assert report["certification"] == "CERTIFIED"
    events = [item.event_type for item in _events(python_workspace, run)]
    assert "verification.deferred.closed" in events
    assert events[-1] == "certification.recorded"
    assert HarnessApplication().verify(python_workspace, run)["valid"]


def _events(workspace: Path, run: str) -> list[Any]:
    resolved = ConfigurationResolver().resolve(workspace)
    services = EngineServices.open(resolved)
    try:
        return services.events.list(run)
    finally:
        services.close()


def test_an_expired_deferred_item_cannot_be_closed(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace)
    application, execution = start(
        python_workspace,
        tmp_path,
        task(
            [
                {
                    "criterionId": "ac_unit",
                    "text": "apply_discount(100, 100, 0.1) returns 90.",
                    "verification": {"level": "L4", "deferred": "staging"},
                }
            ]
        ),
    )
    run = execution.execution_id
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        item = services.state.list("deferred_verification", DeferredVerification, execution_id=run)[
            0
        ]
        expired = item.model_copy(update={"expires_at": item.created_at - timedelta(days=1)})
        services.state.put(
            "deferred_verification",
            item.deferred_id,
            expired,
            execution_id=run,
            project_id=item.project_id,
        )
    finally:
        services.close()
    inbox = application.inbox(python_workspace)
    assert any(entry.get("status") == "EXPIRED" for entry in inbox)
    passing = tmp_path / "e2e.xml"
    passing.write_text(JUNIT_PASSED, encoding="utf-8")
    with pytest.raises(PolicyViolationError, match="expired"):
        application.attach_evidence(
            python_workspace, file=passing, execution_id=run, item="D-ac_unit", actor_id="human.ci"
        )


def test_the_inbox_warns_before_a_deferred_item_expires(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    application, execution = start(
        python_workspace,
        tmp_path,
        task(
            [
                {
                    "criterionId": "ac_unit",
                    "text": "apply_discount(100, 100, 0.1) returns 90.",
                    "verification": {"level": "L4", "deferred": "staging"},
                }
            ]
        ),
    )
    run = execution.execution_id
    inbox = [
        entry for entry in application.inbox(python_workspace) if entry.get("kind") == "deferred"
    ]
    assert [entry["status"] for entry in inbox] == ["PENDING"]
    assert inbox[0]["warning"] is None
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        item = services.state.list("deferred_verification", DeferredVerification, execution_id=run)[
            0
        ]
        soon = item.model_copy(update={"expires_at": utc_now() + timedelta(hours=30)})
        services.state.put(
            "deferred_verification",
            item.deferred_id,
            soon,
            execution_id=run,
            project_id=item.project_id,
        )
    finally:
        services.close()
    inbox = [
        entry for entry in application.inbox(python_workspace) if entry.get("kind") == "deferred"
    ]
    assert [entry["status"] for entry in inbox] == ["PENDING"]
    assert inbox[0]["warning"] == "expires within 2 day(s)"
    assert inbox[0]["next"].startswith(f"harness evidence attach --run {run} --item D-ac_unit")


def test_a_declared_rung_that_is_not_reached_fails_the_verification(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    value = task(
        [
            {
                "criterionId": "ac_rounding",
                "text": "apply_discount(100, 100, 0.1) returns 90.",
                "verification": {"level": "L1"},
            }
        ]
    )
    application, execution = start(python_workspace, tmp_path, value)
    assert execution.current_phase is PhaseId.VERIFICATION
    assert execution.status is ResultStatus.FAILED
    found = [item for item in findings(python_workspace, execution.execution_id)]
    rules = {(item.rule_id, item.severity.value) for item in found}
    assert ("certification.level-not-reached", "HIGH") in rules


def test_warn_reports_the_gap_without_blocking(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, ladder={"mode": "warn"})
    value = task(
        [
            {
                "criterionId": "ac_rounding",
                "text": "apply_discount(100, 100, 0.1) returns 90.",
                "verification": {"level": "L2"},
            }
        ]
    )
    application, execution = start(python_workspace, tmp_path, value)
    assert execution.current_phase is PhaseId.DECISION
    rules = {
        (item.rule_id, item.severity.value)
        for item in findings(python_workspace, execution.execution_id)
    }
    assert ("certification.level-not-reached", "LOW") in rules
    certification = application.verification(python_workspace, execution.execution_id)[
        "certification"
    ]
    assert certification["status"] == "NOT_CERTIFIED"


def test_undeclared_criteria_report_the_default_level_without_blocking(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    value = task([{"criterionId": "ac_other", "text": "apply_discount(100, 100, 0.1) returns 90."}])
    application, execution = start(python_workspace, tmp_path, value)
    assert execution.current_phase is PhaseId.DECISION
    criteria = application.verification(python_workspace, execution.execution_id)["certification"][
        "criteria"
    ]
    assert criteria[0]["declared"] is False
    assert criteria[0]["status"] == "NOT_CERTIFIED"
    assert criteria[0]["achieved"] == "L0"


def test_an_unavailable_probe_blocks_until_a_person_decides(
    python_workspace: Path, tmp_path: Path
) -> None:
    missing = {**PROBE, "id": "device", "command": ["simulator-that-is-not-installed", "{amount}"]}
    configure(
        python_workspace,
        verification={"probes": [missing]},
        capabilities={
            "default": "deny",
            "grants": [
                {"capability": "process.execute", "scope": ["simulator-that-is-not-installed"]}
            ],
        },
    )
    value = task(
        [
            {
                "criterionId": "ac_unit",
                "text": "apply_discount(100, 100, 0.1) returns 90.",
                "verification": {"level": "L1"},
            },
            {
                "criterionId": "ac_device",
                "text": "The device shows the total.",
                "verification": {"level": "L3", "probe": "device"},
            },
        ]
    )
    application, execution = start(python_workspace, tmp_path, value)
    run = execution.execution_id
    assert execution.current_phase is PhaseId.PLANNING
    assert execution.status is ResultStatus.BLOCKED
    inbox = application.inbox(python_workspace)
    assert [item["kind"] for item in inbox] == ["preflight"]
    state = application.verification(python_workspace, run)
    assert state["preflight"]["status"] == "UNAVAILABLE"
    assert "device" in " ".join(state["preflight"]["reasons"])
    with pytest.raises(PolicyViolationError):
        application.decide_verification(
            python_workspace, execution_id=run, rationale="", actor_id="human.lead"
        )
    with pytest.raises(PolicyViolationError):
        application.decide_verification(
            python_workspace, execution_id=run, rationale="no device here", actor_id="agent.x"
        )
    result = application.decide_verification(
        python_workspace, execution_id=run, rationale="No device lab here", actor_id="human.lead"
    )
    assert result["decision"]["criteria"] == ["ac_device"]
    assert result["decision"]["probes"] == ["device"]
    assert result["execution"]["currentPhase"] == "DECISION"
    certification = application.verification(python_workspace, run)["certification"]
    by_id = {item["criterionId"]: item["status"] for item in certification["criteria"]}
    assert by_id == {"ac_unit": "CERTIFIED", "ac_device": "WAIVED"}
    assert certification["status"] == "PARTIAL"
    validations = application.status(python_workspace, run)["validationSummary"]["byStatus"]
    assert validations.get("NOT_APPLICABLE")


def test_a_failing_probe_is_a_failed_verification(python_workspace: Path, tmp_path: Path) -> None:
    wrong = {
        **PROBE,
        "assertions": [{"kind": "jsonPath", "path": "$.total", "equals": 1}],
    }
    configure(python_workspace, verification={"probes": [wrong]})
    value = task(
        [
            {
                "criterionId": "ac_cli",
                "text": "The command line prints the discounted total.",
                "verification": {"level": "L3", "probe": "cli"},
            }
        ]
    )
    _application, execution = start(python_workspace, tmp_path, value)
    assert execution.current_phase is PhaseId.VERIFICATION
    assert execution.status is ResultStatus.FAILED
    messages = [
        item.message
        for item in findings(python_workspace, execution.execution_id)
        if item.rule_id == "probe.assertion-failed"
    ]
    assert len(messages) == 2
    assert "variant below" in messages[0]


def test_light_mutation_finds_an_untested_change(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, verification={"mutation": {"mode": "enforce", "maxHunks": 5}})
    extra = (
        GOOD + "\n\ndef shipping(total: float) -> float:\n    return 0.0 if total > 50 else 4.5\n"
    )
    value = task([{"criterionId": "ac_unit", "text": "apply_discount(100, 100, 0.1) returns 90."}])
    value["implementation"]["patches"][0]["content"] = extra
    _application, execution = start(python_workspace, tmp_path, value)
    found = [
        item
        for item in findings(python_workspace, execution.execution_id)
        if item.rule_id == "tests.change-not-exercised"
    ]
    assert execution.current_phase is PhaseId.VERIFICATION
    assert len(found) == 1
    assert found[0].location
    assert found[0].location.path == "src/sample/pricing.py"
    assert found[0].severity.value == "HIGH"


def test_light_mutation_classifies_new_tests(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, verification={"mutation": {"mode": "warn"}})
    value = task([{"criterionId": "ac_unit", "text": "apply_discount(100, 100, 0.1) returns 90."}])
    value["implementation"]["patches"].append(
        {
            "path": "tests/test_weak.py",
            "operation": "create",
            "content": "from sample import apply_discount\n\n\ndef test_below() -> None:\n"
            "    assert apply_discount(1, 100, 0.1) == 1\n",
        }
    )
    application, execution = start(python_workspace, tmp_path, value)
    assert execution.current_phase is PhaseId.DECISION
    rules = [item.rule_id for item in findings(python_workspace, execution.execution_id)]
    assert "tests.weak" in rules
    assert "tests.change-not-exercised" not in rules
    summaries = [
        item["summary"]
        for item in application.list_evidence(python_workspace, execution.execution_id)[0][
            "evidence"
        ]
    ]
    assert any("1 of 2 new test file(s) discriminating" in item for item in summaries), summaries


def test_a_task_attachment_reaches_the_request_as_intake_context(
    python_workspace: Path, tmp_path: Path
) -> None:
    configure(python_workspace)
    shot = tmp_path / "screen.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nnot really an image")
    source = tmp_path / "task.yaml"
    source.write_text(
        yaml.safe_dump(
            task([{"criterionId": "ac_unit", "text": "apply_discount(100, 100, 0.1) returns 90."}])
        ),
        encoding="utf-8",
    )
    application = HarnessApplication()
    application.create_task(python_workspace, source)
    attached = application.attach_evidence(
        python_workspace,
        file=shot,
        task_id="task_ladder",
        manual=True,
        note="the bug as the customer saw it",
        actor_id="human.reporter",
    )
    assert attached["taskDigest"]
    assert attached["mediaType"] == "image/png"
    with pytest.raises(ConfigurationError, match="--manual"):
        application.attach_evidence(
            python_workspace, file=shot, task_id="task_ladder", actor_id="human.reporter"
        )
    resolved = ConfigurationResolver().resolve(python_workspace)
    services = EngineServices.open(resolved)
    try:
        from governed_harness.orchestration.engine import RunEngine

        engine = RunEngine(services)
        listed = engine.ladder.intake_attachments(engine.get_task("task_ladder"))
    finally:
        services.close()
    assert listed[0]["fileName"] == "screen.png"
    assert Path(listed[0]["path"]).read_bytes() == shot.read_bytes()


def test_extended_profiles_are_detected_only_with_the_key(tmp_path: Path) -> None:
    root = tmp_path / "go-service"
    root.mkdir()
    (root / "go.mod").write_text("module example.invalid/service\n\ngo 1.22\n")
    (root / ".harness").mkdir()
    base = {
        "configVersion": "1.0",
        "projectId": "project_go",
        "workspace": {"root": ".."},
        "toolchain": {"profileDetection": "all"},
    }
    (root / ".harness" / "project.yaml").write_text(yaml.safe_dump(base))
    resolver = ConfigurationResolver()
    with pytest.raises(ConfigurationError, match="no supported technology profile"):
        resolver.resolve(root)
    base["toolchain"] = {"profileDetection": "all", "extendedProfiles": True}
    (root / ".harness" / "project.yaml").write_text(yaml.safe_dump(base))
    resolved = ConfigurationResolver().resolve(root)
    assert [item.profile_id for item in resolved.profiles] == ["go_default"]
    assert [item.validator_id for item in resolved.effective_validators] == ["go.test", "go.vet"]
    # Tests of a technology the traceability corpus does not parse are found by their text.
    (root / "total_test.go").write_text(
        'package service\n\nfunc TestTotal(t *testing.T) { t.Log("ac_total") }\n'
    )
    (root / ".git").mkdir()
    (root / ".git" / "ignored_test.go").write_text("// ac_total\n")
    from governed_harness.domain.models import Task
    from governed_harness.orchestration.engine import RunEngine

    services = EngineServices.open(resolved)
    try:
        task = Task.model_validate(
            {
                "taskId": "t",
                "projectId": "project_go",
                "title": "Total",
                "intent": "Show the total.",
                "acceptanceCriteria": [{"criterionId": "ac_total", "text": "The total shows."}],
            }
        )
        named = RunEngine(services).ladder.named_tests(task)
    finally:
        services.close()
    assert [item.path for item in named["ac_total"]] == ["total_test.go"]
