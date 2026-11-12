"""Cuestionario de auditoría (``evaluation/deterministic/audit_questionnaire.py``).

El bundle es sintético (mismo formato que ``harness export``: ``events.jsonl``, ``records.json``
y ``artifacts/``); el repositorio es un Git real en ``tmp_path`` sin harness."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

DETERMINISTIC = Path(__file__).resolve().parents[2] / "evaluation" / "deterministic"
sys.path.insert(0, str(DETERMINISTIC))

import audit_questionnaire as aq  # noqa: E402

DIGEST = "sha256:" + "a" * 64
OUT_HASH = "b" * 64
META_HASH = "c" * 64


def make_bundle(path: Path, commit: dict[str, str] | None = None) -> None:
    events = [
        {"eventType": "phase.completed", "payload": {"phaseId": "INTENT"}},
        {
            "eventType": "human.decision.recorded",
            "payload": {
                "actor": {"actor_id": "human.ana"},
                "decision": "APPROVE",
                "change_set_digest": DIGEST,
                "decided_at": "2026-01-02T03:04:05Z",
                "rationale": "looks right",
                "acknowledged_risks": [],
            },
        },
        {
            "eventType": "delivery.commit.created",
            "occurredAt": "2026-01-02T03:05:00+00:00",
            "payload": {"commit": "d" * 40, **(commit or {})},
        },
    ]
    records = {
        "change_set": [{"digest": DIGEST, "files": [{"path": "src/a.py"}]}],
        "agent_invocation": [{"provider": "fixture", "callKind": "implement"}],
        "configuration_snapshot": [{"contentDigest": "sha256:" + "e" * 64}],
        "validation": [
            {
                "changeSetDigest": DIGEST,
                "validatorId": "python.pytest",
                "status": "PASSED",
                "evidenceRefs": [f"artifact://sha256/{META_HASH}"],
            }
        ],
        "certification": [
            {
                "criteria": [
                    {
                        "criterionId": "ac_1",
                        "evidence": [{"detail": "passing test(s): tests/test_a.py::test_ac_1"}],
                    }
                ]
            }
        ],
    }
    meta = {"stdoutRef": f"artifact://sha256/{OUT_HASH}", "status": "PASSED"}
    files = {
        "events.jsonl": "\n".join(json.dumps(e) for e in events).encode(),
        "records.json": json.dumps(records).encode(),
        f"artifacts/{META_HASH}": json.dumps(meta).encode(),
        f"artifacts/{OUT_HASH}": b"1 passed in 0.1s",
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def init_repo(root: Path) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "-q"],
        ["add", "a.txt"],
        ["-c", "user.name=Ana", "-c", "user.email=ana@example.invalid", "commit", "-qm", "feat: a"],
    ):
        if args[0] == "add":
            (root / "a.txt").write_text("a", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), *args], check=True, env=env)


def test_harness_answers_the_ten_questions_from_the_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar.gz"
    make_bundle(bundle)
    result = aq.build("harness", aq.from_bundle(bundle))
    assert "total" not in result and "answerable" not in result
    assert result["byGroup"]["tipo"]["answered"] == 10
    assert result["byGroup"]["commit"]["answered"] == 1
    by_id = {q["id"]: q for q in result["questions"]}
    assert by_id["Q01"]["answer"]["actor"] == "human.ana"
    assert by_id["Q08"]["answer"][0]["output"] == "1 passed in 0.1s"
    assert by_id["Q06"]["answer"]["producers"] == ["fixture"]
    # Un bundle sin author ni subject no los responde; la fecha sale de la hora del evento.
    assert not by_id["Q11"]["answerable"] and not by_id["Q12"]["answerable"]
    assert by_id["Q13"]["answerable"]


def test_a_bundle_with_author_and_subject_answers_q11_to_q13(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar.gz"
    make_bundle(bundle, {"author": "Ana <ana@example.invalid>", "subject": "feat: a"})
    result = aq.build("harness", aq.from_bundle(bundle))
    by_id = {q["id"]: q for q in result["questions"]}
    assert by_id["Q11"]["answer"] == "Ana <ana@example.invalid>"
    assert by_id["Q12"]["answer"] == "feat: a"
    assert by_id["Q13"]["answer"] == "2026-01-02T03:05:00+00:00"
    assert result["byGroup"]["commit"]["answered"] == 3
    # ``message`` wins over ``subject`` when a bundle carries both.
    make_bundle(bundle, {"author": "A <a@x.y>", "subject": "s", "message": "full message"})
    again = aq.from_bundle(bundle)
    assert again["Q12"]["answer"] == "full message"


def test_an_old_bundle_without_those_fields_leaves_q11_and_q12_unanswered(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar.gz"
    make_bundle(bundle)
    by_id = aq.from_bundle(bundle)
    assert not by_id["Q11"]["answerable"] and not by_id["Q12"]["answerable"]
    assert by_id["Q13"]["answerable"]


def test_empty_bundle_answers_nothing(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar.gz"
    with tarfile.open(bundle, "w:gz"):
        pass
    groups = aq.build("harness", aq.from_bundle(bundle))["byGroup"]
    assert groups["tipo"]["answered"] == 0 and groups["commit"]["answered"] == 0


def test_direct_without_harness_answers_the_commit_questions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    result = aq.build("direct", aq.from_git(repo, None, can_commit=True))
    assert result["byGroup"] == {
        "tipo": {"answered": 0, "notApplicable": 5, "notMeasurable": 0, "total": 10},
        "commit": {"answered": 3, "notApplicable": 0, "notMeasurable": 0, "total": 3},
    }
    assert {q["status"] for q in result["questions"] if q["id"] <= "Q05"} == {aq.NOT_APPLICABLE}
    by_id = {q["id"]: q for q in result["questions"]}
    assert by_id["Q11"]["answer"].startswith("Ana <ana@example.invalid>")
    assert by_id["Q12"]["answer"] == "feat: a"


def test_direct_uses_the_final_text_and_a_non_repository_answers_nothing(tmp_path: Path) -> None:
    text = "Should I keep the API?\n3 passed in 1s\n"
    result = aq.build("direct", aq.from_git(tmp_path, text))
    by_id = {q["id"]: q for q in result["questions"]}
    assert by_id["Q10"]["answerable"] and by_id["Q08"]["answerable"]
    assert not by_id["Q11"]["answerable"]
    assert result["byGroup"]["tipo"]["answered"] == 2


def test_direct_without_git_tool_marks_the_commit_questions_not_measurable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    result = aq.build("direct", aq.from_git(repo, None, can_commit=False))
    assert result["byGroup"]["commit"] == {
        "answered": 0,
        "notApplicable": 0,
        "notMeasurable": 3,
        "total": 3,
    }
    assert "no medible con este aparato" in aq.render_markdown(result)


def test_direct_can_commit_reads_allowed_tools(tmp_path: Path) -> None:
    lib = tmp_path / "agentlib.py"
    nl = chr(10)
    lib.write_text(
        nl.join(["ALLOWED_TOOLS = [", '"Read",', '"Bash(ls:*)",', "]", ""]), encoding="utf-8"
    )
    assert aq.direct_can_commit(lib) is False
    lib.write_text(
        nl.join(["ALLOWED_TOOLS = [", '"Bash(git commit:*)",', "]", ""]), encoding="utf-8"
    )
    assert aq.direct_can_commit(lib) is True
    assert aq.direct_can_commit(tmp_path / "absent.py") is None
    assert aq.direct_can_commit() is False  # the real agentlib: no git in the allowed tools


def test_markdown_has_no_global_quotient(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar.gz"
    make_bundle(bundle)
    text = aq.render_markdown(aq.build("harness", aq.from_bundle(bundle)))
    assert "10 de 10" in text and "1 de 3" in text
    assert "/ 13" not in text and "de 13" not in text


def test_final_text_of_agent_calls_and_cli_outputs(tmp_path: Path) -> None:
    calls = tmp_path / "agent-calls"
    calls.mkdir()
    (calls / "1.json").write_text(json.dumps({"resultText": "first"}), encoding="utf-8")
    (calls / "2.json").write_text(json.dumps({"resultText": "last?"}), encoding="utf-8")
    assert aq.final_text_of(calls) == "last?"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    out = tmp_path / "out"
    assert aq.main(["--out", str(out), "--workspace", str(repo), "--agent-calls", str(calls)]) == 0
    data = json.loads((out / "auditoria.json").read_text(encoding="utf-8"))
    assert "answerable" not in data and "total" not in data
    assert data["byGroup"]["tipo"]["answered"] == 1
    assert "no medible con este aparato" in (out / "auditoria.md").read_text(encoding="utf-8")
