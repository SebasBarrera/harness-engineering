"""Output parsers of validators (#53). The text samples are the real output of ruff 0.16.7,
mypy and pytest 9 on a two-file fixture; the JSON and XML samples follow each tool's format."""

from __future__ import annotations

import json
from pathlib import Path

from governed_harness.validators.parsers import parse_output, report_files

RUFF_FULL = """\
F401 `os` imported but unused
 --> src/pkg/__init__.py:1:8
  |
1 | import os
  |        ^^
help: Remove unused import: `os`

Found 1 error.
"""
RUFF_CONCISE = "src/pkg/__init__.py:1:8: F401 [*] `os` imported but unused\nFound 1 error.\n"
MYPY = """\
src/pkg/__init__.py:5: error: Incompatible return value type (got "int", expected "str")  [return-value]
src/pkg/__init__.py:5: note: See https://mypy.readthedocs.io
Found 1 error in 1 file (checked 2 source files)
"""
PYTEST = """\
.F                                                                       [100%]
=================================== FAILURES ===================================
___________________________________ test_bad ___________________________________

    def test_bad() -> None:
>       assert f(2) == 3
E       assert 2 == 3
E        +  where 2 = f(2)

tests/test_x.py:9: AssertionError
=========================== short test summary info ============================
FAILED tests/test_x.py::test_bad - assert 2 == 3
1 failed, 1 passed in 0.01s
"""
TSC = """\
src/a.ts(3,7): error TS2322: Type 'number' is not assignable to type 'string'.
src/b.ts:4:1 - error TS2304: Cannot find name 'foo'.
"""


def test_ruff_full_and_concise(tmp_path: Path) -> None:
    for text in (RUFF_FULL, RUFF_CONCISE):
        [issue] = parse_output(text, "", tmp_path)
        assert (issue.rule, issue.path, issue.line, issue.tool) == (
            "F401",
            "src/pkg/__init__.py",
            1,
            "ruff",
        )
        assert issue.message == "`os` imported but unused"


def test_mypy_skips_notes(tmp_path: Path) -> None:
    [issue] = parse_output(MYPY, "", tmp_path)
    assert (issue.rule, issue.path, issue.line) == ("return-value", "src/pkg/__init__.py", 5)
    assert issue.message.startswith("Incompatible return value type")


def test_pytest_failure_takes_the_line_from_the_traceback(tmp_path: Path) -> None:
    [issue] = parse_output(PYTEST, "", tmp_path)
    assert (issue.rule, issue.path, issue.line) == ("test-failed", "tests/test_x.py", 9)
    assert issue.message == "tests/test_x.py::test_bad failed: assert 2 == 3"


def test_tsc_both_formats(tmp_path: Path) -> None:
    issues = parse_output(TSC, "", tmp_path)
    assert [(item.rule, item.path, item.line) for item in issues] == [
        ("TS2322", "src/a.ts", 3),
        ("TS2304", "src/b.ts", 4),
    ]


def test_eslint_and_ruff_json_relative_paths(tmp_path: Path) -> None:
    eslint = [
        {
            "filePath": str(tmp_path / "src" / "a.js"),
            "messages": [
                {"ruleId": "no-unused-vars", "severity": 2, "message": "x unused", "line": 2},
                {"ruleId": "semi", "severity": 1, "message": "Missing semicolon", "line": 3},
            ],
        }
    ]
    issues = parse_output(json.dumps(eslint), "", tmp_path)
    assert [(item.rule, item.level, item.path, item.line) for item in issues] == [
        ("no-unused-vars", "error", "src/a.js", 2),
        ("semi", "warning", "src/a.js", 3),
    ]
    ruff = [
        {
            "code": "E501",
            "message": "Line too long",
            "filename": str(tmp_path / "m.py"),
            "location": {"row": 7, "column": 89},
            "end_location": {"row": 7, "column": 120},
        }
    ]
    [issue] = parse_output(json.dumps(ruff), "", tmp_path)
    assert (issue.rule, issue.path, issue.line, issue.end_line) == ("E501", "m.py", 7, 7)


def test_sarif(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "bandit"}},
                "results": [
                    {
                        "ruleId": "B602",
                        "level": "error",
                        "message": {"text": "subprocess call with shell=True"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "src/run.py"},
                                    "region": {"startLine": 12, "endLine": 13},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }
    [issue] = parse_output(json.dumps(sarif), "", tmp_path)
    assert (issue.rule, issue.path, issue.line, issue.end_line, issue.tool) == (
        "B602",
        "src/run.py",
        12,
        13,
        "bandit",
    )


def test_junit_xml_from_a_report_file(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="1">
<testcase classname="tests.test_x" name="test_ok" file="tests/test_x.py" line="3"/>
<testcase classname="tests.test_x" name="test_bad" file="tests/test_x.py" line="8">
<failure message="assert 2 == 3">trace</failure></testcase>
</testsuite></testsuites>"""
    report = tmp_path / "reports" / "junit.xml"
    report.parent.mkdir()
    report.write_text(xml)
    assert report_files(("python", "-m", "pytest", "--junitxml=reports/junit.xml"), tmp_path) == [
        report.resolve()
    ]
    assert report_files(("pytest", "--junitxml", "../outside.xml"), tmp_path) == []
    [issue] = parse_output("", "", tmp_path, (report.read_text(),))
    assert (issue.rule, issue.path, issue.line) == ("test-failed", "tests/test_x.py", 8)
    assert issue.message == "tests.test_x::test_bad failure: assert 2 == 3"


def test_xml_with_entities_is_refused(tmp_path: Path) -> None:
    hostile = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><testsuite><testcase name="t"><failure message="&a;"/></testcase></testsuite>'
    assert parse_output(hostile, "", tmp_path) == []


def test_unknown_output_yields_nothing(tmp_path: Path) -> None:
    assert parse_output("Segmentation fault\n", "something odd", tmp_path) == []
    assert parse_output("[1, 2]", "", tmp_path) == []
