"""The count pairs read from a pytest summary line (Sonar python:S8786).

The earlier single regular expression, kept here as the oracle, was
``(\\d+) (passed|failed|xfailed|xpassed|errors?|skipped|deselected)\\b`` with ``findall``: the
scan that replaced it must find exactly the same pairs, in linear time.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from governed_harness.orchestration.run_outcome import _summary_items

EARLIER = re.compile(r"(\d+) (passed|failed|xfailed|xpassed|errors?|skipped|deselected)\b")

CASES = [
    "===== 1 failed, 2 passed in 0.12s =====",
    "== 3 passed, 1 skipped, 2 deselected, 1 xfailed, 1 xpassed in 1.00s ==",
    "1 error in 0.01s",
    "2 errors in 0.01s",
    "1 errorsx 2 errorx 3 error_ 4 errors.",
    "abc12 passed x7 failed",
    "12passed 1  passed 1 Passed",
    "١٢ passed ٣ failed",
    "0 passed",
    "007 failed,",
    "1 passed1 passed",
    "1 passed passed 2 passed",
    " passed 1 passed",
    "passed",
    "",
    "no tests ran in 0.01s",
    "1 xfailed2 xpassed 3 deselected-",
    "9 skippedé 9 skipped",
]

WORDS = ["passed", "failed", "xfailed", "xpassed", "error", "errors", "skipped", "deselected"]
PIECES = [*WORDS, " ", "  ", "1", "23", "٣", "x", "_", ",", ".", "s", "é", "\t"]


@pytest.mark.parametrize("line", CASES)
def test_the_pairs_match_the_earlier_expression(line: str) -> None:
    assert _summary_items(line) == EARLIER.findall(line)


def test_random_lines_match_the_earlier_expression() -> None:
    generator = random.Random(20261111)
    for _ in range(5_000):
        line = "".join(generator.choice(PIECES) for _ in range(generator.randint(0, 12)))
        assert _summary_items(line) == EARLIER.findall(line), line


def test_decimal_digits_are_those_of_the_earlier_expression() -> None:
    digit = re.compile(r"\d")
    for code in range(0x110000):
        character = chr(code)
        assert character.isdecimal() == bool(digit.fullmatch(character)), hex(code)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1" * 200_000 + "x", []),
        ("1" * 200_000 + " passe", []),
        ("1 " * 100_000, []),
        ("1 passedx" * 20_000, []),
        (" passed" * 50_000, []),
        ("1" * 100_000 + " passed", [("1" * 100_000, "passed")]),
    ],
)
def test_a_long_pathological_line_is_read_in_linear_time(
    line: str, expected: list[tuple[str, str]]
) -> None:
    start = time.perf_counter()
    found = _summary_items(line)
    assert time.perf_counter() - start < 1.0
    assert found == expected
