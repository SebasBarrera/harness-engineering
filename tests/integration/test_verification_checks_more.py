"""Architecture limits against the baseline, SARIF reports, invariants, test quality and the
Ruff/Mypy ratchet (#40, #52)."""

from __future__ import annotations

import json
from pathlib import Path

from governed_harness.domain.enums import FindingSeverity, ResultStatus
from tests.integration.test_verification_checks import (
    GOOD,
    TEST,
    configure,
    latest,
    patch,
    rules,
    run_task,
    task_yaml,
)


def test_architecture_limits_spare_preexisting_violations(
    python_workspace: Path, tmp_path: Path
) -> None:
    (python_workspace / "src" / "sample" / "legacy.py").write_text(
        "def legacy(value: int) -> int:\n"
        + "".join(f"    value += {number}\n" for number in range(6))
        + "    return value\n"
    )
    configure(python_workspace, {"architecture": {"maxFunctionLines": 4, "severity": "HIGH"}})
    long_function = (
        "def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n"
        "    result = subtotal\n"
        "    if subtotal >= threshold:\n"
        "        result = subtotal * (1 - rate)\n"
        "    return result\n"
    )
    legacy = (python_workspace / "src" / "sample" / "legacy.py").read_text() + "\n\nX = 1\n"
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml(
            [
                patch("src/sample/pricing.py", long_function),
                patch("src/sample/legacy.py", legacy),
                patch("tests/test_pricing.py", TEST),
            ]
        ),
    )
    found = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "architecture.function-lines"
    ]
    by_path = {item.location.path: item for item in found if item.location}
    assert by_path["src/sample/pricing.py"].severity is FindingSeverity.HIGH
    assert by_path["src/sample/legacy.py"].severity is FindingSeverity.LOW
    assert by_path["src/sample/legacy.py"].message.startswith("Pre-existing")


def test_sarif_reports_and_invariants(python_workspace: Path, tmp_path: Path) -> None:
    (python_workspace / "reports").mkdir()
    (python_workspace / "reports" / "scan.sarif").write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "semgrep", "version": "1.2.3"}},
                        "results": [
                            {
                                "ruleId": "python.lang.security.audit",
                                "level": "error",
                                "message": {"text": "Dangerous call"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "src/sample/pricing.py"},
                                            "region": {"startLine": 2},
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    )
    configure(
        python_workspace,
        {
            "sarif": [
                {"path": "reports/scan.sarif", "tool": "semgrep"},
                {"path": "reports/missing.sarif", "required": True},
            ],
            "invariants": [
                {"id": "always-red", "command": ["python", "-c", "raise SystemExit(1)"]}
            ],
        },
    )
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", GOOD), patch("tests/test_pricing.py", TEST)]),
    )
    found = rules(application, python_workspace, run)
    sarif = found["sarif.semgrep.python.lang.security.audit"]
    assert sarif.severity is FindingSeverity.HIGH and "semgrep 1.2.3" in sarif.message
    assert found["sarif.missing-report"].severity is FindingSeverity.HIGH
    validations = latest(application, python_workspace, run)
    assert validations["invariant.always-red"].status is ResultStatus.FAILED
    assert validations["invariant.always-red"].mandatory


def test_test_quality_checks(python_workspace: Path, tmp_path: Path) -> None:
    marker = tmp_path / "flaky-marker"
    configure(
        python_workspace,
        {
            "testQuality": {
                "assertions": True,
                "diffCoverage": 100,
                "flakyReruns": 1,
                "severity": "HIGH",
            }
        },
    )
    code = GOOD + "\n\ndef unused(value: int) -> int:\n    return value * 2\n"
    tests = (
        TEST
        + "\n\ndef test_without_assertion() -> None:\n    apply_discount(1, 2, 0.1)\n"
        + "\n\ndef test_flaky() -> None:\n"
        + "    from pathlib import Path\n"
        + f"    marker = Path({str(marker)!r})\n"
        + "    seen = marker.exists()\n"
        + "    marker.write_text('x')\n"
        + "    assert not seen\n"
    )
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", code), patch("tests/test_pricing.py", tests)]),
    )
    found = rules(application, python_workspace, run)
    assert found["tests.assertion-free"].severity is FindingSeverity.HIGH
    assert "test_without_assertion" in found["tests.assertion-free"].message
    assert found["tests.changed-lines-uncovered"].location is not None
    assert found["tests.flaky"].severity is FindingSeverity.HIGH
    assert latest(application, python_workspace, run)["harness.test-quality"].status is (
        ResultStatus.FAILED
    )


def test_ratchet_blocks_new_ruff_problems_only(python_workspace: Path, tmp_path: Path) -> None:
    configure(python_workspace, {"ratchet": "enforce"})
    worse = "import os\n\n\n" + GOOD
    application, run = run_task(
        python_workspace,
        tmp_path,
        task_yaml([patch("src/sample/pricing.py", worse), patch("tests/test_pricing.py", TEST)]),
    )
    found = [
        item
        for item in application.list_findings(python_workspace, run)
        if item.rule_id == "ratchet.regressed"
    ]
    assert any("python.ruff" in item.message and "F401" in item.message for item in found)
    assert latest(application, python_workspace, run)["harness.ratchet"].status is (
        ResultStatus.FAILED
    )
