"""Language standards packs (#56): the shipped packs, detection, card selection, repository
precedence, the tool validators and the parsers of the packs' tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_harness.domain.errors import ConfigurationError
from governed_harness.standards import (
    BUILTIN_PACKS,
    WorkspaceFacts,
    bdd_frameworks,
    builtin_pack,
    detect_packs,
    load_pack,
    project_standards,
    select_cards,
    selection_digest,
    tool_validators,
)
from governed_harness.validators.parsers import (
    parse_cargo_messages,
    parse_checkstyle,
    parse_msbuild_text,
    parse_output,
    parse_rubocop_json,
)


def test_every_pack_loads_with_unique_prefixed_cards_and_tools() -> None:
    assert len(BUILTIN_PACKS) == 15
    seen: set[str] = set()
    for pack_id in BUILTIN_PACKS:
        pack = builtin_pack(pack_id)
        assert pack.cards, pack_id
        for card in pack.cards:
            assert card.card_id.startswith(f"{pack_id}."), card.card_id
            assert card.card_id not in seen
            seen.add(card.card_id)
            assert card.rule and card.applies_to and card.verified_by
        for tool in pack.tools:
            assert tool.command, (pack_id, tool.tool_id)
        for parent in pack.extends:
            assert parent in BUILTIN_PACKS


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def test_detection_by_markers_extensions_and_dependencies(tmp_path: Path) -> None:
    web = _write(
        tmp_path / "web",
        {
            "package.json": json.dumps({"dependencies": {"react": "19.0.0"}}),
            "tsconfig.json": "{}",
            "src/App.tsx": "export function App() { return null; }\n",
        },
    )
    assert detect_packs(web) == ["typescript", "node", "react"]
    effective = project_standards(web, packs=None, overrides=None)
    # A pack brings the packs it extends: typescript and react bring javascript.
    assert [item.pack_id for item in effective.packs] == [
        "javascript",
        "typescript",
        "node",
        "react",
    ]
    service = _write(tmp_path / "go", {"go.mod": "module example.com/shop\n", "main.go": "x"})
    assert detect_packs(service) == ["go"]
    jvm = _write(tmp_path / "jvm", {"pom.xml": "<project/>", "src/main/java/A.java": "class A {}"})
    assert detect_packs(jvm) == ["java"]
    # A profile technology selects its pack even without markers.
    assert detect_packs(tmp_path / "go", ("python",))[:1] == ["python"]


def test_selection_by_file_and_review_checklist(tmp_path: Path) -> None:
    standards = project_standards(tmp_path, packs=("python", "typescript"), overrides=None)
    assert [item.pack_id for item in standards.packs] == ["python", "javascript", "typescript"]
    python_cards = select_cards(standards, ["src/shop/orders.py"], 50)
    assert python_cards and all(item.card_id.startswith("python.") for item in python_cards)
    assert "python.test-behaviour" not in {item.card_id for item in python_cards}
    review = select_cards(standards, ["src/view.tsx"], 50, review_only=True)
    assert {item.card_id for item in review} >= {"typescript.discriminated-unions"}
    assert all(item.needs_review for item in review)
    assert len(select_cards(standards, ["a.py", "b.ts"], 3)) == 3
    one = selection_digest(standards, ["b.py", "a.py"], 12)
    assert one == selection_digest(standards, ["a.py", "b.py", "a.py"], 12)
    compact = python_cards[0].compact()
    assert set(compact) <= {"id", "rule", "exceptions"}


def test_repository_standards_take_precedence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            ".harness/standards/python/cards.yaml": (
                "cards:\n"
                "  - id: python.no-print-in-library\n"
                "    title: Print allowed\n"
                "    rule: Printing is fine in this repository.\n"
                "    appliesTo: ['**/*.py']\n"
                "    verifiedBy: [review]\n"
                "  - id: python.team-rule\n"
                "    title: Team rule\n"
                "    rule: Use the shop logger.\n"
                "    appliesTo: ['src/**/*.py']\n"
                "    verifiedBy: [review]\n"
                "disabled: [python.naming]\n"
            ),
            ".harness/standards/team/cards.yaml": (
                "title: Team\n"
                "cards:\n"
                "  - id: team.commits\n"
                "    title: Small commits\n"
                "    rule: Keep each change small.\n"
                "    appliesTo: ['**/*']\n"
                "    verifiedBy: [review]\n"
            ),
        },
    )
    pack = load_pack("python", tmp_path, ".harness/standards")
    cards = {item.card_id: item for item in pack.cards}
    assert cards["python.no-print-in-library"].rule.startswith("Printing is fine")
    assert "python.team-rule" in cards and "python.naming" not in cards
    assert pack.source == "builtin+repository"
    standards = project_standards(tmp_path, packs=("auto",), overrides=".harness/standards")
    assert "team" in [item.pack_id for item in standards.packs]
    with pytest.raises(ConfigurationError, match="unknown standards pack"):
        load_pack("cobol", tmp_path, ".harness/standards")


def test_tool_validators_need_the_repository_configuration(tmp_path: Path) -> None:
    _write(tmp_path, {"Gemfile": "gem 'rails'\n", "app/a.rb": "x = 1\n"})
    standards = project_standards(tmp_path, packs=("ruby",), overrides=None)
    assert tool_validators(standards, tmp_path, []) == []
    (tmp_path / ".rubocop.yml").write_text("AllCops: {}\n")
    added = tool_validators(standards, tmp_path, [])
    assert [definition.validator_id for _, _, definition in added] == ["standards.ruby.rubocop"]
    definition = added[0][2]
    assert definition.mandatory is False and definition.when_available
    assert definition.parser == "rubocop"
    python = project_standards(tmp_path, packs=("python",), overrides=None)
    (tmp_path / "ruff.toml").write_text("line-length = 100\n")
    covered = tool_validators(python, tmp_path, ["python.ruff"])
    assert all(item[1].tool_id != "ruff" for item in covered)


def test_bdd_framework_detection(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": "[project]\ndependencies = ['pytest-bdd>=7']\n",
            "features/cart.feature": "Feature: Cart\n",
        },
    )
    facts = WorkspaceFacts.read(tmp_path)
    assert bdd_frameworks([builtin_pack("python")], facts) == ["pytest-bdd"]


def test_pack_tool_output_parsers(tmp_path: Path) -> None:
    checkstyle = (
        '<?xml version="1.0"?><checkstyle><file name="src/A.java">'
        '<error line="3" severity="error" message="Method too long" '
        'source="com.puppycrawl.tools.checkstyle.checks.sizes.MethodLengthCheck"/>'
        "</file></checkstyle>"
    )
    issues = parse_checkstyle(checkstyle, tmp_path)
    assert (issues[0].rule, issues[0].path, issues[0].line) == ("MethodLength", "src/A.java", 3)
    assert parse_checkstyle("<!DOCTYPE x><checkstyle/>", tmp_path) == []
    rubocop = {
        "files": [
            {
                "path": "app/a.rb",
                "offenses": [
                    {
                        "severity": "convention",
                        "message": "Missing magic comment",
                        "cop_name": "Style/FrozenStringLiteralComment",
                        "location": {"start_line": 1, "last_line": 1},
                    }
                ],
            }
        ],
        "summary": {"offense_count": 1},
    }
    found = parse_rubocop_json(rubocop, tmp_path)
    assert found[0].rule == "Style/FrozenStringLiteralComment" and found[0].level == "warning"
    assert parse_output(json.dumps(rubocop), "", tmp_path)[0].tool == "rubocop"
    cargo = json.dumps(
        {
            "reason": "compiler-message",
            "message": {
                "level": "warning",
                "message": "used unwrap",
                "code": {"code": "clippy::unwrap_used"},
                "spans": [{"file_name": "src/lib.rs", "line_start": 7, "is_primary": True}],
            },
        }
    )
    lint = parse_cargo_messages('{"reason":"build-finished"}\n' + cargo, tmp_path)
    assert (lint[0].rule, lint[0].path, lint[0].line) == ("clippy::unwrap_used", "src/lib.rs", 7)
    msbuild = (
        "src/Shop/Cart.cs(12,5): warning CA1062: Validate parameter 'item' [src/Shop.csproj]\n"
        "src/Shop/Cart.cs(12,5): warning CA1062: Validate parameter 'item' [src/Shop.csproj]\n"
    )
    built = parse_msbuild_text(msbuild.splitlines())
    assert len(built) == 1 and built[0].rule == "CA1062" and built[0].line == 12
    assert parse_output(msbuild, "", tmp_path, parser="msbuild")[0].path == "src/Shop/Cart.cs"
