"""Evaluation of behaviour probes (#55, items 3 and 4).

A probe runs a command once per variant and asserts on what it printed. This module holds the
deterministic part: filling a variant into the command, reading the output as JSON or text,
evaluating each assertion and classifying whether the probe could run at all. Running the
process is the orchestration's job (with the harness's process runner and grants).

Classification of one probe on one workspace state:

* ``READY``: every variant ran to completion (no timeout, no missing or refused executable);
  its assertions may pass or fail (an output that is not JSON fails the JSON assertions: on the
  baseline that is the expected "fails before");
* ``UNAVAILABLE``: some variant could not run, so the probe says nothing about the behaviour.

An unavailable probe never passes: VERIFICATION records it ``BLOCKED``."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from governed_harness.ladder.jsonpath import select

Readiness = Literal["READY", "UNAVAILABLE"]
MAX_SHOWN = 300


@dataclass(frozen=True)
class VariantRun:
    """What one variant of a probe produced."""

    name: str
    ran: bool
    """The process started and finished on its own (not timed out, not refused, not missing)."""
    exit_code: int | None
    stdout: str
    stderr: str = ""
    problem: str | None = None
    """Why the variant did not run (``ran`` false), or why its output could not be read."""


@dataclass(frozen=True)
class AssertionResult:
    index: int
    kind: str
    passed: bool
    detail: str
    variant: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "index": self.index,
            "kind": self.kind,
            "passed": self.passed,
            "detail": self.detail,
        }
        if self.variant is not None:
            value["variant"] = self.variant
        return value


@dataclass(frozen=True)
class ProbeEvaluation:
    probe_id: str
    readiness: Readiness
    passed: bool
    results: tuple[AssertionResult, ...] = ()
    problems: tuple[str, ...] = ()
    variants: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "probeId": self.probe_id,
            "readiness": self.readiness,
            "passed": self.passed,
            "variants": list(self.variants),
            "problems": list(self.problems),
            "assertions": [item.as_dict() for item in self.results],
        }


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
NOT_JSON = object()
"""The document of a variant whose output is not JSON (``output: json``)."""


def fill(template: str, values: dict[str, str]) -> str:
    """``template`` with each ``{name}`` replaced by the variant's value (validated earlier)."""
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def _shown(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_SHOWN else text[: MAX_SHOWN - 3] + "..."


def _document(run: VariantRun, output: str) -> Any:
    if output != "json":
        return run.stdout
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError:
        return NOT_JSON


def _values(document: Any, path: str | None, output: str) -> list[Any]:
    if path is None:
        return [document]
    if output != "json":
        return []
    return select(document, path)


def _ordered(values: list[Any], order: str) -> bool:
    try:
        pairs = list(zip(values, values[1:], strict=False))
        if order == "ascending":
            return all(left <= right for left, right in pairs)
        return all(left >= right for left, right in pairs)
    except TypeError:
        return False


def _before(sequence: list[Any] | str, before: str, after: str) -> bool:
    if isinstance(sequence, str):
        first = sequence.find(before)
        second = sequence.find(after)
        return first >= 0 and second >= 0 and first < second
    texts = [
        item if isinstance(item, str) else json.dumps(item, sort_keys=True) for item in sequence
    ]
    if before not in texts or after not in texts:
        return False
    return texts.index(before) < texts.index(after)


def _single(
    index: int, assertion: Any, run: VariantRun, document: Any, output: str
) -> AssertionResult:
    kind = assertion.kind
    if document is NOT_JSON and kind != "exitCode" and (kind != "text"):
        return AssertionResult(
            index,
            kind,
            False,
            f"the output is not JSON (exit code {run.exit_code})",
            run.name,
        )
    if kind == "exitCode":
        passed = run.exit_code == assertion.equals
        return AssertionResult(
            index, kind, passed, f"exit code {run.exit_code}, expected {assertion.equals}", run.name
        )
    if kind == "jsonPath":
        values = _values(document, assertion.path, output)
        if assertion.present is not None:
            passed = bool(values) is assertion.present
            state = "present" if values else "absent"
            want = "present" if assertion.present else "absent"
            return AssertionResult(
                index, kind, passed, f"{assertion.path} is {state}, expected {want}", run.name
            )
        if assertion.matches is not None:
            pattern = re.compile(assertion.matches)
            passed = bool(values) and all(
                isinstance(item, str) and pattern.search(item) is not None for item in values
            )
            return AssertionResult(
                index,
                kind,
                passed,
                f"{assertion.path} = {_shown(values)} must match {assertion.matches!r}",
                run.name,
            )
        actual: Any = values[0] if len(values) == 1 else values
        passed = bool(values) and actual == assertion.equals
        return AssertionResult(
            index,
            kind,
            passed,
            f"{assertion.path} = {_shown(actual) if values else 'absent'}, expected "
            f"{_shown(assertion.equals)}",
            run.name,
        )
    if kind == "order":
        if assertion.path is not None:
            values = _values(document, assertion.path, output)
            sequence: list[Any] | str = values
        else:
            sequence = run.stdout if output != "json" else json.dumps(document, sort_keys=True)
        if assertion.order is not None:
            passed = (
                isinstance(sequence, list)
                and len(sequence) > 0
                and _ordered(sequence, assertion.order)
            )
            return AssertionResult(
                index,
                kind,
                passed,
                f"{assertion.path} = {_shown(sequence)} must be {assertion.order}",
                run.name,
            )
        passed = _before(sequence, str(assertion.before), str(assertion.after))
        return AssertionResult(
            index,
            kind,
            passed,
            f"{assertion.before!r} must appear before {assertion.after!r}",
            run.name,
        )
    # text
    if assertion.contains is not None:
        passed = assertion.contains in run.stdout
        return AssertionResult(
            index, kind, passed, f"output must contain {assertion.contains!r}", run.name
        )
    passed = re.search(str(assertion.matches), run.stdout) is not None
    return AssertionResult(
        index, kind, passed, f"output must match {assertion.matches!r}", run.name
    )


def evaluate(probe: Any, runs: list[VariantRun]) -> ProbeEvaluation:
    """Evaluate the assertions of ``probe`` (a ``ProbeDefinition``) on the runs of its variants,
    in the order of ``probe.expanded_variants()``."""
    names = tuple(run.name for run in runs)
    problems = [
        f"variant {run.name}: {run.problem or 'did not run'}" for run in runs if not run.ran
    ]
    documents = {run.name: _document(run, probe.output) for run in runs if run.ran}
    if problems:
        return ProbeEvaluation(probe.probe_id, "UNAVAILABLE", False, (), tuple(problems), names)
    results: list[AssertionResult] = []
    for index, assertion in enumerate(probe.assertions, start=1):
        if assertion.kind == "differs":
            chosen = [
                run for run in runs if not assertion.variants or run.name in assertion.variants
            ]
            observed = [
                (
                    "not JSON"
                    if documents[run.name] is NOT_JSON
                    else _shown(_values(documents[run.name], assertion.path, probe.output))
                )
                if assertion.path is not None
                else run.stdout
                for run in chosen
            ]
            passed = len(chosen) >= 2 and len(set(observed)) > 1
            results.append(
                AssertionResult(
                    index,
                    "differs",
                    passed,
                    f"{assertion.path or 'the output'} across "
                    f"{', '.join(run.name for run in chosen)}: "
                    + ("differs" if passed else "the same in every variant"),
                )
            )
            continue
        for run in runs:
            results.append(_single(index, assertion, run, documents[run.name], probe.output))
    return ProbeEvaluation(
        probe.probe_id, "READY", all(item.passed for item in results), tuple(results), (), names
    )


__all__ = [
    "AssertionResult",
    "ProbeEvaluation",
    "Readiness",
    "VariantRun",
    "evaluate",
    "fill",
]
