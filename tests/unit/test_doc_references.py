"""The document references the intent review looks for in an answer (#114: Sonar python:S8786).

The expected lists were produced by the earlier single regular expression,
``(?<![\\w/.-])([\\w-]++(?:/[\\w-]++)*+\\.(?:md|txt|rst|pdf|docx?|ya?ml|json))(?![\\w])`` with
``re.I`` and ``findall``: the scan that replaced it must find exactly the same strings.
"""

from __future__ import annotations

import time

import pytest

from governed_harness.orchestration.intent_review import _doc_references

CASES = [
    ("See docs/spec.md and README.MD.", ["docs/spec.md", "README.MD"]),
    ("per ops/run-book.yaml, then notes.txt", ["ops/run-book.yaml", "notes.txt"]),
    ("a.docx a.doc a.docxy a.yml a.yaml a.yamlx", ["a.docx", "a.doc", "a.yml", "a.yaml"]),
    ("dir-1/sub_2/file-3.PDF,", ["dir-1/sub_2/file-3.PDF"]),
    ("(see `api.json`)", ["api.json"]),
    ("über/straße.rst", ["über/straße.rst"]),
    ("a.rſt", ["a.rſt"]),
    ("x/a.md", ["x/a.md"]),
    ("a.md.bak", ["a.md"]),
    ("a.md-old", ["a.md"]),
    ("a.md/b.txt", ["a.md"]),
    ("-a.md a-.md", ["-a.md", "a-.md"]),
    ("/abs/path.md", []),
    ("trail/.md", []),
    ("a//b.md", []),
    ("https://host/a.md", []),
    ("a.b.md", []),
    ("v1.2.md", []),
    ("e.g.md", []),
    ("no reference here", []),
    ("", []),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_the_references_match_the_earlier_expression(text: str, expected: list[str]) -> None:
    assert _doc_references(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "a/" * 100_000 + "x",
        "a/" * 100_000 + ".md",
        "a" * 200_000,
        "a-" * 100_000,
        "a." * 100_000,
        ("x/" * 20 + " ") * 10_000,
    ],
)
def test_a_long_pathological_answer_is_read_in_linear_time(text: str) -> None:
    start = time.perf_counter()
    found = _doc_references(text)
    assert time.perf_counter() - start < 1.0
    assert found == []
