"""The simulated person of the evaluation before a dispute of frozen acceptance tests.

``evaluation/person.py`` answers an ``acceptance-dispute`` wait (amendment of 2026-11-11 to the
pre-registration) with the product owner's UPHOLD or REVISE, bound to the digest the inbox shows;
without a product owner the fixed fallback is UPHOLD. A fake harness stands in for the CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

EVALUATION = Path(__file__).resolve().parents[2] / "evaluation"
sys.path.insert(0, str(EVALUATION))

import person  # noqa: E402
import product_owner  # noqa: E402

DIGEST = "sha256:" + "a" * 64
DISPUTED = [{"test": "tests/acceptance/test_ac.py::test_ac", "reason": "str has no rsplit(b'.')"}]
TESTS = [
    {"path": "tests/acceptance/test_ac.py", "content": "def test_ac():\n    assert f() == 1\n"},
    {"path": "tests/acceptance/test_other.py", "content": "def test_other():\n    assert g()\n"},
]
INBOX = {
    "executionId": "run_1",
    "kind": "acceptance-dispute",
    "digest": DIGEST,
    "disputed": DISPUTED,
    "next": f"harness acceptance dispute --run run_1 --decision <UPHOLD|REVISE> --digest {DIGEST}",
}


class FakeHarness:
    """Answers the commands the person sends; records them."""

    def __init__(self, shown_digest: str = DIGEST) -> None:
        self.shown_digest = shown_digest
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, workspace: Path, *args: str) -> Any:
        self.commands.append(args)
        out: Any = {}
        if args[0] == "status":
            out = {"execution": {"currentPhase": "VERIFICATION", "status": "BLOCKED"}}
        elif args[0] == "inbox":
            out = [INBOX]
        elif args[:2] == ("acceptance", "show"):
            out = {
                "tests": TESTS,
                "dispute": {"status": "OPEN", "digest": self.shown_digest, "disputed": DISPUTED},
            }
        elif args[:2] == ("task", "show"):
            out = {"taskId": "task_1", "acceptanceCriteria": [{"text": "f() returns 1"}]}
        elif args[:2] == ("run", "continue"):
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    def dispute_commands(self) -> list[tuple[str, ...]]:
        return [c for c in self.commands if c[:2] == ("acceptance", "dispute")]


class Owner:
    actor = "human.product-owner-simulated"

    def __init__(self, decision: str = "REVISE", fail: bool = False) -> None:
        self.decision = decision
        self.fail = fail
        self.seen: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def judge_dispute(self, task: dict[str, Any], dispute: dict[str, Any]) -> dict[str, str]:
        self.seen.append((task, dispute))
        if self.fail:
            raise ValueError("unreadable reply")
        return {"decision": self.decision, "reason": "The test calls rsplit(b'.') on a str."}


def _wait(tmp_path: Path, harness: FakeHarness, clarifier: Any = None) -> tuple[Any, dict]:
    driver = person.SimulatedPerson(harness, tmp_path, tmp_path, clarifier=clarifier)
    driver.task_id = "task_1"
    code = driver.wait("run_1", "task_1")
    return code, driver.waits[-1]


def _option(command: tuple[str, ...], name: str) -> str:
    return command[command.index(name) + 1]


def test_the_product_owner_revises_a_dispute_bound_to_the_shown_digest(tmp_path: Path) -> None:
    harness, owner = FakeHarness(), Owner("REVISE")
    code, entry = _wait(tmp_path, harness, owner)
    assert code == 0
    assert entry["wait"] == "acceptance-dispute" and entry["source"] == "inbox"
    assert entry["decision"] == "REVISE" and entry["judge"] == "product-owner"
    assert entry["digestMatches"] is True
    assert entry["reasons"] == {
        "agent": [DISPUTED[0]["reason"]],
        "owner": "The test calls rsplit(b'.') on a str.",
    }
    [command] = harness.dispute_commands()
    assert _option(command, "--decision") == "REVISE"
    assert _option(command, "--digest") == DIGEST
    assert _option(command, "--actor") == "human.product-owner-simulated"
    assert "--no-continue" in command
    # The product owner saw the criteria and only the disputed file's source.
    task, dispute = owner.seen[0]
    assert task["acceptanceCriteria"]
    assert [t["path"] for t in dispute["tests"]] == ["tests/acceptance/test_ac.py"]
    assert dispute["disputed"] == DISPUTED


def test_without_a_product_owner_the_fallback_upholds(tmp_path: Path) -> None:
    harness = FakeHarness()
    code, entry = _wait(tmp_path, harness)
    assert code == 0
    assert entry["decision"] == "UPHOLD" and entry["judge"] == "fallback"
    assert entry["reasons"]["owner"] == person.DISPUTE_FALLBACK_REASON
    [command] = harness.dispute_commands()
    assert _option(command, "--decision") == "UPHOLD"
    assert _option(command, "--actor") == person.REVIEWER_ACTOR


def test_a_digest_acceptance_show_does_not_show_is_not_answered(tmp_path: Path) -> None:
    harness = FakeHarness(shown_digest="sha256:" + "b" * 64)
    code, entry = _wait(tmp_path, harness, Owner())
    assert code is None
    assert entry["digestMatches"] is False and entry["action"] == "dispute-not-shown"
    assert harness.dispute_commands() == []


def test_a_failed_product_owner_call_is_not_acted_on(tmp_path: Path) -> None:
    harness = FakeHarness()
    code, entry = _wait(tmp_path, harness, Owner(fail=True))
    assert code is None
    assert entry["action"] == "product-owner-failed" and "unreadable reply" in entry["error"]
    assert harness.dispute_commands() == []


def test_the_phase_summary_names_the_dispute_command() -> None:
    summary = (
        "The agent disputes 2 frozen acceptance test(s) and only they fail; a person decides: "
        f"harness acceptance dispute --run run --decision <UPHOLD|REVISE> --digest {DIGEST}"
    )
    match = person._DECIDE["acceptance-dispute"].search(summary)
    assert match is not None and match.group(1) == DIGEST


def test_the_product_owner_reply_is_read_as_a_decision() -> None:
    reply = "```yaml\ndecision: revise\nreason: |\n  The test asserts a wrong value.\n```"
    assert product_owner.parse_dispute(reply) == {
        "decision": "REVISE",
        "reason": "The test asserts a wrong value.",
    }
    with pytest.raises(ValueError, match="UPHOLD or REVISE"):
        product_owner.parse_dispute("```yaml\ndecision: maybe\nreason: x\n```")
    with pytest.raises(ValueError, match="no reason"):
        product_owner.parse_dispute("```yaml\ndecision: UPHOLD\n```")


def test_a_spanish_reply_is_read_as_a_decision_and_as_answers() -> None:
    """The prompts are in Spanish since 2026-11-11; the YAML keys and decisions are not."""
    reply = "```yaml\ndecision: UPHOLD\nreason: |\n  La prueba comprueba el redondeo que pide SPEC.md.\n```"
    assert product_owner.parse_dispute(reply)["reason"].startswith("La prueba comprueba")
    answers = product_owner.parse_answers(
        "```yaml\nanswers:\n  Q-1: |\n    Un peso de 0 kg lanza ValueError; 30 kg es el máximo aceptado.\n"
        'addCriteria:\n  - |\n    shipping_cost(30, "local", 0) devuelve Decimal("48.50").\n```',
        ["Q-1"],
    )
    assert answers["answers"]["Q-1"].endswith("el máximo aceptado.")
    assert answers["addCriteria"] == ['shipping_cost(30, "local", 0) devuelve Decimal("48.50").']


def test_the_product_owner_prompts_ask_for_an_engineer_answer_in_spanish() -> None:
    prompt = product_owner.PO_PROMPT.format(task="t", questions="- Q-1: ?", knowledge="k")
    assert prompt.startswith("Eres el product owner")
    for asked in ("en español", "casos", "seguridad", "justificación", "fuera del alcance"):
        assert asked in prompt
    # The format the parser reads is kept: the keys and the question ids are written as they are.
    for key in ("answers:", "replaceCriteria:", "addCriteria:", "addRequirements:"):
        assert key in prompt
    dispute = product_owner.DISPUTE_PROMPT.format(task="t", disputed="d", tests="s", knowledge="k")
    assert "decision: UPHOLD" in dispute and "REVISE" in dispute


def test_the_product_owner_model_is_fixed_for_every_cell(tmp_path: Path) -> None:
    assert product_owner.DEFAULT_PO_MODEL == "claude-sonnet-5-5"
    owner = product_owner.ProductOwner("k", product_owner.DEFAULT_PO_MODEL, tmp_path)
    assert owner.model == "claude-sonnet-5-5"
