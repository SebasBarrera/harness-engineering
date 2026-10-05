"""Diff and quality checks of the agent-results settings: weakened controls, test quality,
secrets, risk factors and SARIF provenance. Every input is inline: diffs are built as unified
diff text and parsed with ``parse_unified_diff``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from governed_harness.checks.model import DiffFile, Issue, parse_unified_diff
from governed_harness.checks.risk import RiskSignal, detect_risk_factors
from governed_harness.checks.sarif import SarifResult, read_sarif
from governed_harness.checks.secrets import is_dummy_value, scan_secrets
from governed_harness.checks.suite_quality import (
    assertion_free_tests,
    changed_line_coverage,
    pytest_node_ids,
    untested_interface_methods,
)
from governed_harness.checks.weakened import check_weakened_controls
from governed_harness.domain.enums import FindingSeverity

HIGH = FindingSeverity.HIGH
MEDIUM = FindingSeverity.MEDIUM


def unified(
    path: str,
    removed: tuple[str, ...] = (),
    added: tuple[str, ...] = (),
    *,
    deleted: bool = False,
    created: bool = False,
    start: int = 1,
) -> str:
    old = "/dev/null" if created else f"a/{path}"
    new = "/dev/null" if deleted else f"b/{path}"
    lines = [
        f"--- {old}",
        f"+++ {new}",
        f"@@ -{start},{len(removed)} +{start},{len(added)} @@",
        *(f"-{line}" for line in removed),
        *(f"+{line}" for line in added),
    ]
    return "\n".join(lines) + "\n"


def diff_of(*parts: str) -> list[DiffFile]:
    return parse_unified_diff("".join(parts))


def rules(issues: Sequence[Issue]) -> list[str]:
    return [issue.rule_id for issue in issues]


# ===== weakened controls =====================================================================
class TestWeakenedControls:
    def test_deleted_test_file(self) -> None:
        issues = check_weakened_controls(
            diff_of(
                unified("tests/test_orders.py", ("def test_a():", "    assert 1"), deleted=True)
            ),
            "enforce",
        )
        assert rules(issues) == ["weakened.test-deleted"]
        assert issues[0].severity is HIGH
        assert issues[0].path == "tests/test_orders.py"
        assert issues[0].category == "weakened-controls"
        assert issues[0].recommendation

    def test_deleted_production_file_is_not_a_deleted_test(self) -> None:
        issues = check_weakened_controls(
            diff_of(unified("src/orders.py", ("def total():", "    return 1"), deleted=True)),
            "enforce",
        )
        assert issues == []

    def test_removed_test_function_not_readded(self) -> None:
        diff = diff_of(
            unified(
                "tests/test_orders.py",
                ("def test_cancel():", "async def test_refund():"),
                ("async def test_refund():",),
                start=10,
            )
        )
        issues = check_weakened_controls(diff, "enforce")
        assert rules(issues) == ["weakened.test-deleted"]
        assert issues[0].line == 10
        assert "test_cancel" in issues[0].message

    def test_moved_test_function_is_fine(self) -> None:
        diff = diff_of(
            unified("tests/test_orders.py", ("def test_cancel():",), ("def test_cancel():",))
        )
        assert check_weakened_controls(diff, "enforce") == []

    def test_removed_javascript_test(self) -> None:
        diff = diff_of(unified("web/orders.test.ts", ("it('cancels an order', () => {",), ()))
        assert rules(check_weakened_controls(diff, "enforce")) == ["weakened.test-deleted"]

    def test_assertions_removed(self) -> None:
        diff = diff_of(
            unified(
                "tests/test_orders.py",
                ("    assert total == 3", "    self.assertEqual(a, b)"),
                ("    mock.assert_called_once()", "    print(total)"),
            )
        )
        issues = check_weakened_controls(diff, "enforce")
        assert rules(issues) == ["weakened.assert-removed"]
        assert "2 assertion line(s) removed and 1 added" in issues[0].message

    def test_assertions_replaced_one_for_one(self) -> None:
        diff = diff_of(
            unified("tests/test_orders.py", ("    assert total == 3",), ("    assert total == 4",))
        )
        assert check_weakened_controls(diff, "enforce") == []

    def test_assertions_removed_from_production_code_is_not_a_test_issue(self) -> None:
        diff = diff_of(unified("src/orders.py", ("    assert total >= 0",), ()))
        assert check_weakened_controls(diff, "enforce") == []

    @pytest.mark.parametrize(
        "line",
        [
            '@pytest.mark.skip(reason="flaky")',
            "@pytest.mark.skipif(sys.platform == 'win32', reason='x')",
            "@pytest.mark.xfail",
            "    pytest.skip('later')",
            "@unittest.skip('later')",
            "@unittest.expectedFailure",
            "it.skip('cancels', () => {})",
            "describe.skip('orders', () => {})",
            "xit('cancels', () => {})",
        ],
    )
    def test_skip_added(self, line: str) -> None:
        issues = check_weakened_controls(
            diff_of(unified("tests/test_x.py", (), (line,))), "enforce"
        )
        assert rules(issues) == ["weakened.skip-added"]
        assert issues[0].severity is HIGH

    def test_skip_removed_is_fine(self) -> None:
        diff = diff_of(unified("tests/test_x.py", ("@pytest.mark.skip",), ()))
        assert check_weakened_controls(diff, "enforce") == []

    @pytest.mark.parametrize(
        "line",
        [
            "import os  # noqa: F401",
            "value = run()  # nosec B603",
            "x = f(y)  # type: ignore[arg-type]",
            "if debug:  # pragma: no cover",
            "// eslint-disable-next-line no-console",
            "// @ts-ignore",
            "// @ts-expect-error",
            "# pylint: disable=broad-except",
        ],
    )
    def test_suppression_added(self, line: str) -> None:
        issues = check_weakened_controls(diff_of(unified("src/app.py", (), (line,))), "enforce")
        assert rules(issues) == ["weakened.suppression-added"]
        assert issues[0].severity is MEDIUM

    def test_documentation_mentions_are_not_weakening(self) -> None:
        diff = diff_of(
            unified("docs/guide.md", (), ("Never add `# noqa` or `|| true` to a check.",))
        )
        assert check_weakened_controls(diff, "enforce") == []

    def test_threshold_lowered(self) -> None:
        diff = diff_of(unified("pyproject.toml", ("fail_under = 90",), ("fail_under = 80",)))
        issues = check_weakened_controls(diff, "enforce")
        assert rules(issues) == ["weakened.threshold-changed"]
        assert "from 90 to 80" in issues[0].message

    def test_threshold_raised_is_fine(self) -> None:
        diff = diff_of(unified("pyproject.toml", ("fail_under = 80",), ("fail_under = 90",)))
        assert check_weakened_controls(diff, "enforce") == []

    def test_threshold_removed_without_replacement(self) -> None:
        diff = diff_of(unified("setup.cfg", ("addopts = --cov-fail-under=85",), ("addopts = -q",)))
        issues = check_weakened_controls(diff, "enforce")
        assert rules(issues) == ["weakened.threshold-changed"]
        assert "removed without replacement" in issues[0].message

    def test_jest_coverage_threshold_lowered(self) -> None:
        diff = diff_of(unified("jest.config.js", ("      lines: 90,",), ("      lines: 50,",)))
        assert rules(check_weakened_controls(diff, "enforce")) == ["weakened.threshold-changed"]

    def test_threshold_in_source_file_is_ignored(self) -> None:
        diff = diff_of(unified("src/limits.py", ("threshold = 90",), ("threshold = 10",)))
        assert check_weakened_controls(diff, "enforce") == []

    @pytest.mark.parametrize(
        ("path", "line"),
        [
            ("pyproject.toml", "strict = true"),
            ("tsconfig.yaml", "strict: true"),
            ("package.json", '    "strict": true,'),
            ("pytest.ini", "addopts = --strict-markers"),
        ],
    )
    def test_strict_removed(self, path: str, line: str) -> None:
        issues = check_weakened_controls(diff_of(unified(path, (line,), ())), "enforce")
        assert rules(issues) == ["weakened.threshold-changed"]
        assert "strict mode removed" in issues[0].message

    def test_strict_kept_is_fine(self) -> None:
        diff = diff_of(
            unified("mypy.ini", ("strict = true",), ("strict = true", "warn_unused = true"))
        )
        assert check_weakened_controls(diff, "enforce") == []

    def test_workflow_check_removed(self) -> None:
        diff = diff_of(
            unified(
                ".github/workflows/ci.yml",
                ("      - run: mypy", "      - run: pytest -q"),
                ("      - run: pytest -q",),
            )
        )
        issues = check_weakened_controls(diff, "enforce")
        assert rules(issues) == ["weakened.threshold-changed"]
        assert "no longer runs mypy" in issues[0].message

    def test_workflow_check_rewritten_is_fine(self) -> None:
        diff = diff_of(
            unified(
                ".github/workflows/ci.yml",
                (
                    "      - name: Type check",
                    "      - run: mypy",
                    "      - run: pip install ruff",
                    '          cat "$RUNNER_TEMP/ruff.txt"',
                ),
                ("      - run: mypy src",),
            )
        )
        assert check_weakened_controls(diff, "enforce") == []

    @pytest.mark.parametrize(
        "handler",
        [
            ("    except Exception:", "        pass"),
            ("    except BaseException as error:", "        return None"),
            ("    except:", "        ..."),
            ("    except Exception as e:", "        continue"),
            ("    except Exception: pass",),
        ],
    )
    def test_broad_except_swallowing(self, handler: tuple[str, ...]) -> None:
        diff = diff_of(unified("src/app.py", (), ("    try:", "        run()", *handler)))
        issues = check_weakened_controls(diff, "enforce")
        assert rules(issues) == ["weakened.broad-except"]
        assert issues[0].line == 3

    @pytest.mark.parametrize(
        "handler",
        [
            ("    except ValueError:", "        pass"),
            ("    except Exception as error:", "        raise RuntimeError('x') from error"),
            ("    except Exception:", "        log.exception('failed')"),
        ],
    )
    def test_specific_or_handled_except_is_fine(self, handler: tuple[str, ...]) -> None:
        diff = diff_of(unified("src/app.py", (), ("    try:", "        run()", *handler)))
        assert check_weakened_controls(diff, "enforce") == []

    def test_broad_except_followed_by_a_line_of_another_hunk_is_fine(self) -> None:
        text = unified("src/app.py", (), ("    except Exception:",), start=5) + (
            "@@ -20,0 +21,1 @@\n+        pass\n"
        )
        diff = parse_unified_diff(text)
        assert check_weakened_controls(diff, "enforce") == []

    @pytest.mark.parametrize(
        ("path", "line"),
        [
            (".github/workflows/ci.yml", "      - run: pytest || true"),
            ("Makefile", "\tmypy src || exit 0"),
            (".github/workflows/ci.yml", "    continue-on-error: true"),
            ("Makefile", "\truff check --exit-zero src"),
            ("src/runner.py", "    subprocess.run(cmd, check=False)"),
            ("mypy.ini", "ignore_errors = True"),
            ("src/runner.py", "    result.returncode = 0"),
        ],
    )
    def test_error_to_success(self, path: str, line: str) -> None:
        issues = check_weakened_controls(diff_of(unified(path, (), (line,))), "enforce")
        assert "weakened.error-to-success" in rules(issues)
        assert all(issue.severity is HIGH for issue in issues)

    def test_exit_zero_after_except(self) -> None:
        diff = diff_of(unified("src/cli.py", (), ("except RuntimeError:", "    sys.exit(0)")))
        assert rules(check_weakened_controls(diff, "enforce")) == ["weakened.error-to-success"]

    def test_exit_zero_and_success_comparisons_are_fine(self) -> None:
        diff = diff_of(
            unified(
                "src/cli.py",
                (),
                (
                    "if result.returncode == 0:",
                    "    sys.exit(0)",
                    "check=True",
                    "shutil.rmtree(temp, ignore_errors=True)",
                    "# --exit-zero is used by the report step only",
                ),
            )
        )
        assert check_weakened_controls(diff, "enforce") == []

    def test_warn_policy_downgrades_every_issue(self) -> None:
        diff = diff_of(
            unified("tests/test_x.py", (), ("@pytest.mark.skip", "import os  # noqa")),
        )
        issues = check_weakened_controls(diff, "warn")
        assert rules(issues) == ["weakened.skip-added", "weakened.suppression-added"]
        assert {issue.severity for issue in issues} == {FindingSeverity.LOW}

    def test_quoted_line_is_trimmed(self) -> None:
        line = "x = 1  # noqa " + "y" * 400
        issue = check_weakened_controls(diff_of(unified("src/app.py", (), (line,))), "enforce")[0]
        assert len(issue.message) < 220
        assert "…" in issue.message


# ===== test quality ==========================================================================
TEST_SOURCE = """\
import pytest


def test_without_assertion():
    run()


def test_with_assert():
    assert run() == 1


def test_with_raises():
    with pytest.raises(ValueError):
        run()


def test_with_mock():
    mock.assert_called_once_with(1)


def test_with_fail():
    if run():
        pytest.fail("ran")


def helper():
    pass


class TestOrders:
    def test_method_without_assertion(self):
        self.client.get("/orders")

    def test_method_with_unittest_assertion(self):
        self.assertEqual(1, 1)

    def test_with_assert_raises(self):
        with self.assertRaises(KeyError):
            run()


class Helper:
    def test_not_collected(self):
        pass
"""


class TestAssertionFreeTests:
    def test_reports_each_assertion_free_test(self) -> None:
        issues = assertion_free_tests(
            {"tests/test_orders.py": TEST_SOURCE, "src/orders.py": "def test_x():\n    pass\n"},
            severity=MEDIUM,
        )
        assert rules(issues) == ["tests.assertion-free", "tests.assertion-free"]
        assert [(issue.line, issue.path) for issue in issues] == [
            (4, "tests/test_orders.py"),
            (31, "tests/test_orders.py"),
        ]
        assert "test_without_assertion" in issues[0].message
        assert all(issue.severity is MEDIUM and issue.category == "tests" for issue in issues)

    def test_unparsable_file_is_info(self) -> None:
        issues = assertion_free_tests({"tests/test_bad.py": "def test_x(:\n"}, severity=HIGH)
        assert rules(issues) == ["tests.unparsable"]
        assert issues[0].severity is FindingSeverity.INFO


class TestUntestedInterfaceMethods:
    def test_names_without_calls_are_reported(self) -> None:
        sources = {
            "tests/test_dispatch.py": "dispatcher.advance(order)\nassert service.status\n",
            "tests/test_other.py": "result = quote_fare(1, 2)\n",
        }
        issues = untested_interface_methods(
            ["Dispatcher.advance", "create_order", "quote_fare", "Ride.status", "create_order"],
            sources,
            severity=HIGH,
        )
        assert rules(issues) == ["tests.interface-untested"]
        assert issues[0].path is None
        assert "create_order" in issues[0].message

    def test_substring_is_not_a_call(self) -> None:
        sources = {"tests/test_x.py": "precreate_order(1)\nx = create_orders\n"}
        assert rules(untested_interface_methods(["create_order"], sources, severity=HIGH)) == [
            "tests.interface-untested"
        ]


class TestChangedLineCoverage:
    DIFF = unified(
        "src/app/fares.py",
        (),
        ("def fare(km):", "", "    if km > 10:", "        return 5", "    return 1"),
    )

    def coverage(self, executed: list[int], missing: list[int]) -> dict[str, object]:
        return {
            "files": {
                "/workspace/src/app/fares.py": {
                    "executed_lines": executed,
                    "missing_lines": missing,
                }
            }
        }

    def test_below_minimum_reports_missing_lines(self) -> None:
        percent, issues = changed_line_coverage(
            diff_of(self.DIFF), self.coverage([1, 3, 5], [4]), minimum_percent=80, severity=HIGH
        )
        assert percent == pytest.approx(75.0)
        assert rules(issues) == ["tests.changed-lines-uncovered"]
        assert issues[0].path == "src/app/fares.py"
        assert "lines 4" in issues[0].message
        assert "75.0%" in issues[0].message

    def test_above_minimum_has_no_issue(self) -> None:
        percent, issues = changed_line_coverage(
            diff_of(self.DIFF), self.coverage([1, 3, 5], [4]), minimum_percent=70, severity=HIGH
        )
        assert percent == pytest.approx(75.0)
        assert issues == []

    def test_relative_coverage_key(self) -> None:
        coverage = {"files": {"src/app/fares.py": {"executed_lines": [1, 3, 4, 5]}}}
        percent, issues = changed_line_coverage(
            diff_of(self.DIFF), coverage, minimum_percent=100, severity=HIGH
        )
        assert percent == 100.0
        assert issues == []

    def test_file_absent_from_coverage(self) -> None:
        percent, issues = changed_line_coverage(
            diff_of(self.DIFF), {"files": {}}, minimum_percent=80, severity=MEDIUM
        )
        assert percent is None
        assert rules(issues) == ["tests.changed-file-not-executed"]

    def test_tests_and_non_python_files_are_ignored(self) -> None:
        diff = diff_of(
            unified("tests/test_fares.py", (), ("def test_x():",)),
            unified("README.md", (), ("text",)),
        )
        assert changed_line_coverage(diff, {"files": {}}, minimum_percent=80, severity=HIGH) == (
            None,
            [],
        )

    def test_long_missing_list_is_truncated(self) -> None:
        diff = diff_of(unified("src/big.py", (), tuple(f"x{n} = {n}" for n in range(1, 21))))
        coverage = {
            "files": {"src/big.py": {"executed_lines": [], "missing_lines": list(range(1, 21))}}
        }
        percent, issues = changed_line_coverage(diff, coverage, minimum_percent=50, severity=HIGH)
        assert percent == 0.0
        assert issues[0].message.startswith("20 changed line(s)")
        assert "15, …" in issues[0].message


def test_pytest_node_ids() -> None:
    output = (
        "tests/test_a.py::test_one FAILED                    [ 50%]\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_one - assert 2 == 3\n"
        "FAILED tests/test_a.py::TestX::test_two[case-1] - KeyError\n"
        "ERROR tests/test_b.py::test_three - fixture 'db' not found\n"
        "ERROR tests/test_c.py - ImportError\n"
        "2 failed, 1 error in 0.10s\n"
    )
    assert pytest_node_ids(output) == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::TestX::test_two[case-1]",
        "tests/test_b.py::test_three",
    ]
    assert pytest_node_ids("3 passed in 0.01s\n") == []


# ===== secrets ===============================================================================
def secret_issues(path: str, *lines: str, task_text: str = "") -> list[tuple[str, FindingSeverity]]:
    issues = scan_secrets(diff_of(unified(path, (), lines)), task_text=task_text)
    return [(issue.rule_id, issue.severity) for issue in issues]


class TestSecrets:
    def test_haiku_dummy_test_credentials_are_info(self) -> None:
        found = secret_issues(
            "tests/test_auth.py",
            'client.login("alice", "testpwd")',
            '    payload = {"username": "alice", "password": "testpassword123"}',
            'user = create_user(email="a@b.co", password="testpassword123")',
        )
        assert found == [("secrets.test-dummy", FindingSeverity.INFO)] * 3

    def test_declared_value_in_test_is_critical(self) -> None:
        issues = scan_secrets(
            diff_of(
                unified(
                    "tests/conftest.py",
                    (),
                    ('    monkeypatch.setenv("ALERTS_PASSWORD", "dev-Alerts-2026!")',),
                )
            ),
            task_text='Alerts are protected. The admin password is "dev-Alerts-2026!" for now.',
        )
        declared = [issue for issue in issues if issue.rule_id == "secrets.declared-value"]
        assert len(declared) == 1
        assert declared[0].severity is FindingSeverity.CRITICAL
        assert declared[0].category == "security"
        assert all("dev-Alerts-2026!" not in issue.message for issue in issues)

    def test_declared_unquoted_value(self) -> None:
        found = secret_issues(
            "deploy/.env.example", "ADMIN=S3cret-Pass", task_text="The admin password: S3cret-Pass."
        )
        assert ("secrets.declared-value", FindingSeverity.CRITICAL) in found

    def test_quoted_task_identifiers_are_not_declared_secrets(self) -> None:
        found = secret_issues(
            "src/app/routes.py",
            '@app.post("/orders/create")',
            task_text='Add the endpoint "/orders/create" calling `create_order`. The token is stored.',
        )
        assert found == []

    def test_constant_in_conftest_is_medium(self) -> None:
        issues = scan_secrets(
            diff_of(unified("tests/conftest.py", (), ('PW = "Str0ng-Passw0rd!"',)))
        )
        assert [(issue.rule_id, issue.severity) for issue in issues] == [
            ("secrets.constant", MEDIUM)
        ]
        assert "'St…' (16 chars)" in issues[0].message
        assert "Str0ng-Passw0rd!" not in issues[0].message

    def test_constant_in_production_is_critical(self) -> None:
        assert secret_issues("src/app/settings.py", 'ADMIN_PW = "Str0ng-Passw0rd!"') == [
            ("secrets.constant", FindingSeverity.CRITICAL)
        ]

    def test_dummy_or_unrelated_constants_are_fine(self) -> None:
        assert (
            secret_issues(
                "src/app/settings.py",
                'PW = "changeme"',
                'MONKEY_NAME = "Abu-the-monkey"',
                'TOKEN_URL = "https://auth.example.com/token"',
                'TIMEOUT = "30s-default"',
                "PASSED = \"print({'status': 'PASSED'})\"",
            )
            == []
        )

    def test_production_credential_is_critical_and_masked(self) -> None:
        issues = scan_secrets(
            diff_of(unified("src/app/config.py", (), ('db_password = "S3cure-Value!"',)))
        )
        assert [(issue.rule_id, issue.severity) for issue in issues] == [
            ("secrets.credential", FindingSeverity.CRITICAL)
        ]
        assert "'S3…' (13 chars)" in issues[0].message
        assert "S3cure-Value!" not in issues[0].message

    @pytest.mark.parametrize(
        "line",
        [
            'API_KEY: str = "orange-river-stone"',
            'connect(host="db", password="Pr0d-Pa55-x")',
            'const config = { apiKey: "orange-river-stone" };',
            'secret_token = "orange-river-stone"',
        ],
    )
    def test_credential_forms(self, line: str) -> None:
        assert secret_issues("src/app/client.py", line) == [
            ("secrets.credential", FindingSeverity.CRITICAL)
        ]

    def test_non_dummy_test_credential_is_medium(self) -> None:
        assert secret_issues("tests/test_api.py", 'api_key = "live-Value-9981"') == [
            ("secrets.test-credential", MEDIUM)
        ]

    def test_positional_auth_call(self) -> None:
        assert secret_issues(
            "src/app/seed.py", 'register_user("bob@example.org", "Hunter-2-Real")'
        ) == [("secrets.credential", FindingSeverity.CRITICAL)]
        assert secret_issues("src/app/urls.py", 'router.register("/users/login", handler)') == []

    @pytest.mark.parametrize(
        "line",
        [
            "token = 'ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8'",
            "aws = 'AKIA" + "ABCDEFGHIJKLMNOP'",
            "openai = 'sk-" + "abcdefghijklmnopqrstuvwxyz123456'",
            "slack = 'xoxb-" + "123456-abcdef'",
            "-----BEGIN RSA PRIVATE KEY-----",
        ],
    )
    def test_credential_formats_are_critical_even_in_tests(self, line: str) -> None:
        found = secret_issues("tests/fixtures/keys.py", line)
        assert ("secrets.credential-format", FindingSeverity.CRITICAL) in found

    def test_environment_assignments(self) -> None:
        assert secret_issues("src/app/boot.py", 'os.environ["DB_PASSWORD"] = "Pr0d-Pass-77"') == [
            ("secrets.environment-assignment", FindingSeverity.CRITICAL)
        ]
        assert secret_issues(
            "src/app/boot.py", 'os.environ.setdefault("API_TOKEN", "Pr0d-Token-77")'
        ) == [("secrets.environment-assignment", FindingSeverity.CRITICAL)]
        assert secret_issues("web/src/boot.js", 'process.env.API_KEY = "orange-river-stone"') == [
            ("secrets.environment-assignment", FindingSeverity.CRITICAL)
        ]
        assert secret_issues(
            "tests/test_x.py", 'monkeypatch.setenv("API_TOKEN", "dummy-token")'
        ) == [("secrets.test-dummy", FindingSeverity.INFO)]
        assert secret_issues("tests/test_x.py", 'os.putenv("SERVICE_SECRET", "Qx7-real-ish")') == [
            ("secrets.environment-assignment", MEDIUM)
        ]
        assert secret_issues("src/app/boot.py", 'os.environ["LOG_LEVEL"] = "debug-verbose"') == []

    def test_templates_and_empty_values_are_ignored(self) -> None:
        assert (
            secret_issues(
                "src/app/client.py",
                'token = f"Bearer {value}"',
                'password = ""',
                'password = "${DB_PASSWORD}"',
                'password: "{{ secrets.DB_PASSWORD }}"',
                'api_key = "<your-api-key>"',
            )
            == []
        )

    def test_comparisons_and_names_are_not_assignments(self) -> None:
        assert (
            secret_issues(
                "src/app/auth.py",
                'if password == "Some-Value-1":',
                'password_field = "user_password_input"',
                'token_type = "bearer-token"',
                '    "inputTokens": "input_tokens",',
            )
            == []
        )

    def test_one_issue_per_line_and_rule(self) -> None:
        found = secret_issues(
            "src/app/config.py", 'password = "Val-1-abcdef"; token = "Val-2-abcdef"'
        )
        assert found == [("secrets.credential", FindingSeverity.CRITICAL)]

    def test_removed_lines_are_not_scanned(self) -> None:
        diff = diff_of(unified("src/app/config.py", ('password = "Old-Value-123"',), ()))
        assert scan_secrets(diff) == []

    @pytest.mark.parametrize(
        ("value", "dummy"),
        [
            ("testpwd", True),
            ("testpassword123", True),
            ("ChangeMe", True),
            ("fake-key.v2", True),
            ("xxxxxxxx", True),
            ("********", True),
            ("<token>", True),
            ("${TOKEN}", True),
            ("{password}", True),
            ("abc", True),
            ("dev-Alerts-2026!", False),
            ("Str0ng-Passw0rd!", False),
            ("hunter22", False),
        ],
    )
    def test_is_dummy_value(self, value: str, dummy: bool) -> None:
        assert is_dummy_value(value) is dummy


# ===== risk factors ==========================================================================
def factors(diff: list[DiffFile], **kwargs: object) -> list[tuple[str, str | None, int | None]]:
    signals = detect_risk_factors(diff, **kwargs)  # type: ignore[arg-type]
    assert all(isinstance(signal, RiskSignal) for signal in signals)
    return [(signal.factor, signal.path, signal.line) for signal in signals]


class TestRiskFactors:
    def test_new_dependencies(self) -> None:
        diff = diff_of(
            unified(
                "pyproject.toml",
                (),
                (
                    '  "httpx>=0.27",',
                    '  "E",',
                    'version = "0.9.0"',
                    'requires-python = ">=3.12"',
                    'readme = {file = "README.md"}',
                    'Homepage = "https://example.org/project"',
                ),
            ),
            unified("requirements-dev.txt", (), ("# tools", "requests==2.32.3", "-r base.txt")),
            unified("web/package.json", (), ('    "axios": "^1.7.0",', '  "version": "1.0.0",')),
            unified("go.mod", (), ("require github.com/acme/rides v1.2.3",)),
            unified("Pipfile", (), ('flask = "*"',)),
        )
        assert [signal for signal in factors(diff) if signal[0] == "newDependency"] == [
            ("newDependency", "Pipfile", 1),
            ("newDependency", "go.mod", 1),
            ("newDependency", "pyproject.toml", 1),
            ("newDependency", "requirements-dev.txt", 2),
            ("newDependency", "web/package.json", 1),
        ]

    def test_poetry_dependency(self) -> None:
        diff = diff_of(
            unified("pyproject.toml", (), ('pydantic = "^2.7"', 'rich = {version = "^13"}'))
        )
        assert factors(diff) == [
            ("newDependency", "pyproject.toml", 1),
            ("newDependency", "pyproject.toml", 2),
        ]

    def test_authentication_once_per_file(self) -> None:
        diff = diff_of(
            unified("src/app/auth/service.py", (), ("def login():", "    token = make()")),
            unified("src/app/users.py", (), ("x = 1", "role = user.role", "jwt = sign()")),
            unified("tests/test_auth.py", (), ("def test_login():",)),
            unified("docs/security.md", (), ("Tokens expire.",)),
        )
        assert [signal for signal in factors(diff) if signal[0] == "authentication"] == [
            ("authentication", "src/app/auth/service.py", None),
            ("authentication", "src/app/users.py", 2),
        ]

    def test_destructive_migration(self) -> None:
        diff = diff_of(
            unified(
                "migrations/0003_drop.sql",
                (),
                (
                    "DROP TABLE riders;",
                    "DELETE FROM rides;",
                    "DELETE FROM rides WHERE id = 1;",
                    "DELETE FROM drivers",
                    "  WHERE active = false;",
                    "ALTER TABLE rides DROP COLUMN fare;",
                    "CREATE TABLE x (id int);",
                ),
            ),
            unified(
                "alembic/versions/a1_migration.py", (), ("    op.drop_column('rides', 'fare')",)
            ),
            unified("src/app/cleanup.py", (), ("sql = 'DROP TABLE tmp'",)),
        )
        assert [signal for signal in factors(diff) if signal[0] == "destructiveMigration"] == [
            ("destructiveMigration", "alembic/versions/a1_migration.py", 1),
            ("destructiveMigration", "migrations/0003_drop.sql", 1),
            ("destructiveMigration", "migrations/0003_drop.sql", 2),
            ("destructiveMigration", "migrations/0003_drop.sql", 6),
        ]

    def test_public_contract(self) -> None:
        diff = diff_of(
            unified("src/app/api.py", (), ("x = 1",)),
            unified(
                "src/app/orders.py",
                ("def cancel(order):", "def _helper():", "class Order:", "def total(order):"),
                ("def total(order, tax):", '__all__ = ["Order", "total"]'),
            ),
            unified("tests/test_orders.py", ("def build_order():",), ()),
        )
        assert [
            signal
            for signal in factors(diff, interface_paths=["./src/app/api.py"])
            if signal[0] == "publicContract"
        ] == [
            ("publicContract", "src/app/api.py", None),
            ("publicContract", "src/app/orders.py", 1),
            ("publicContract", "src/app/orders.py", 2),
            ("publicContract", "src/app/orders.py", 3),
        ]

    def test_network(self) -> None:
        diff = diff_of(
            unified(
                "src/app/client.py",
                (),
                ("import requests", "from urllib import request", "x = cursor.fetch()"),
            ),
            unified(
                "web/src/api.ts", (), ("const r = await fetch(url);", "import axios from 'axios';")
            ),
            unified("tests/test_client.py", (), ("import httpx",)),
        )
        assert [signal for signal in factors(diff) if signal[0] == "network"] == [
            ("network", "src/app/client.py", 1),
            ("network", "src/app/client.py", 2),
            ("network", "web/src/api.ts", 1),
            ("network", "web/src/api.ts", 2),
        ]

    def test_float_money(self) -> None:
        diff = diff_of(
            unified(
                "src/app/fares.py",
                (),
                (
                    "total_price: float = 0.0",
                    "fee = float(raw)",
                    "ratio = float(x)",
                    "amount = Decimal(raw)",
                ),
            ),
            unified("tests/test_fares.py", (), ("price = float(1)",)),
        )
        assert [signal for signal in factors(diff) if signal[0] == "floatMoney"] == [
            ("floatMoney", "src/app/fares.py", 1),
            ("floatMoney", "src/app/fares.py", 2),
        ]

    def test_sensitive_logging(self) -> None:
        diff = diff_of(
            unified(
                "src/app/log.py",
                (),
                (
                    'logger.info("login password=%s", password)',
                    "print(order_id)",
                    "console.log(card.number)",
                    'logging.debug("rides %s", ids)',
                ),
            ),
        )
        assert [signal for signal in factors(diff) if signal[0] == "sensitiveLogging"] == [
            ("sensitiveLogging", "src/app/log.py", 1),
            ("sensitiveLogging", "src/app/log.py", 3),
        ]

    def test_deleted_without_tests(self) -> None:
        removed = tuple(f"value_{n} = {n}" for n in range(20))
        alone = factors(diff_of(unified("src/app/legacy.py", removed, ())))
        assert alone == [("deletedWithoutTests", None, None)]
        with_tests = factors(
            diff_of(
                unified("src/app/legacy.py", removed, ()),
                unified("tests/test_legacy.py", ("def check():",), ()),
            )
        )
        assert ("deletedWithoutTests", None, None) not in with_tests
        few = factors(diff_of(unified("src/app/legacy.py", removed[:19], ())))
        assert few == []

    def test_order_is_by_factor_then_path_then_line(self) -> None:
        diff = diff_of(
            unified("src/b.py", (), ("import socket", "fee: float = 1")),
            unified("requirements.txt", (), ("httpx",)),
            unified("src/a.py", (), ("import grpc",)),
        )
        assert factors(diff) == [
            ("newDependency", "requirements.txt", 1),
            ("network", "src/a.py", 1),
            ("network", "src/b.py", 1),
            ("floatMoney", "src/b.py", 2),
        ]


# ===== SARIF =================================================================================
SARIF = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {
                "driver": {
                    "name": "semgrep",
                    "semanticVersion": "1.80.0",
                    "rules": [
                        {"id": "python.sqli", "properties": {"version": "3"}},
                        {"id": "python.eval"},
                    ],
                }
            },
            "results": [
                {
                    "ruleId": "python.sqli",
                    "level": "error",
                    "message": {"text": "SQL injection"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "file:///ws/src/app/db.py"},
                                "region": {"startLine": 12},
                            }
                        }
                    ],
                },
                {
                    "ruleIndex": 1,
                    "message": {"text": "eval used"},
                    "locations": [
                        {"physicalLocation": {"artifactLocation": {"uri": "src/app/x.py"}}}
                    ],
                },
                "not a result",
            ],
        },
        {
            "tool": {
                "driver": {
                    "name": "bandit",
                    "version": "1.7.9",
                    "rules": [{"id": "B602", "properties": {"ruleVersion": "2024.1"}}],
                }
            },
            "results": [{"ruleId": "B602", "level": "note", "message": {"text": "shell"}}],
        },
        "not a run",
    ],
}


class TestSarif:
    def test_results_with_provenance(self) -> None:
        results = read_sarif(json.dumps(SARIF), Path("/ws"))
        assert results == [
            SarifResult(
                rule_id="python.sqli",
                level="error",
                message="SQL injection",
                path="src/app/db.py",
                line=12,
                tool="semgrep",
                tool_version="1.80.0",
                rule_version="3",
            ),
            SarifResult(
                rule_id="python.eval",
                level="warning",
                message="eval used",
                path="src/app/x.py",
                line=None,
                tool="semgrep",
                tool_version="1.80.0",
                rule_version="1.80.0",
            ),
            SarifResult(
                rule_id="B602",
                level="note",
                message="shell",
                path=None,
                line=None,
                tool="bandit",
                tool_version="1.7.9",
                rule_version="2024.1",
            ),
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "{not json",
            "[]",
            '{"version": "1.0", "runs": []}',
            '{"version": "2.1.0"}',
            '{"version": "2.1.0", "runs": {}}',
        ],
    )
    def test_invalid_documents(self, text: str) -> None:
        assert read_sarif(text, Path("/ws")) == []

    def test_malformed_result_is_kept_without_location(self) -> None:
        text = json.dumps(
            {
                "version": "2.1.0",
                "runs": [{"tool": "x", "results": [{"locations": {"a": 1}, "ruleId": 7}]}],
            }
        )
        assert read_sarif(text, Path("/ws")) == [
            SarifResult(
                rule_id="7",
                level="warning",
                message="no message",
                path=None,
                line=None,
                tool="sarif",
                tool_version=None,
                rule_version=None,
            )
        ]
