from __future__ import annotations

from pathlib import Path

import pytest

from governed_harness.domain.ids import new_id
from governed_harness.domain.models import Requirement
from governed_harness.validators.traceability import (
    discover_test_files,
    load_test_corpus,
    name_mentions,
    requirement_identifier,
    trace_requirements,
)


def requirement(text: str, requirement_id: str | None = None) -> Requirement:
    return Requirement(requirement_id=requirement_id or new_id("req"), text=text)


@pytest.mark.parametrize(
    ("text", "identifier"),
    [
        ("A1. Apply the discount at the threshold.", "A1"),
        ("  B12: Reject an empty basket.", "B12"),
        ("X8) Round to cents.", "X8"),
        ("[B12] Reject an empty basket.", "B12"),
        ("[C3]. Keep the signature.", "C3"),
        ("REQ1: Three letters and a number.", "REQ1"),
        ("C3.1. A sub-requirement keeps its dotted number.", "C3.1"),
        ("A1 without a delimiter is not an identifier.", None),
        ("a1. Lower case is not an identifier.", None),
        ("A1.Without space after the delimiter.", None),
        ("ABCD1. Four letters is not an identifier.", None),
        ("A1234. Four digits is not an identifier.", None),
        ("The text mentions A1. later on.", None),
    ],
)
def test_identifier_is_the_leading_token_of_the_text(text: str, identifier: str | None) -> None:
    found = requirement_identifier(requirement(text))
    assert (found.identifier if found else None) == identifier
    if found:
        assert found.source == "text"


def test_explicit_requirement_id_identifies_a_requirement_without_a_token() -> None:
    found = requirement_identifier(requirement("Apply the discount.", "req_discount"))
    assert found is not None
    assert (found.identifier, found.source) == ("req_discount", "requirementId")


def test_text_token_wins_over_an_explicit_requirement_id() -> None:
    found = requirement_identifier(requirement("A1. Apply the discount.", "req_discount"))
    assert found is not None
    assert (found.identifier, found.source) == ("A1", "text")


def test_generated_requirement_id_does_not_identify_a_requirement() -> None:
    assert requirement_identifier(requirement("Apply the discount.")) is None


@pytest.mark.parametrize(
    ("name", "identifier", "expected"),
    [
        ("test_a1_at_threshold", "A1", True),
        ("test_A1", "A1", True),
        ("TestA1", "A1", True),
        ("TestA1Rounding", "A1", True),
        ("testA1", "A1", True),
        ("test_at_threshold_b12", "B12", True),
        ("test_a12", "A1", False),
        ("test_ba1", "A1", False),
        ("TestA1rounding", "A1", False),
        ("test_c3_1_sub", "C3.1", True),
        ("test_req_discount_at_threshold", "req_discount", True),
        ("test_req_discounts", "req_discount", False),
        ("test_a1", "A1", True),
    ],
)
def test_names_contain_the_identifier_as_a_token(
    name: str, identifier: str, expected: bool
) -> None:
    assert name_mentions(name, identifier) is expected


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


PYTHON_TESTS = '''\
"""Module docstring that covers D4."""
import pytest


def test_a1_at_threshold():
    assert True


def test_mentions_in_docstring():
    """Covers B2 at the threshold."""


@pytest.mark.parametrize("value", [pytest.param(1, id="C3")])
def test_parametrized(value):
    assert value


def test_comment_only():
    # E5: covered by this comment
    assert True


class TestF6:
    def test_inside(self):
        assert True


class TestGrouped:
    def test_g7_nested(self):
        assert True


def helper_h8():
    """H8 is named by a helper, not by a test."""


def test_neighbours():
    """A1.2 and A12 are other requirements."""
'''


@pytest.mark.parametrize(
    ("identifier", "node", "match"),
    [
        ("A1", "tests/test_rules.py::test_a1_at_threshold", "name"),
        ("B2", "tests/test_rules.py::test_mentions_in_docstring", "string"),
        ("C3", "tests/test_rules.py::test_parametrized", "string"),
        ("D4", "tests/test_rules.py", "string"),
        ("E5", "tests/test_rules.py::test_comment_only", "text"),
        ("F6", "tests/test_rules.py::TestF6", "name"),
        ("G7", "tests/test_rules.py::TestGrouped::test_g7_nested", "name"),
        ("A1.2", "tests/test_rules.py::test_neighbours", "string"),
    ],
)
def test_python_tests_name_a_requirement_in_every_supported_way(
    tmp_path: Path, identifier: str, node: str, match: str
) -> None:
    write(tmp_path, "tests/test_rules.py", PYTHON_TESTS)
    corpus = load_test_corpus(tmp_path, ("python",))
    (trace,), skipped = trace_requirements([requirement(f"{identifier}. Some rule.")], corpus)
    assert skipped == ()
    assert trace.traced
    assert [(item.node_id, item.match) for item in trace.tests] == [(node, match)]


@pytest.mark.parametrize("identifier", ["H8", "A1.3", "Z9"])
def test_helpers_and_neighbouring_identifiers_do_not_trace(tmp_path: Path, identifier: str) -> None:
    write(tmp_path, "tests/test_rules.py", PYTHON_TESTS)
    corpus = load_test_corpus(tmp_path, ("python",))
    (trace,), _ = trace_requirements([requirement(f"{identifier}. Some rule.")], corpus)
    assert not trace.traced
    assert trace.tests == ()


def test_file_name_traces_and_unparsable_file_is_read_as_text(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_k1_rules.py", "def test_x():\n    assert True\n")
    write(tmp_path, "tests/test_broken.py", "def test_(:\n    # covers L2\n")
    corpus = load_test_corpus(tmp_path, ("python",))
    traces, _ = trace_requirements(
        [requirement("K1. File named."), requirement("L2. Broken file.")], corpus
    )
    assert [(item.node_id, item.match) for item in traces[0].tests] == [
        ("tests/test_k1_rules.py", "name")
    ]
    assert [(item.node_id, item.match) for item in traces[1].tests] == [
        ("tests/test_broken.py", "text")
    ]


def test_requirements_without_identifier_are_skipped_and_counted(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_rules.py", PYTHON_TESTS)
    corpus = load_test_corpus(tmp_path, ("python",))
    plain = requirement("Apply the discount.")
    traces, skipped = trace_requirements([plain, requirement("A1. Rule.")], corpus)
    assert skipped == (plain.requirement_id,)
    assert [item.identifier for item in traces] == ["A1"]


def test_discovery_follows_the_profile_test_paths_and_skips_tool_directories(
    tmp_path: Path,
) -> None:
    for relative in (
        "tests/test_a.py",
        "tests/unit/b_test.py",
        "pkg/test_c.py",
        "tests/helpers.py",
        ".venv/lib/test_hidden.py",
        "node_modules/x/test_dep.py",
        "test/resolve.test.js",
        "test/helpers.js",
        "src/widget.spec.ts",
        "src/widget.ts",
    ):
        write(tmp_path, relative, "")
    assert discover_test_files(tmp_path, ("python",)) == (
        "pkg/test_c.py",
        "tests/test_a.py",
        "tests/unit/b_test.py",
    )
    assert discover_test_files(tmp_path, ("node",)) == (
        "src/widget.spec.ts",
        "test/helpers.js",
        "test/resolve.test.js",
    )


def test_node_titles_and_text_trace_a_requirement(tmp_path: Path) -> None:
    write(
        tmp_path,
        "test/rules.test.js",
        "import test from 'node:test';\n"
        "test('A1: task overrides repository', () => {});\n"
        'it("keeps defaults", () => {}); // B2\n',
    )
    write(tmp_path, "test/c3-precedence.test.js", "test('x', () => {});\n")
    corpus = load_test_corpus(tmp_path, ("node",))
    traces, _ = trace_requirements(
        [requirement("A1. Title."), requirement("B2. Comment."), requirement("C3. File.")], corpus
    )
    assert [(item.node_id, item.match) for item in traces[0].tests] == [
        ("test/rules.test.js::A1: task overrides repository", "string")
    ]
    assert [(item.node_id, item.match) for item in traces[1].tests] == [
        ("test/rules.test.js", "text")
    ]
    assert [(item.node_id, item.match) for item in traces[2].tests] == [
        ("test/c3-precedence.test.js", "name")
    ]


def test_tracing_is_deterministic(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_rules.py", PYTHON_TESTS)
    write(tmp_path, "tests/test_more.py", "def test_a1_again():\n    assert True\n")
    requirements = [requirement("A1. Rule.", "req_a"), requirement("B2. Rule.", "req_b")]
    first = trace_requirements(requirements, load_test_corpus(tmp_path, ("python",)))
    second = trace_requirements(requirements, load_test_corpus(tmp_path, ("python",)))
    assert first == second
    assert [item.node_id for item in first[0][0].tests] == [
        "tests/test_more.py::test_a1_again",
        "tests/test_rules.py::test_a1_at_threshold",
    ]
