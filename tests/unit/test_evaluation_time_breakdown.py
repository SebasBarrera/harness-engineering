"""The own-time breakdown of the evaluation pilot (``evaluation/analysis/time_breakdown.py``), on
synthetic records: agent time is subtracted per leg and validators are timed from the event gaps."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ANALYSIS = Path(__file__).resolve().parents[2] / "evaluation" / "analysis"
sys.path.insert(0, str(ANALYSIS))

import time_breakdown as tb  # noqa: E402


def _cmd(*words: str, seconds: float) -> dict[str, object]:
    return {"command": list(words), "exitCode": 0, "seconds": seconds}


def _record() -> dict[str, object]:
    commands = [
        _cmd("init", "--path", seconds=2),
        _cmd("run", "start", seconds=30),
        _cmd("status", "--run", seconds=2),
        _cmd("plan", "decide", seconds=3),
        _cmd("run", "continue", seconds=100),
        _cmd("gate", "decide", seconds=3),
        _cmd("run", "continue", seconds=10),
    ]
    calls = [
        {"kind": "clarify", "wallSeconds": 10},
        {"kind": "locate", "wallSeconds": 10},
        {"kind": "implement", "wallSeconds": 50},
        {"kind": "review", "wallSeconds": 20},
    ]
    return {
        "condition": "harness",
        "scenario": "s",
        "rep": 1,
        "startedAt": "2026-01-01T00:00:00+00:00",
        "agentCalls": calls,
        "harness": {"commands": commands},
    }


def test_agent_time_is_subtracted_per_leg() -> None:
    result = tb.breakdown(_record())
    assert result is not None
    assert result["ownSeconds"] == {
        "short": 10.0,
        "intent_planning": 10.0,
        "implementation": 30.0,
        "closure": 10.0,
    }
    assert result["ownTotalSeconds"] == 60.0
    assert result["commands"]["short"] == 4


def test_without_plan_decide_all_runs_before_the_gate_are_implementation() -> None:
    record = {
        "agentCalls": [{"kind": "implement", "wallSeconds": 35}],
        "harness": {
            "commands": [
                _cmd("init", seconds=2),
                _cmd("run", "start", seconds=60),
                _cmd("gate", "decide", seconds=2),
                _cmd("run", "continue", seconds=3),
            ]
        },
    }
    result = tb.breakdown(record)
    assert result is not None
    assert result["ownSeconds"]["implementation"] == 25.0
    assert result["ownSeconds"]["intent_planning"] == 0.0
    assert tb.breakdown({"harness": {}}) is None


def _state_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "create table events (global_sequence integer primary key, event_type text, occurred_at text, payload_json text)"
    )
    rows = [
        ("phase.started", "2026-01-01T00:00:00+00:00", {}),
        ("capabilities.resolved", "2026-01-01T00:00:01+00:00", {"phase": "VERIFICATION"}),
        (
            "validation.completed",
            "2026-01-01T00:00:04+00:00",
            {"validator_id": "python.pytest", "status": "PASSED"},
        ),
        (
            "validation.completed",
            "2026-01-01T00:00:30+00:00",
            {"validator_id": "harness.mutation", "status": "PASSED"},
        ),
        ("phase.completed", "2026-01-01T00:00:31+00:00", {}),
        ("phase.started", "2026-01-01T00:01:00+00:00", {}),
        ("capabilities.resolved", "2026-01-01T00:01:00+00:00", {"phase": "IMPLEMENTATION"}),
        (
            "validation.completed",
            "2026-01-01T00:01:09+00:00",
            {"validator_id": "ignored", "status": "PASSED"},
        ),
    ]
    con.executemany(
        "insert into events (event_type, occurred_at, payload_json) values (?, ?, ?)",
        [(a, b, json.dumps(c)) for a, b, c in rows],
    )
    con.commit()
    con.close()


def test_validators_are_timed_from_event_gaps(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    _state_db(db)
    passes = tb.validator_passes(db)
    assert len(passes) == 1
    assert [(v["validatorId"], v["seconds"]) for v in passes[0]["validators"]] == [
        ("python.pytest", 4.0),
        ("harness.mutation", 26.0),
    ]
    assert passes[0]["seconds"] == 30.0


def test_markdown_and_json_are_rendered(tmp_path: Path) -> None:
    pilot = tmp_path / "pilot.jsonl"
    pilot.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    out_json, out_md = tmp_path / "t.json", tmp_path / "t.md"
    assert tb.main([str(pilot), "--json", str(out_json), "--md", str(out_md)]) == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))[0]["ownTotalSeconds"] == 60.0
    assert "Desglose del tiempo propio" in out_md.read_text(encoding="utf-8")
