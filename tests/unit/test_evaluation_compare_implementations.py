"""The comparison of implementations of the evaluation (``evaluation/analysis``).

Small run directories are built in ``tmp_path`` with the layout ``run_eval.py`` leaves: a Git
workspace with a ``baseline`` commit, the junit and coverage files of ``measure/``, the agent's call
records, the harness's state database and, for a stopped run, the quarantined patch."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ANALYSIS = Path(__file__).resolve().parents[2] / "evaluation" / "analysis"
sys.path.insert(0, str(ANALYSIS))

import compare_implementations as cmp  # noqa: E402
import practices  # noqa: E402

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "commit.gpgsign=false"]
BASE_SOURCE = 'def decode(value: str) -> str:\n    """Decode."""\n    return value\n'
DIRECT_SOURCE = (
    "def decode(value: str) -> str:\n"
    '    """Decode."""\n'
    "    if not isinstance(value, str):\n"
    '        raise ValueError("bad")\n'
    "    return value\n"
)
HARNESS_SOURCE = (
    "import os\n\n\n"
    "def decode(value: str) -> str:\n"
    '    """Decode."""\n'
    "    try:\n"
    "        return value.strip()\n"
    "    except Exception:\n"
    "        pass\n"
    "    return value\n"
)
TEST_FILE = (
    "import pytest\n\n"
    "from toy import decode\n\n\n"
    '@pytest.mark.parametrize("value", ["", "a"])\n'
    "def test_decode(value):\n"
    "    assert decode(value) == value\n\n\n"
    "def test_rejects():\n"
    "    with pytest.raises(ValueError):\n"
    "        decode(None)\n"
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *IDENTITY, *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    ).stdout


def workspace(root: Path, source: str | None, tests: dict[str, str] | None = None) -> Path:
    ws = root / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "toy.py").write_text(BASE_SOURCE, encoding="utf-8")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_base.py").write_text(
        "def test_base():\n    assert True\n", encoding="utf-8"
    )
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "baseline")
    if source is not None:
        (ws / "src" / "toy.py").write_text(source, encoding="utf-8")
    for name, text in (tests or {}).items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(text, encoding="utf-8")
    return ws


def junit(path: Path, cases: list[tuple[str, str, str]]) -> None:
    body = []
    for classname, name, outcome in cases:
        inner = "" if outcome == "passed" else f"<{outcome} message='x'/>"
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<testsuites><testsuite tests="{len(cases)}">{"".join(body)}</testsuite></testsuites>',
        encoding="utf-8",
    )


def call(run_dir: Path, text: str | None, cost: float = 0.1) -> None:
    calls = run_dir / "agent-calls"
    calls.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"wallSeconds": 1.0, "costUsd": cost, "numTurns": 3}
    if text is not None:
        record["resultText"] = text
    (calls / "call-1.json").write_text(json.dumps(record), encoding="utf-8")


def state(
    run_dir: Path,
    records: dict[str, list[dict[str, Any]]],
    events: list[tuple[str, dict[str, Any]]],
) -> None:
    folder = run_dir / "state" / "project"
    folder.mkdir(parents=True)
    db = sqlite3.connect(folder / "state.db")
    db.execute("create table records (record_type text, record_id text, payload_json text)")
    db.execute(
        "create table events (global_sequence integer primary key, event_type text, payload_json text)"
    )
    for kind, payloads in records.items():
        for index, payload in enumerate(payloads):
            db.execute(
                "insert into records values (?, ?, ?)",
                (kind, f"{kind}-{index}", json.dumps(payload)),
            )
    for event_type, payload in events:
        db.execute(
            "insert into events (event_type, payload_json) values (?, ?)",
            (event_type, json.dumps(payload)),
        )
    db.commit()
    db.close()


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    direct = root / "toy-direct-model-x-r1-20261111T000000Z"
    workspace(direct, DIRECT_SOURCE, {"tests/test_toy.py": TEST_FILE})
    junit(
        direct / "measure" / "visible.xml",
        [
            ("tests.test_toy", "test_decode[]", "passed"),
            ("tests.test_toy", "test_rejects", "passed"),
        ],
    )
    call(direct, "Done. I assumed the input is always ASCII.\nShould empty input be valid?")

    governed = root / "toy-harness-model-x-r1-20261111T000100Z"
    workspace(
        governed,
        HARNESS_SOURCE,
        {
            "tests/acceptance/test_ac_one.py": "def test_ac_one():\n    pass\n",
            "scratch.py": "x = 1\n",
        },
    )
    junit(
        governed / "measure" / "visible.xml",
        [("tests.acceptance.test_ac_one", "test_ac_one", "failure")],
    )
    call(governed, None, cost=0.5)
    state(
        governed,
        {
            "task": [
                {
                    "acceptanceCriteria": [{"criterionId": "ac_one"}, {"criterionId": "ac_two"}],
                    "requirements": [{}],
                }
            ],
            "execution": [
                {
                    "currentPhase": "VERIFICATION",
                    "status": "FAILED",
                    "terminalReason": "still fails",
                }
            ],
            "clarification_request": [{"questions": [{"ruleId": "C0"}, {"ruleId": "C1"}]}],
            "clarification": [
                {"answers": [{"question": "Which?", "answer": "This"}], "addedCriteria": ["ac_two"]}
            ],
            "agent_self_report": [
                {"assumptions": ["ASCII only"], "disputedAcceptanceTests": [{"test": "t"}]}
            ],
            "finding": [{"ruleId": "agent.unsupported-claim", "severity": "HIGH"}],
            "validation": [{"validatorId": "python.ruff", "mandatory": True, "status": "FAILED"}],
        },
        [
            (
                "acceptance.tests.decided",
                {"frozen": {"tests/acceptance/test_ac_one.py": "sha256:x"}},
            ),
            ("plan.approval.decided", {}),
            ("agent.routing.decided", {}),
            ("correction.authorized", {"failedValidators": ["harness.ratchet"]}),
            ("correction.exhausted", {"failedValidators": ["harness.ratchet"]}),
        ],
    )
    (root / "toy-harness-model-x-20261111T000100Z").mkdir()  # a stray directory, not a run
    return root


def test_run_names_are_parsed_with_the_prompt_and_condition() -> None:
    assert cmp.parse_run_name("brownfield-direct-m-1-r1-20261111T152622Z") == {
        "scenario": "brownfield",
        "prompt": "full",
        "condition": "direct",
        "model": "m-1",
        "rep": 1,
        "stamp": "20261111T152622Z",
    }
    casual = cmp.parse_run_name("brownfield-casual-harness-m-r2-20261111T160625Z")
    assert casual is not None
    assert (casual["prompt"], casual["condition"], casual["rep"]) == ("casual", "harness", 2)
    anchored = cmp.parse_run_name("brownfield-harness-anchored-m-r1-20261111T154615Z")
    core = cmp.parse_run_name("brownfield-harness-core-m-r1-20261111T154615Z")
    assert anchored is not None
    assert anchored["condition"] == "harness-anchored"
    assert core is not None
    assert core["condition"] == "harness-core"
    assert cmp.parse_run_name("brownfield-casual-harness-claude-haiku-4-5-20261111T160625Z") is None


def test_source_practices_count_good_and_bad_constructs() -> None:
    source = (
        "def f(a, b: int) -> int:\n"
        "    try:\n"
        "        return a / b\n"
        "    except:\n"
        "        pass\n"
        "    try:\n"
        "        return 42\n"
        "    except Exception as error:\n"
        "        raise ValueError('x') from error\n"
        "    return 0\n"
        "    print('dead')\n"
    )
    found = practices.source_practices({"src/m.py": source})
    assert found["bareExcept"] == 1
    assert found["broadExcept"] == 1
    assert found["swallowedErrors"] == 1
    assert found["raiseInExceptWithFrom"] == 1
    assert found["raiseInExceptWithoutFrom"] == 0
    assert found["magicNumbers"] == 1
    assert found["unreachableStatements"] == 1
    assert found["typeHintCoverage"] == pytest.approx(2 / 3, abs=1e-4)
    assert found["docstringCoverage"] == 0
    assert found["maxComplexity"] >= 3


def test_test_practices_find_empty_negative_and_parametrized_tests() -> None:
    text = TEST_FILE + "\n\ndef test_nothing():\n    global STATE\n    decode('a')\n"
    found = practices.test_practices({"tests/test_x.py": text})
    assert found["tests"] == 3
    assert found["testsWithoutAssertion"] == 1
    assert found["negativeTests"] == 1
    assert found["parametrizedCases"] == 2
    assert found["globalStatements"] == 1
    assert found["edgeCaseLiterals"] >= 2  # "" in the parametrization and None


def test_public_surface_reports_signature_changes() -> None:
    before = cmp.public_surface("def f(a):\n    pass\n\ndef _g():\n    pass\n")
    after = cmp.public_surface(
        'def f(a, b=1):\n    """Doc."""\n\nclass K:\n    def m(self):\n        pass\n'
    )
    assert set(before) == {"f"}
    assert set(after) == {"f", "K", "K.m"}
    assert before["f"]["signature"] != after["f"]["signature"]


def test_hidden_tests_are_mapped_to_criteria(tmp_path: Path) -> None:
    path = tmp_path / "hidden.xml"
    junit(
        path,
        [
            (
                "_hidden_eval.t",
                "test_characters_outside_the_url_safe_alphabet_are_rejected[YW+j]",
                "passed",
            ),
            ("_hidden_eval.t", "test_non_ascii_and_control_bytes_are_rejected[x]", "failure"),
            ("_hidden_eval.t", "test_impossible_length_is_rejected", "passed"),
        ],
    )
    hidden = cmp.hidden_metrics("brownfield", path)
    assert hidden["passed"] == 2
    assert hidden["tests"] == 3
    assert hidden["byCriterion"]["ac_reject"] == {"passed": 1, "tests": 2}
    assert hidden["byCriterion"]["(sin criterio)"] == {"passed": 1, "tests": 1}
    assert hidden["criteriaFullyPassed"] == 0


def test_runs_are_analyzed_and_compared(runs: Path, tmp_path: Path) -> None:
    found, skipped = cmp.discover([runs])
    assert len(found) == 2
    assert skipped == ["toy-harness-model-x-20261111T000100Z"]
    direct = cmp.analyze_run(found[0], static=False)
    governed = cmp.analyze_run(found[1], static=False)

    assert direct["change"]["files"] == 2
    assert direct["tests"]["testFunctionsAdded"] == 2
    assert direct["tests"]["casesInChangedFiles"]["passed"] == 2
    assert direct["requirements"]["direct"]["assumptionLines"] == 1
    assert direct["requirements"]["direct"]["questionLines"] == 1
    assert direct["practices"]["source"]["delta"]["raiseStatements"] == 1
    assert "+    if not isinstance(value, str):" in direct["code"]["text"]

    assert governed["change"]["byKind"]["other"]["files"] == 1
    assert governed["practices"]["source"]["delta"]["broadExcept"] == 1
    assert governed["practices"]["source"]["delta"]["swallowedErrors"] == 1
    assert governed["practices"]["tests"]["delta"]["testsWithoutAssertion"] == 1
    harness = governed["requirements"]["harness"]
    assert harness["clarification"] == {
        "rounds": 1,
        "questions": 2,
        "answers": 1,
        "criteriaAddedByAnswers": 1,
        "requirementsAddedByAnswers": 0,
        "questionTexts": ["Which?"],
    }
    assert harness["assumptions"]["count"] == 1
    assert harness["disputes"]["disputedTests"] == 1
    assert harness["corrections"] == {"cycles": 1, "exhausted": True}
    assert harness["blockingValidators"] == ["harness.ratchet", "python.ruff"]
    assert harness["decisions"]["events"] == {
        "acceptance.tests.decided": 1,
        "plan.approval.decided": 1,
    }
    assert harness["acceptanceTests"]["criteriaWithAcceptanceFile"] == 1
    assert harness["acceptanceTests"]["cases"]["failed"] == 1
    assert harness["findings"]["unsupportedClaims"] == 1

    markdown = cmp.comparison_markdown([direct, governed], skipped)
    assert "**`harness` frente a `direct`**" in markdown
    assert "a favor de `direct`" in markdown
    assert "```diff" in markdown


def test_a_missing_direct_run_is_said_and_poor_prompts_come_first(runs: Path) -> None:
    found, _ = cmp.discover([runs])
    governed = cmp.analyze_run(found[1], static=False)
    casual = json.loads(json.dumps(governed))
    casual["run"]["prompt"] = "casual"
    markdown = cmp.comparison_markdown([governed, casual], [])
    assert markdown.index("prompt `casual`") < markdown.index("prompt `full`")
    assert markdown.count("Falta la corrida `direct`") == 2


def test_differences_report_both_directions() -> None:
    direct = {
        "run": {"condition": "direct"},
        "static": {"ruff": {"new": 0}},
        "agent": {"costUsd": 0.2},
        "tests": {"testFunctionsAdded": 1},
    }
    other = {
        "run": {"condition": "harness"},
        "static": {"ruff": {"new": 3}},
        "agent": {"costUsd": 0.1},
        "tests": {"testFunctionsAdded": 5},
    }
    lines, tally = cmp.differences(direct, other, cmp.ROWS)
    assert tally["direct"] == 1  # Ruff
    assert tally["harness"] == 2  # cost and tests
    assert any("Ruff: hallazgos nuevos" in line and "a favor de `direct`" in line for line in lines)


def test_a_quarantined_change_is_rebuilt_from_its_patch(tmp_path: Path) -> None:
    run_dir = tmp_path / "toy-harness-model-r1-20261111T000000Z"
    ws = workspace(
        run_dir,
        DIRECT_SOURCE,
        {"tests/acceptance/test_kept.py": "def test_kept():\n    assert True\n"},
    )
    patch = subprocess.run(
        ["git", "diff", "HEAD"], cwd=ws, env=GIT_ENV, check=True, capture_output=True
    ).stdout
    git(ws, "add", "-A")
    patch_all = subprocess.run(
        ["git", "diff", "--cached", "HEAD"], cwd=ws, env=GIT_ENV, check=True, capture_output=True
    ).stdout
    assert patch  # the source change is in the patch
    (run_dir / "quarantined.patch").write_bytes(
        patch_all.replace(b"\n", b"\r\n")
    )  # saved on Windows
    git(ws, "reset", "-q")
    (ws / "src" / "toy.py").write_text(BASE_SOURCE, encoding="utf-8")  # restored, tests kept
    call(run_dir, None)
    record = cmp.analyze_run(run_dir, static=False)
    assert record["run"]["measuredOn"] == "quarantine-reconstructed"
    assert record["quarantine"]["patchApplied"] is True
    assert record["quarantine"]["patchHadCrlf"] is True
    assert "src/toy.py" in record["change"]["paths"]


def test_the_cli_writes_depersonalized_outputs(runs: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert cmp.main(["--runs", str(runs), "--out", str(out), "--no-static"]) == 0
    files = sorted(p.name for p in (out / "per-run").iterdir())
    assert files == ["toy-full-direct-model-x-r1.json", "toy-full-harness-model-x-r1.json"]
    text = (out / "per-run" / files[0]).read_text(encoding="utf-8")
    assert str(runs) not in text
    assert str(tmp_path) not in text
    assert "## Directorios omitidos" in (out / "comparison.md").read_text(encoding="utf-8")


def test_static_quality_counts_only_what_the_change_added(runs: Path) -> None:
    found, _ = cmp.discover([runs])
    ws = found[1] / "ws"
    baseline = cmp.baseline_commit(ws)
    assert baseline is not None
    files = cmp.measure._changed_files(ws, baseline)
    static = cmp.static_metrics(ws, baseline, files)
    if not static["ruff"]["available"]:
        pytest.skip("ruff is not installed")
    assert static["ruff"]["newByRule"].get("F401") == 1  # the unused ``import os``
    assert static["ruff"]["newByKind"].get("source", 0) >= 1
    assert static["ruff"]["new"] == sum(static["ruff"]["newByRule"].values())


def test_a_refused_run_is_listed_as_refused_and_not_measured(runs: Path) -> None:
    refused = runs / "toy-harness-core-model-x-r1-20261111T000200Z"
    workspace(refused, None)
    (refused / "run-outcome.json").write_text(
        json.dumps({"outcome": "task-refused-exit-2", "refused": True, "invalid": None}),
        encoding="utf-8",
    )
    found, _ = cmp.discover([runs])
    records = [cmp.analyze_run(path, static=False) for path in found]
    record = next(r for r in records if r["run"]["condition"] == "harness-core")
    assert record["notMeasured"] is True
    assert record["run"]["status"] == "rechazada"
    assert "requirements" not in record
    markdown = cmp.comparison_markdown(records, [])
    assert "**`harness-core` frente a `direct`**: rechazada (`task-refused-exit-2`)" in markdown
    assert "| Estado de la corrida | medida | rechazada | medida |" in markdown


# ----- A9: the five primary metrics --------------------------------------------------------
def _pair() -> tuple[dict[str, Any], dict[str, Any]]:
    """The harness is better on one primary metric (scope), worse on another (Ruff), and better on
    two exploratory ones (cost, test functions)."""
    direct = {
        "run": {"condition": "direct"},
        "change": {"outOfScopeFiles": 2},
        "static": {"ruff": {"new": 0, "newByKind": {"source": 0}}},
        "agent": {"costUsd": 0.2},
        "tests": {"testFunctionsAdded": 1},
    }
    other = {
        "run": {"condition": "harness"},
        "change": {"outOfScopeFiles": 0},
        "static": {"ruff": {"new": 3, "newByKind": {"source": 3}}},
        "agent": {"costUsd": 0.1},
        "tests": {"testFunctionsAdded": 5},
    }
    return direct, other


def test_primary_is_the_five_declared_metrics_with_unchanged_directions() -> None:
    assert [row[0] for row in cmp.PRIMARY] == list(cmp.PRIMARY_LABELS)
    assert len(cmp.PRIMARY) == 5
    polarity = {row[0]: row[2] for row in cmp.PRIMARY}
    assert polarity["Archivos fuera del alcance"] == -1
    assert polarity["Ruff: hallazgos nuevos en fuente"] == -1
    assert polarity["Pruebas: pruebas negativas con `raises` (nuevas)"] == 1
    assert polarity["Cobertura de líneas fuente cambiadas"] == 1
    assert polarity["Fuente: complejidad máxima (final)"] == -1
    exploratory = {r[0] for r in cmp.EXPLORATORY_ROWS + cmp.EXPLORATORY_PRACTICE_ROWS}
    assert not exploratory & set(cmp.PRIMARY_LABELS)


def test_the_tally_counts_only_primary_metrics() -> None:
    direct, other = _pair()
    _, tally = cmp.differences(direct, other, cmp.PRIMARY)
    assert (tally["harness"], tally["direct"]) == (1, 1)
    # the exploratory rows differ too (cost, test functions) but are not in the tally
    _, extra = cmp.differences(direct, other, cmp.EXPLORATORY_ROWS)
    assert extra["harness"] >= 2


def test_markdown_lists_primary_first_and_counts_only_them(runs: Path) -> None:
    found, _ = cmp.discover([runs])
    governed = cmp.analyze_run(found[1], static=False)
    direct = json.loads(json.dumps(governed))
    direct["run"]["condition"] = "direct"
    direct["agent"] = {**governed.get("agent", {}), "costUsd": 99.0}
    markdown = cmp.comparison_markdown([direct, governed], [])
    assert markdown.index("#### Métricas primarias") < markdown.index("(exploratorias)")
    assert "de las 5 métricas primarias" in markdown
    assert "no entran en el recuento" in markdown


# ----- D8: hidden tests deducible without the specification ----------------------------------
def _function_names(path: Path) -> set[str]:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    }


def test_every_greenfield_hidden_test_is_classified() -> None:
    hidden = Path(cmp.__file__).resolve().parents[1] / "hidden"
    for scenario, folder, module in (
        ("greenfield", "greenfield-shipping", "test_hidden_shipping.py"),
        ("security", "greenfield-alerts", "test_hidden_alerts.py"),
    ):
        names = _function_names(hidden / folder / module)
        assert names == set(cmp.HIDDEN_DEDUCIBILITY[scenario]), scenario
        assert set(cmp.HIDDEN_DEDUCIBILITY[scenario].values()) <= {cmp.DEDUCIBLE, cmp.SPEC_ONLY}


def test_hidden_metrics_give_the_two_counts(tmp_path: Path) -> None:
    path = tmp_path / "hidden.xml"
    junit(
        path,
        [
            ("t", "test_negative_subtotal_is_rejected", "passed"),
            ("t", "test_result_is_decimal_with_two_places[a0-kwargs0]", "failure"),
            ("t", "test_base_cost_by_weight[1-local-5.00]", "passed"),
            ("t", "test_base_cost_by_weight[0.5-local-5.00]", "passed"),
            ("t", "test_invented", "passed"),
        ],
    )
    result = cmp.hidden_metrics("greenfield", path)["deducibility"]
    assert result["deducible"] == {"passed": 1, "total": 2}
    assert result["especificacion"] == {"passed": 2, "total": 2}
    assert result["unclassified"] == 1
    assert cmp.hidden_metrics("brownfield", path)["deducibility"] is None
    row = dict((r[0], r[1]) for r in cmp.ROWS)
    record = {"requirements": {"hidden": {"deducibility": result}}}
    assert row["Pruebas ocultas deducibles sin especificación pasadas"](record) == 1
    assert row["Pruebas ocultas solo con especificación (total)"](record) == 2


# ----- D8: what the run did before what the prompt left unsaid ------------------------------
INVENTING = (
    '"""Costs."""\n'
    "from decimal import Decimal\n\n"
    'RATES = {"local": Decimal("5.00"), "zonex": Decimal("7.35")}\n\n\n'
    "def cost(zone):\n"
    '    if zone not in RATES:\n        raise ValueError(f"unknown zone {zone} 42")\n'
    "    return RATES[zone] + 1 + 0\n"
)
KNOWN = (
    "from decimal import Decimal\n\n"
    'RATES = {"local": Decimal("5.00"), "national": Decimal("9.00")}\n'
    'FREE_FROM = Decimal("100")\nHALF = 0.5\n'
)


def _unknowns(tmp_path: Path, source: str, files=None, **kwargs: Any) -> dict[str, Any]:
    ws = workspace(tmp_path, source)
    baseline = git(ws, "rev-parse", "HEAD").strip()
    changed = files or [{"path": "src/toy.py"}]
    return cmp.unknown_behavior("greenfield", ws, baseline, changed, **kwargs)


def test_literals_skip_docstrings_trivial_numbers_messages_and_fstrings() -> None:
    found = cmp.code_literals(INVENTING)
    assert dict(found) == ({("s", "local"): 1, ("n", "5"): 1, ("s", "zonex"): 1, ("n", "7.35"): 1})


def test_invented_values_are_those_neither_in_the_spec_nor_declared(tmp_path: Path) -> None:
    result = _unknowns(tmp_path, INVENTING, asked=0, declared=0)
    assert result["behavior"] == cmp.INVENTED
    assert result["inventedTexts"] == ["7.35", "zonex"]
    assert result["specAvailable"] is True


def test_values_of_the_spec_are_not_invented(tmp_path: Path) -> None:
    result = _unknowns(tmp_path, KNOWN, asked=0, declared=0)
    assert result["behavior"] == cmp.NOTHING_NEW and result["inventedValues"] == 0


def test_priority_is_asked_then_declared_then_invented(tmp_path: Path) -> None:
    asked = _unknowns(tmp_path / "a", INVENTING, asked=1, declared=1)
    assert asked["behavior"] == cmp.ASKED
    recorded = _unknowns(tmp_path / "b", INVENTING, asked=0, declared=1)
    assert recorded["behavior"] == cmp.ASSUMED
    said = _unknowns(
        tmp_path / "c", INVENTING, asked=0, declared=0, final_text="Asumo 7.35 y zonex."
    )
    assert said["behavior"] == cmp.ASSUMED and said["inventedValues"] == 0


def test_a_comment_or_a_todo_in_the_added_code_declares_the_assumption(tmp_path: Path) -> None:
    commented = INVENTING + "# TODO confirmar la tarifa zonex 7.35\n"
    result = _unknowns(tmp_path, commented, asked=0, declared=0)
    assert result["behavior"] == cmp.ASSUMED
    assert result["inventedValues"] == 0


def test_the_row_is_in_the_primary_table_only_when_the_run_has_the_metric(runs: Path) -> None:
    found, _ = cmp.discover([runs])
    record = cmp.analyze_run(found[1], static=False)
    assert "unknowns" not in record["requirements"]  # scenario ``toy`` is not greenfield
    record["requirements"]["unknowns"] = {"behavior": cmp.INVENTED, "inventedValues": 3}
    other = json.loads(json.dumps(record))
    other["run"]["condition"] = "direct"
    other["requirements"]["unknowns"] = {"behavior": cmp.ASKED}
    markdown = cmp.comparison_markdown([other, record], [])
    primary = markdown.split("#### Métricas primarias")[1].split("(exploratorias)")[0]
    assert "inventó (3 valores inventados)" in primary and "preguntó" in primary
    # the row has no direction: it never enters the tally
    assert cmp.UNKNOWNS_ROW[2] == 0 and cmp.UNKNOWNS_ROW not in cmp.PRIMARY


def test_analyze_run_reports_the_behavior_of_a_poor_greenfield_run(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    direct = root / "greenfield-casual-direct-model-x-r1-20261111T000000Z"
    workspace(direct, INVENTING, {})
    call(direct, "Hecho. ¿Qué zonas necesitas?")
    harness = root / "greenfield-casual-harness-model-x-r1-20261111T000100Z"
    workspace(harness, KNOWN, {})
    call(harness, None, cost=0.5)
    state(
        harness,
        {"task": [{"metadata": {"assumptions": ["zones are local and national"]}}]},
        [],
    )
    found, _ = cmp.discover([root])
    by_condition = {
        r["run"]["condition"]: r for r in (cmp.analyze_run(f, static=False) for f in found)
    }
    assert by_condition["direct"]["requirements"]["unknowns"]["behavior"] == cmp.ASKED
    unknowns = by_condition["harness"]["requirements"]["unknowns"]
    assert unknowns["behavior"] == cmp.ASSUMED and unknowns["assumptionsDeclared"] == 1
