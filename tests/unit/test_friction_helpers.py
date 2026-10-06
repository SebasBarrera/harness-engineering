"""Pure helpers of #58: change types, affected tests, plan digests, the configuration section,
price estimates, the ``--since`` parser, issue references and the interaction markers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from governed_harness.configuration import initialize_project, load_project_config
from governed_harness.configuration.friction import (
    DEFAULT_FAST_LANE_SKIP,
    FastLaneConfig,
    FrictionConfig,
    MetricsConfig,
    ModelPrice,
)
from governed_harness.domain.enums import ActorType
from governed_harness.domain.models import (
    AcceptanceCriterion,
    Actor,
    Plan,
    PlanStep,
    PreAuthorization,
    Provenance,
    Task,
    utc_now,
)
from governed_harness.events.sqlite_store import StoredEvent
from governed_harness.friction import (
    affected_python_tests,
    change_type,
    exempt_from_tests,
    path_kind,
    plan_digest,
)
from governed_harness.metrics import call_cost, load_prices, parse_since, task_issues
from governed_harness.telemetry.metrics import human_interactions


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("README.md", "documentation"),
        ("docs/guide.rst", "documentation"),
        ("LICENSE", "documentation"),
        ("pyproject.toml", "configuration"),
        (".github/workflows/ci.yml", "configuration"),
        (".gitignore", "configuration"),
        ("src/app.py", "code"),
        ("docs/conf.py", "code"),
        ("tests/test_app.py", "code"),
    ],
)
def test_path_kind(path: str, kind: str) -> None:
    assert path_kind(path) == kind


def test_change_type() -> None:
    assert change_type(["README.md", "docs/a.md"]) == "documentation"
    assert change_type(["pyproject.toml"]) == "configuration"
    assert change_type(["README.md", "setup.cfg"]) == "documentation-configuration"
    assert change_type(["README.md", "src/a.py"]) == "code"
    assert change_type([]) == "code"
    assert exempt_from_tests("documentation") and not exempt_from_tests("code")


def test_affected_python_tests(tmp_path: Path) -> None:
    (tmp_path / "src" / "shop").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "shop" / "pricing.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_pricing.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_other.py").write_text(
        "from shop.pricing import x\n\ndef test_y(): pass\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_unrelated.py").write_text(
        "import json\n\ndef test_z(): pass\n", encoding="utf-8"
    )
    assert affected_python_tests(tmp_path, ["src/shop/pricing.py"]) == [
        "tests/test_other.py",
        "tests/test_pricing.py",
    ]
    assert affected_python_tests(tmp_path, ["tests/test_unrelated.py"]) == [
        "tests/test_unrelated.py"
    ]
    assert affected_python_tests(tmp_path, ["README.md"]) == []


def _plan(step_id: str) -> Plan:
    return Plan(
        plan_id=f"plan_{step_id}",
        execution_id="run_x",
        task_id="task_x",
        steps=(PlanStep(step_id=step_id, description="Apply", capabilities=("filesystem.read",)),),
        validator_ids=("python.pytest",),
        provenance=Provenance(
            actor=Actor(actor_type=ActorType.HARNESS, actor_id="harness.core"),
            core_version="1.0.0",
        ),
    )


def test_plan_digest_ignores_identifiers() -> None:
    assert plan_digest(_plan("step_a"), "sha256:t") == plan_digest(_plan("step_b"), "sha256:t")
    assert plan_digest(_plan("step_a"), "sha256:t") != plan_digest(_plan("step_a"), "sha256:u")


def test_friction_section_is_left_out_when_absent(tmp_path: Path) -> None:
    path = initialize_project(tmp_path)
    written = load_project_config(path)
    assert written.friction is not None and written.friction.fast_lane is not None
    assert written.friction.fast_lane.skipped == DEFAULT_FAST_LANE_SKIP
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop("friction")
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    plain = load_project_config(path)
    assert plain.friction is None
    dumped = plain.model_dump(mode="json", by_alias=True)
    assert "friction" not in dumped and "metrics" not in dumped
    assert FrictionConfig().model_dump(by_alias=True) == {}
    assert FastLaneConfig.model_validate({"mode": False}).mode == "off"


def test_metrics_prices_need_model_ids() -> None:
    with pytest.raises(ValidationError):
        MetricsConfig.model_validate({"prices": {"bad key": {"input": 1, "output": 2}}})


def test_call_cost() -> None:
    prices = {
        "gpt-x": ModelPrice(input=2, output=8, cache=0.5),
        "codex": ModelPrice(input=1, output=1),
    }
    reported = call_cost({"cost_usd": 0.25, "input_tokens": 10}, prices, "codex", "gpt-x")
    assert (reported.reported, reported.source) == (0.25, "reported")
    estimated = call_cost(
        {"input_tokens": 1_000_000, "output_tokens": 500_000, "cache_tokens": 400_000},
        prices,
        "codex",
        "gpt-x",
    )
    assert estimated.source == "estimated"
    assert estimated.estimated == pytest.approx(0.6 * 2 + 0.4 * 0.5 + 0.5 * 8)
    by_provider = call_cost({"input_tokens": 1_000_000}, prices, "codex", None)
    assert by_provider.estimated == pytest.approx(1.0)
    assert call_cost({"input_tokens": 5}, prices, "other", "m").source == "unpriced"
    assert call_cost(None, prices, "codex", None).source == "none"


def test_load_prices(tmp_path: Path) -> None:
    table = tmp_path / "prices.yaml"
    table.write_text("prices:\n  m1:\n    input: 1\n    output: 2\n", encoding="utf-8")
    assert load_prices(table)["m1"].output == 2


def test_parse_since() -> None:
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    assert parse_since("7d", now) == datetime(2026, 9, 28, 12, tzinfo=UTC)
    assert parse_since("2026-10-01") == datetime(2026, 10, 1, tzinfo=UTC)
    assert parse_since(None) is None
    with pytest.raises(ValueError):
        parse_since("yesterday")


def test_task_issues() -> None:
    task = Task(
        task_id="task_x",
        project_id="p",
        title="Fix the export, closes #12",
        intent="Resolves #13 and mentions #14",
        acceptance_criteria=(AcceptanceCriterion(criterion_id="ac", text="It exports"),),
        metadata={"issues": ["#11", 12]},
    )
    assert task_issues(task) == ["11", "12", "13"]


def _event(event_type: str, payload: dict[str, object], actor: str) -> StoredEvent:
    return StoredEvent(
        event_id="e",
        sequence=1,
        execution_id="run_x",
        event_type=event_type,
        occurred_at=utc_now().isoformat(),
        actor={"actorType": actor},
        payload=payload,
        previous_digest=None,
        event_digest="d",
    )


def test_interactions_count_one_act_once() -> None:
    events = [
        _event("decision.preauthorized", {}, "HUMAN"),
        _event("contract.confirmed", {"withPreAuthorization": True}, "HUMAN"),
        _event("human.decision.recorded", {"preAuthorizationId": "p"}, "HARNESS"),
        _event("plan.approval.decided", {}, "HUMAN"),
    ]
    assert human_interactions(events) == {"preauthorization": 1, "plan": 1}


def test_pre_authorisation_needs_every_condition() -> None:
    actor = Actor(actor_type=ActorType.HUMAN, actor_id="human.tester")
    values = {
        "preAuthorizationId": "preauth_x",
        "executionId": "run_x",
        "taskId": "task_x",
        "actor": actor,
        "contractDigest": "sha256:c",
        "taskDigest": "sha256:t",
        "rationale": "ok",
        "expiresAt": utc_now(),
    }
    assert PreAuthorization.model_validate(values).conditions == (
        "gatePassed",
        "noRiskFactors",
        "sizeS",
    )
    with pytest.raises(ValidationError):
        PreAuthorization.model_validate({**values, "conditions": ["gatePassed"]})
