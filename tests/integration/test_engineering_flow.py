"""Wave 6 end to end (#56): standards cards in the implement request and the review checklist,
the architecture survey and options with a person's decision, the project setup questions,
TDD evidence, BDD scenarios and the embedded session provider. The agent is a fixture command
provider (a Python script that calls no model) or the simulated provider."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.models import Finding, ValidationResult
from tests.conftest import GIT_ENV, GIT_ISOLATION, without_agent_results

AGENT = """\
import json, sys
from pathlib import Path

request = json.load(sys.stdin)
log = Path(LOG)
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(request)
log.write_text(json.dumps(calls))
kind = request.get("kind", "implement")

def answer(result):
    print(json.dumps({"status": "PASSED", "summary": kind, "result": result}))
    sys.exit(0)

if kind == "clarify":
    answer({"questions": []})
if kind == "review":
    answer({"findings": []})
if kind == "architecture":
    if request.get("mode") == "advise":
        answer({"options": [
            {"id": "hex", "style": "hexagonal", "title": "Hexagonal core",
             "benefits": ["testable core"], "costs": ["more interfaces"], "recommended": True,
             "layers": [{"name": "core", "paths": ["src/app/core/**"]},
                        {"name": "adapters", "paths": ["src/app/adapters/**"]}],
             "allow": {"adapters": ["core"]}},
            {"id": "layered", "style": "layered", "title": "Layered",
             "benefits": ["simple"], "costs": ["coupling"], "recommended": False,
             "layers": [], "allow": {}},
        ]})
    answer({"style": "layered", "summary": "A pricing core and an infrastructure layer.",
            "layers": [{"name": "core", "paths": ["src/sample/pricing.py"]},
                       {"name": "infra", "paths": ["src/sample/infra/**"]}],
            "allow": {"infra": ["core"]}})
if kind == "acceptance":
    answer({"tests": [{"path": request["directory"] + "/discount.feature", "content": FEATURE}]})
for path, text in FILES.items():
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
print(json.dumps({"status": "PASSED", "summary": "implemented"}))
"""

FEATURE = (
    "Feature: Threshold discount\n"
    "  Scenario: AC-1 a subtotal at the threshold is discounted\n"
    "    Given a subtotal of 100 and a threshold of 100\n"
    "    When the discount of 10 percent applies\n"
    "    Then the total is 90\n"
)

GOOD = (
    "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
    "    return subtotal * (1 - rate) if subtotal >= threshold else subtotal\n"
)
TEST_AT_THRESHOLD = (
    "from sample import apply_discount\n\n"
    "def test_below_threshold() -> None:\n"
    "    assert apply_discount(99, 100, 0.1) == 99\n\n"
    "def test_at_threshold() -> None:\n"
    "    assert apply_discount(100, 100, 0.1) == 90\n"
)

TASK = (
    "taskId: task_wave6\n"
    "title: Threshold discount\n"
    "intent: Apply the configured discount at or above the threshold.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: apply_discount(100, 100, 0.1) returns 90.\n"
    "metadata:\n"
    "  ownedPaths: OWNED\n"
)


def configure(
    workspace: Path,
    tmp_path: Path,
    files: dict[str, str],
    sections: dict[str, Any],
    *,
    provider: str = "fixture_agent",
) -> Path:
    log = tmp_path / "calls.json"
    script = (
        AGENT.replace("LOG", repr(str(log)))
        .replace("FILES", repr(files))
        .replace("FEATURE", repr(FEATURE))
    )
    (tmp_path / "agent.py").write_text(script, encoding="utf-8")
    path = workspace / ".harness" / "project.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agentProvider"] = provider
    config["agentProviders"] = {
        "fixture_agent": {"kind": "command", "command": ["python", str(tmp_path / "agent.py")]}
    }
    config["runtime"].update({"agentSandbox": "off", "providerRetries": 0})
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return log


def calls(log: Path, kind: str) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [item for item in json.loads(log.read_text()) if item.get("kind", "implement") == kind]


def create(workspace: Path, tmp_path: Path, owned: list[str], task: str = TASK) -> str:
    source = tmp_path / "task.yaml"
    source.write_text(task.replace("OWNED", json.dumps(owned)), encoding="utf-8")
    return HarnessApplication().create_task(workspace, source).task_id


def findings(workspace: Path, run: str) -> list[Finding]:
    with HarnessApplication()._services(workspace) as services:
        return services.state.list("finding", Finding, execution_id=run)


def validations(workspace: Path, run: str) -> list[ValidationResult]:
    with HarnessApplication()._services(workspace) as services:
        return services.state.list("validation", ValidationResult, execution_id=run)


def events(workspace: Path, run: str) -> list[dict[str, Any]]:
    data = HarnessApplication().trace(workspace, run, "jsonl").decode().splitlines()
    return [json.loads(line) for line in data if line.strip()]


OWNED = ["src/sample/pricing.py", "tests/test_pricing.py"]


def test_standards_cards_reach_the_implement_request_and_the_review_checklist(
    python_workspace: Path, tmp_path: Path
) -> None:
    log = configure(
        python_workspace,
        tmp_path,
        {"src/sample/pricing.py": GOOD, "tests/test_pricing.py": TEST_AT_THRESHOLD},
        {
            "standards": {"packs": ["python"], "cards": "auto", "maxCards": 4},
            "verification": {"principles": {"mode": "warn"}},
            "review": {"agentReview": "warn"},
        },
    )
    task = create(python_workspace, tmp_path, OWNED)
    application = HarnessApplication()
    run = application.start_run(python_workspace, task)
    assert run.current_phase.value == "DECISION"
    implement = calls(log, "implement")[0]
    cards = implement["standards"]["cards"]
    assert 0 < len(cards) <= 4 and all(item["id"].startswith("python.") for item in cards)
    assert "rationale" not in json.dumps(cards)
    assert "standards cards" in implement["instructions"]
    review = calls(log, "review")[0]
    checklist = review["checklist"]
    assert any(item["id"] == "principles.srp" for item in checklist["principles"])
    assert {item["id"] for item in checklist["standards"]} <= {
        "python.resource-handling",
        "python.test-behaviour",
    }
    assert "checklist" in review["instructions"]
    assert any(
        item.validator_id == "harness.principles"
        for item in validations(python_workspace, run.execution_id)
    )
    second = application.start_run(python_workspace, task)
    selected = [
        item["payload"]
        for item in events(python_workspace, second.execution_id)
        if item["eventType"] == "standards.cards.selected"
    ]
    assert selected and selected[0]["reused"] is True


def test_survey_once_then_approved_layers_are_enforced(
    python_workspace: Path, tmp_path: Path
) -> None:
    importer = "from sample.infra.db import save\n\n" + GOOD
    log = configure(
        python_workspace,
        tmp_path,
        {
            "src/sample/pricing.py": importer,
            "src/sample/infra/__init__.py": "",
            "src/sample/infra/db.py": "def save(value: float) -> float:\n    return value\n",
            "tests/test_pricing.py": TEST_AT_THRESHOLD,
        },
        {"architecture": {"mode": "agent"}},
    )
    task = create(
        python_workspace,
        tmp_path,
        [*OWNED, "src/sample/infra/__init__.py", "src/sample/infra/db.py"],
    )
    application = HarnessApplication()
    run = application.start_run(python_workspace, task)
    assert (run.current_phase.value, run.status.value) == ("DISCOVERY", "BLOCKED")
    shown = application.architecture(python_workspace)
    assert shown["state"]["status"] == "PROPOSED"
    assert (
        (python_workspace / ".harness" / "architecture.md")
        .read_text()
        .startswith("# Architecture of the project (layered)")
    )
    decided = application.decide_architecture(
        python_workspace,
        execution_id=run.execution_id,
        digest=shown["state"]["digest"],
        rationale="Matches the code",
        decision=DecisionKind.APPROVE,
        actor_id="human.lead",
    )
    assert decided["architecture"]["status"] == "APPROVED"
    assert decided["execution"]["currentPhase"] == "VERIFICATION"
    violation = [
        item
        for item in findings(python_workspace, run.execution_id)
        if item.rule_id == "architecture.layer-violation"
    ]
    assert violation and violation[0].location is not None
    assert violation[0].location.path == "src/sample/pricing.py"
    # The second run reuses the cached survey: no second architecture call.
    application.start_run(python_workspace, task)
    assert len(calls(log, "architecture")) == 1


def _new_project(root: Path) -> Path:
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text(
        "[project]\nname='app'\nversion='0.1.0'\n\n[tool.pytest.ini_options]\npythonpath=['src']\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".harness/\n")

    def git(*args: str) -> None:
        subprocess.run(["git", *GIT_ISOLATION, *args], cwd=root, check=True, env=GIT_ENV)

    git("init", "-q")
    git("config", "user.email", "fixture@example.com")
    git("config", "user.name", "Fixture")
    git("add", ".")
    git("commit", "-qm", "scaffold")
    HarnessApplication().init(root)
    without_agent_results(root)
    return root


CORE = "def total(items: list[float]) -> float:\n    return sum(items)\n"
CORE_TEST = (
    "from app.core.cart import total\n\ndef test_total() -> None:\n"
    "    assert total([1.0, 2.0]) == 3.0\n"
)
NEW_TASK = (
    "taskId: task_new\n"
    "title: Cart total\n"
    "intent: Sum the prices of a cart.\n"
    "acceptanceCriteria:\n"
    "  - criterionId: AC-1\n"
    "    text: total([1.0, 2.0]) returns 3.0.\n"
    "metadata:\n"
    "  ownedPaths: OWNED\n"
)


def test_new_project_options_choice_and_adr(tmp_path: Path) -> None:
    root = _new_project(tmp_path / "new")
    log = configure(
        root,
        tmp_path,
        {
            "src/app/core/__init__.py": "",
            "src/app/core/cart.py": CORE,
            "tests/test_cart.py": CORE_TEST,
        },
        {"architecture": {"mode": "agent"}},
    )
    task = create(
        root,
        tmp_path,
        ["src/app/core/__init__.py", "src/app/core/cart.py", "tests/test_cart.py"],
        NEW_TASK,
    )
    application = HarnessApplication()
    run = application.start_run(root, task)
    assert (run.current_phase.value, run.status.value) == ("INTENT", "BLOCKED")
    state = application.architecture(root)["state"]
    assert state["status"] == "OPTIONS" and len(state["options"]) == 2
    decided = application.decide_architecture(
        root,
        execution_id=run.execution_id,
        digest=state["digest"],
        rationale="A testable core matters most",
        option="hex",
        actor_id="human.lead",
    )
    assert decided["architecture"]["style"] == "hexagonal"
    adr = (root / ".harness" / "adr" / "ADR-0001-architecture.md").read_text()
    assert "adapters (src/app/adapters/**) may depend on: core" in adr
    assert decided["execution"]["currentPhase"] == "DECISION"
    implement = calls(log, "implement")[0]
    assert implement["architecture"]["style"] == "hexagonal"
    assert len(calls(log, "architecture")) == 1


def test_project_setup_questions_for_a_new_project(tmp_path: Path) -> None:
    root = _new_project(tmp_path / "setup")
    configure(
        root,
        tmp_path,
        {
            "src/app/core/__init__.py": "",
            "src/app/core/cart.py": CORE,
            "tests/test_cart.py": CORE_TEST,
        },
        {"intake": {"criteriaPolicy": "enforce", "projectSetup": "ask"}},
    )
    task = create(
        root,
        tmp_path,
        ["src/app/core/__init__.py", "src/app/core/cart.py", "tests/test_cart.py"],
        NEW_TASK,
    )
    application = HarnessApplication()
    run = application.start_run(root, task)
    assert (run.current_phase.value, run.status.value) == ("INTENT", "BLOCKED")
    request = application.list_clarifications(root, task)["openRequest"]
    targets = {item["target"]: item["questionId"] for item in request["questions"]}
    assert set(targets) == {"project:architecture", "project:testing", "project:standards"}
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "answers": {
                    targets["project:architecture"]: "Hexagonal, ports and adapters",
                    targets["project:testing"]: "TDD",
                    targets["project:standards"]: "default",
                }
            }
        )
    )
    clarified = application.clarify_task(
        root, task_id=task, answers_file=answers, actor_id="human.lead"
    )
    assert "Project setup (testing): TDD" in clarified["task"]["constraints"]
    report = application.project(root)
    assert report["projectSetup"]["testing"] == "tdd"
    assert report["projectSetup"]["architecture"] == "hexagonal"
    assert report["projectSetup"]["standards"] == ["python"]
    assert report["testing"]["strategy"] == "tdd"
    continued = application.continue_run(root, run.execution_id)
    assert continued.current_phase.value != "INTENT"


def test_tdd_red_green_refactor_evidence(python_workspace: Path, tmp_path: Path) -> None:
    configure(
        python_workspace,
        tmp_path,
        {"src/sample/pricing.py": GOOD, "tests/test_pricing.py": TEST_AT_THRESHOLD},
        {"testing": {"strategy": "tdd"}},
    )
    task = create(python_workspace, tmp_path, OWNED)
    application = HarnessApplication()
    run = application.start_run(python_workspace, task)
    assert run.current_phase.value == "DECISION"
    tdd = [
        item
        for item in validations(python_workspace, run.execution_id)
        if item.validator_id == "harness.tdd"
    ]
    assert tdd and tdd[-1].status.value == "PASSED"
    evidence = [
        item["payload"]
        for item in events(python_workspace, run.execution_id)
        if item["eventType"] == "evidence.recorded"
        and "TDD:" in str(item["payload"].get("summary"))
    ]
    assert evidence and "red FAILED" in evidence[0]["summary"]
    assert "green PASSED" in evidence[0]["summary"]


def test_tdd_rejects_tests_written_after_the_code(python_workspace: Path, tmp_path: Path) -> None:
    passing_before = (
        "from sample import apply_discount\n\n"
        "def test_below_threshold() -> None:\n"
        "    assert apply_discount(99, 100, 0.1) == 99\n\n"
        "def test_far_below() -> None:\n"
        "    assert apply_discount(10, 100, 0.1) == 10\n"
    )
    configure(
        python_workspace,
        tmp_path,
        {"src/sample/pricing.py": GOOD, "tests/test_pricing.py": passing_before},
        {"testing": {"strategy": "tdd"}},
    )
    task = create(python_workspace, tmp_path, OWNED)
    run = HarnessApplication().start_run(python_workspace, task)
    assert run.current_phase.value == "VERIFICATION"
    assert any(
        item.rule_id == "tdd.not-red" for item in findings(python_workspace, run.execution_id)
    )


RUNNER = """\
import re, sys
from pathlib import Path

steps = "\\n".join(p.read_text() for p in Path("features/steps").glob("*.py")) if Path("features/steps").is_dir() else ""
missing = []
for feature in Path("features").glob("*.feature"):
    for line in feature.read_text().splitlines():
        match = re.match(r"\\s*(Given|When|Then|And) (.+)", line)
        if match and match.group(2) not in steps:
            missing.append(match.group(2))
print("undefined steps:", missing)
sys.exit(1 if missing else 0)
"""


def test_bdd_scenarios_approved_frozen_then_step_definitions(
    python_workspace: Path, tmp_path: Path
) -> None:
    (python_workspace / "run_features.py").write_text(RUNNER, encoding="utf-8")
    steps = (
        "STEPS = [\n"
        "    'a subtotal of 100 and a threshold of 100',\n"
        "    'the discount of 10 percent applies',\n"
        "    'the total is 90',\n"
        "]\n"
    )
    log = configure(
        python_workspace,
        tmp_path,
        {
            "src/sample/pricing.py": GOOD,
            "tests/test_pricing.py": TEST_AT_THRESHOLD,
            "features/steps/discount_steps.py": steps,
        },
        {"testing": {"strategy": "bdd", "bddCommand": ["python", "run_features.py"]}},
    )
    task = create(python_workspace, tmp_path, [*OWNED, "features/steps/discount_steps.py"])
    application = HarnessApplication()
    run = application.start_run(python_workspace, task)
    assert (run.current_phase.value, run.status.value) == ("SPECIFICATION", "BLOCKED")
    proposal = application.acceptance(python_workspace, run.execution_id)
    assert proposal["format"] == "gherkin"
    assert proposal["tests"][0]["path"] == "features/discount.feature"
    assert "Gherkin" in calls(log, "acceptance")[0]["instructions"]
    decided = application.decide_acceptance(
        python_workspace,
        execution_id=run.execution_id,
        decision=DecisionKind.APPROVE,
        digest=proposal["digest"],
        rationale="The scenarios match the criteria",
        actor_id="human.lead",
    )
    assert decided["acceptanceTests"]["failBefore"]["status"] == "FAILED"
    assert decided["execution"]["currentPhase"] == "DECISION"
    implement = calls(log, "implement")[0]
    assert implement["acceptanceTests"]["format"] == "gherkin"
    assert "step definitions" in implement["instructions"]


def test_session_provider_waits_for_the_session_edits(
    python_workspace: Path, tmp_path: Path
) -> None:
    task_file = tmp_path / "session.yaml"
    task_file.write_text(
        TASK.replace("OWNED", json.dumps(OWNED)).replace("task_wave6", "task_session"),
        encoding="utf-8",
    )
    application = HarnessApplication()
    application.create_task(python_workspace, task_file)
    run = application.start_run(python_workspace, "task_session", provider="session")
    assert (run.current_phase.value, run.status.value) == ("IMPLEMENTATION", "BLOCKED")
    (python_workspace / "src" / "sample" / "pricing.py").write_text(GOOD, encoding="utf-8")
    (python_workspace / "tests" / "test_pricing.py").write_text(TEST_AT_THRESHOLD, encoding="utf-8")
    continued = application.continue_run(python_workspace, run.execution_id)
    assert continued.current_phase.value == "DECISION"
