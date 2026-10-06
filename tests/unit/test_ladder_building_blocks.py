"""The deterministic building blocks of the verification ladder (#55): JSON paths, probe
evaluation, hunks for light mutation, external evidence, certification, the operational
contract and the instruction-file lint."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from governed_harness.configuration.ladder import CapabilityDetection
from governed_harness.domain.enums import ActorType, VerificationLevel
from governed_harness.domain.models import (
    AcceptanceCriterion,
    Actor,
    DeferredVerification,
    ProbeDefinition,
    Task,
    utc_now,
)
from governed_harness.ladder.capabilities import (
    capability_statuses,
    detect,
    load_catalog,
    profile_verification,
)
from governed_harness.ladder.certification import (
    CertificationInputs,
    NamedTest,
    ProbeOutcome,
    certify,
)
from governed_harness.ladder.contract import derive_contract
from governed_harness.ladder.external import (
    EvidenceFormatError,
    detect_kind,
    read_evidence,
)
from governed_harness.ladder.hunks import cosmetic, hunks, revert
from governed_harness.ladder.instructions import lint_instructions
from governed_harness.ladder.jsonpath import parse_path, select
from governed_harness.ladder.probes import VariantRun, evaluate, fill


# ----- JSON paths -----------------------------------------------------------------------------
def test_json_path_subset() -> None:
    document = {"items": [{"id": 2, "tag": "b"}, {"id": 1}], "total": 3, "a-b": True}
    assert select(document, "$.total") == [3]
    assert select(document, "$.items[*].id") == [2, 1]
    assert select(document, "$.items[-1].id") == [1]
    assert select(document, "$['a-b']") == [True]
    assert select(document, "$.items[*].tag") == ["b"]
    assert select(document, "$.missing") == []
    assert select(document, "$.*") == [document["items"], 3, True]
    for bad in ("total", "$..x", "$.items[?(@.id)]", "$[1:2]"):
        with pytest.raises(ValueError):
            parse_path(bad)


# ----- probes ---------------------------------------------------------------------------------
def probe(**values: object) -> ProbeDefinition:
    base: dict[str, object] = {
        "id": "cli",
        "command": ["python", "tool.py", "{amount}"],
        "output": "json",
        "matrix": {"amount": ["10", "1000"]},
        "assertions": [
            {"kind": "exitCode", "equals": 0},
            {"kind": "jsonPath", "path": "$.total", "present": True},
            {"kind": "jsonPath", "path": "$.debug", "present": False},
            {"kind": "differs", "path": "$.total"},
            {"kind": "order", "path": "$.items[*]", "order": "ascending"},
        ],
    }
    base.update(values)
    return ProbeDefinition.model_validate(base)


def run(name: str, payload: object, code: int = 0) -> VariantRun:
    return VariantRun(name, True, code, json.dumps(payload))


def test_probe_variants_and_placeholders() -> None:
    item = probe()
    variants = item.expanded_variants()
    assert [variant.name for variant in variants] == ["amount=10", "amount=1000"]
    assert fill("{amount} and {other}", variants[0].values) == "10 and {other}"
    with pytest.raises(ValueError, match="does not set placeholder"):
        probe(matrix={}, assertions=[{"kind": "exitCode", "equals": 0}])
    with pytest.raises(ValueError, match="at least two variants"):
        probe(
            command=["python", "tool.py"],
            matrix={},
            assertions=[{"kind": "differs", "path": "$.total"}],
        )
    with pytest.raises(ValueError, match="exactly one of"):
        probe(assertions=[{"kind": "jsonPath", "path": "$.a"}])


def test_probe_evaluation_passes_and_fails() -> None:
    item = probe()
    passing = evaluate(
        item,
        [
            run("amount=10", {"total": 10, "items": [1, 2]}),
            run("amount=1000", {"total": 900, "items": [1, 3]}),
        ],
    )
    assert passing.readiness == "READY"
    assert passing.passed
    failing = evaluate(
        item,
        [
            run("amount=10", {"total": 10, "items": [2, 1], "debug": 1}),
            run("amount=1000", {"total": 10, "items": [1]}),
        ],
    )
    assert failing.readiness == "READY"
    assert not failing.passed
    failed = {(result.kind, result.variant) for result in failing.results if not result.passed}
    assert ("jsonPath", "amount=10") in failed
    assert ("differs", None) in failed
    assert ("order", "amount=10") in failed


def test_assertion_variants_narrow_every_kind() -> None:
    # #74: an assertion meant for one variant is checked on that variant only.
    item = probe(
        assertions=[
            {"kind": "exitCode", "equals": 2, "variants": ["amount=10"]},
            {"kind": "exitCode", "equals": 0, "variants": ["amount=1000"]},
            {"kind": "jsonPath", "path": "$.error", "present": True, "variants": ["amount=10"]},
            {"kind": "jsonPath", "path": "$.total", "equals": 900, "variants": ["amount=1000"]},
            {
                "kind": "order",
                "path": "$.items[*]",
                "order": "ascending",
                "variants": ["amount=1000"],
            },
            {"kind": "differs", "path": "$.total"},
        ]
    )
    result = evaluate(
        item,
        [
            run("amount=10", {"error": "below the minimum", "items": [3, 1]}, code=2),
            run("amount=1000", {"total": 900, "items": [1, 3]}),
        ],
    )
    assert result.passed
    checked = [(entry.kind, entry.variant) for entry in result.results]
    assert checked == [
        ("exitCode", "amount=10"),
        ("exitCode", "amount=1000"),
        ("jsonPath", "amount=10"),
        ("jsonPath", "amount=1000"),
        ("order", "amount=1000"),
        ("differs", None),
    ]


def test_assertion_naming_no_variant_fails() -> None:
    item = probe(assertions=[{"kind": "exitCode", "equals": 0, "variants": ["amount=5"]}])
    result = evaluate(item, [run("amount=10", {}), run("amount=1000", {})])
    assert not result.passed
    assert [entry.passed for entry in result.results] == [False]
    assert "amount=5" in result.results[0].detail


def test_probe_that_cannot_run_is_unavailable() -> None:
    item = probe()
    missing = evaluate(
        item,
        [
            VariantRun("amount=10", False, None, "", problem="python not found"),
            run("amount=1000", {"total": 1}),
        ],
    )
    assert missing.readiness == "UNAVAILABLE"
    assert not missing.passed
    garbage = evaluate(
        item,
        [VariantRun("amount=10", True, 0, "not json"), run("amount=1000", {"total": 1})],
    )
    # A program that ran and printed something else is READY: its JSON assertions fail.
    assert garbage.readiness == "READY"
    assert not garbage.passed
    assert any("not JSON" in item.detail for item in garbage.results if not item.passed)
    text = ProbeDefinition.model_validate(
        {
            "id": "greeting",
            "command": ["echo", "hello world"],
            "assertions": [
                {"kind": "text", "contains": "hello"},
                {"kind": "order", "before": "hello", "after": "world"},
                {"kind": "text", "matches": "^bye"},
            ],
        }
    )
    result = evaluate(text, [VariantRun("default", True, 0, "hello world\n")])
    assert [entry.passed for entry in result.results] == [True, True, False]


# ----- hunks ----------------------------------------------------------------------------------
def test_hunks_revert_one_change_at_a_time() -> None:
    before = "a\nb\nc\nd\n"
    after = "a\nB\nc\nd\ne\n"
    found = hunks("file.py", before, after)
    assert [(item.index, item.before_start, item.after_end) for item in found] == [
        (1, 1, 2),
        (2, 4, 5),
    ]
    assert revert(found[0], before, after) == "a\nb\nc\nd\ne\n"
    assert revert(found[1], before, after) == "a\nB\nc\nd\n"
    comment = hunks("file.py", "x = 1\n", "x = 1\n# note\n")[0]
    assert cosmetic(comment, "x = 1\n", "x = 1\n# note\n")
    assert not cosmetic(found[0], before, after)


# ----- external evidence ----------------------------------------------------------------------
JUNIT = b"""<?xml version="1.0"?>
<testsuites><testsuite name="e2e" tests="2">
<testcase classname="e2e.login" name="test_ac_login"/>
<testcase classname="e2e.other" name="test_other"><failure message="boom"/></testcase>
</testsuite></testsuites>"""


def test_external_evidence_formats() -> None:
    assert detect_kind(JUNIT) == "junit"
    verdict = read_evidence(JUNIT)
    assert not verdict.passed
    assert verdict.details
    assert verdict.details["failed"] == 1
    narrowed = read_evidence(JUNIT, case="ac_login")
    assert narrowed.passed
    assert "1 test case(s)" in narrowed.summary
    assert not read_evidence(JUNIT, case="nothing").passed
    sarif = json.dumps(
        {"runs": [{"tool": {"driver": {"name": "scan"}}, "results": [{"level": "warning"}]}]}
    ).encode()
    assert read_evidence(sarif).passed
    status = json.dumps({"state": "success", "sha": "abc123", "context": "ci/e2e"}).encode()
    verdict = read_evidence(status)
    assert verdict.passed
    assert verdict.commit == "abc123"
    pending = json.dumps({"state": "pending"}).encode()
    with pytest.raises(EvidenceFormatError, match="not final"):
        read_evidence(pending)
    with pytest.raises(EvidenceFormatError):
        read_evidence(b"<!DOCTYPE x><testsuites/>")


# ----- certification --------------------------------------------------------------------------
def criterion(cid: str, **verification: object) -> AcceptanceCriterion:
    value: dict[str, object] = {"criterionId": cid, "text": f"{cid} holds"}
    if verification:
        value["verification"] = verification
    return AcceptanceCriterion.model_validate(value)


def deferred(cid: str, status: str) -> DeferredVerification:
    now = utc_now()
    return DeferredVerification(
        deferred_id="deferred_1",
        item_id=f"D-{cid}",
        project_id="p",
        execution_id="run_1",
        task_id="t",
        criterion_id=cid,
        level=VerificationLevel.L4,
        where="CI e2e",
        change_set_digest="sha256:x",
        status=status,  # type: ignore[arg-type]
        expires_at=now + timedelta(days=1),
    )


def test_certification_levels_and_statuses() -> None:
    criteria = [
        criterion("ac_unit"),
        criterion("ac_probe", level="L3", probe="cli"),
        criterion("ac_ci", level="L4", deferred="CI e2e"),
        criterion("ac_eye", level="L5", manual="The dialog looks right"),
        criterion("ac_gap", level="L2"),
    ]
    inputs = CertificationInputs(
        default_level=VerificationLevel.L1,
        verification_passed=True,
        digest="sha256:x",
        passed_validators={"python.pytest"},
        level_validators={VerificationLevel.L1: {"python.pytest"}},
        tests={"ac_unit": [NamedTest("tests/test_a.py::test_ac_unit", "tests/test_a.py")]},
        probes=[ProbeOutcome("cli", VerificationLevel.L3, (), True, "READY")],
        deferred=[deferred("ac_ci", "PENDING")],
    )
    status, results = certify(criteria, inputs)
    by_id = {item.criterion_id: item for item in results}
    assert by_id["ac_unit"].status == "CERTIFIED"
    assert by_id["ac_unit"].achieved == "L1"
    assert by_id["ac_probe"].status == "CERTIFIED"
    assert by_id["ac_probe"].achieved == "L3"
    assert by_id["ac_ci"].status == "PENDING"
    assert by_id["ac_ci"].pending == ("D-ac_ci",)
    assert by_id["ac_eye"].status == "PENDING"
    assert by_id["ac_gap"].status == "NOT_CERTIFIED"
    assert by_id["ac_gap"].achieved == "L0"
    assert status == "PARTIAL"
    inputs.deferred = [deferred("ac_ci", "PASSED")]
    inputs.checked = {"ac_eye"}
    inputs.waived = {"ac_gap"}
    status, results = certify(criteria[:4], inputs)
    assert status == "CERTIFIED"
    _, gap = certify(criteria[4:], inputs)
    assert gap[0].status == "WAIVED"


def test_no_rung_by_omission() -> None:
    inputs = CertificationInputs(
        default_level=VerificationLevel.L1, verification_passed=False, digest="sha256:x"
    )
    status, results = certify([criterion("ac_unit")], inputs)
    assert status == "NOT_CERTIFIED"
    assert results[0].achieved is None
    unavailable = CertificationInputs(
        default_level=VerificationLevel.L1,
        verification_passed=True,
        digest="sha256:x",
        probes=[ProbeOutcome("cli", VerificationLevel.L3, ("ac_probe",), False, "UNAVAILABLE")],
    )
    _, results = certify([criterion("ac_probe", level="L3")], unavailable)
    assert results[0].achieved == "L0"
    assert results[0].status == "NOT_CERTIFIED"


# ----- operational contract -------------------------------------------------------------------
def test_contract_summary_is_bound_to_the_task() -> None:
    task = Task.model_validate(
        {
            "taskId": "t",
            "projectId": "p",
            "title": "Discount",
            "intent": "Apply the discount at the threshold.",
            "requirements": [{"requirementId": "r1", "text": "Discount at the threshold."}],
            "acceptanceCriteria": [{"criterionId": "a", "text": "apply(100) returns 90"}],
            "contract": {"createPullRequest": True, "branch": "feature/discount"},
        }
    )
    summary = derive_contract(task, "sha256:task", {"push": False, "coverageThreshold": 80})
    values = summary.values
    assert values["objective"] == "Apply the discount at the threshold."
    assert values["examples"] == ["apply(100) returns 90"]
    assert values["createPullRequest"] is True
    assert values["push"] is False
    assert "verificationLevel" in summary.missing
    assert "comment" in summary.missing
    other = derive_contract(task, "sha256:other", {"push": False, "coverageThreshold": 80})
    assert other.digest != summary.digest


# ----- instruction files ----------------------------------------------------------------------
def test_instruction_lint(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Use Python 3.11.\nCoverage must stay above 90%.\nNever use --no-verify.\n"
    )
    (tmp_path / "CLAUDE.md").write_text(
        "Use python 3.12 for everything.\nCommit with git commit --no-verify when hooks are slow.\n"
        "Skip the tests when in a hurry.\n"
    )
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)
    (tmp_path / ".cursor" / "rules" / "style.md").write_text("Run git add -A before committing.\n")
    report = lint_instructions(
        tmp_path,
        ["AGENTS.md", "CLAUDE.md", ".cursor/rules", ".github/copilot-instructions.md"],
        ["harness", "AGENTS.md", "CLAUDE.md", ".cursor/rules"],
        coverage_threshold=80,
    )
    assert report["status"] == "FAILED"
    kinds = [item["kind"] for item in report["issues"]]
    assert kinds.count("forbidden-flag") == 2  # the negated --no-verify is not reported
    assert {"tool-version", "coverage", "tests"} <= set(kinds)
    version = next(item for item in report["issues"] if item["kind"] == "tool-version")
    assert version["winner"] == "AGENTS.md"
    assert report["files"] == ["AGENTS.md", "CLAUDE.md", ".cursor/rules/style.md"]
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "AGENTS.md").write_text("Run the tests before you finish.\n")
    assert lint_instructions(clean, ["AGENTS.md"], ["harness"])["status"] == "PASSED"


# ----- capabilities ---------------------------------------------------------------------------
def test_capability_catalog_and_detection(tmp_path: Path) -> None:
    catalog = load_catalog()
    assert {"python_default", "node_default", "swift_default", "android_default", "*"} <= set(
        catalog
    )
    (tmp_path / "tests" / "integration").mkdir(parents=True)

    def runner(argv: tuple[str, ...], cwd: Path, timeout: int) -> tuple[int | None, str]:
        return (0, "pytest 9") if argv[:3] == ("python", "-m", "pytest") else (None, "missing")

    from governed_harness.configuration.loader import load_builtin_profile

    declared = profile_verification([load_builtin_profile("python_default")], catalog)
    statuses = capability_statuses(
        declared, tmp_path, {"python.pytest"}, run_detections=True, timeout=5, runner=runner
    )
    by_level = {(item.profile_id, item.level): item for item in statuses}
    assert by_level[("python_default", "L1")].available
    assert by_level[("python_default", "L2")].available
    assert not by_level[("*", "L4")].available  # docker info could not run
    skipped = capability_statuses(
        declared, tmp_path, {"python.pytest"}, run_detections=False, timeout=5, runner=runner
    )
    unchecked = {(item.profile_id, item.level): item for item in skipped}[("python_default", "L1")]
    assert unchecked.available
    assert not unchecked.detections[0].checked
    expect = CapabilityDetection.model_validate(
        {"command": ["adb", "devices"], "expect": "\\tdevice$"}
    )
    found = detect(expect, tmp_path, 5, lambda *_: (0, "List of devices\nemulator-5554\tdevice"))
    absent = detect(expect, tmp_path, 5, lambda *_: (0, "List of devices attached\n"))
    assert found.available
    assert not absent.available
    assert Actor(actor_type=ActorType.HUMAN, actor_id="human.x").actor_id == "human.x"
