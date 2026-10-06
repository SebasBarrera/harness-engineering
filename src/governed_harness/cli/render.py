"""Human-readable rendering of CLI results.

JSON stays the contract for scripts: it is printed whenever standard output is not a terminal
or ``--json`` is given. On a terminal (or with ``--no-json``) the same data is rendered as text:
known shapes (runs, tasks, findings, execution status, review brief) get a dedicated layout and
anything else falls back to an indented ``key: value`` listing. Rendering never changes the
data, only how it is shown."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")


def wants_json(flag: bool | None, stream: Any = None) -> bool:
    """``True``/``False`` when the user chose; otherwise JSON unless the stream is a terminal."""
    if flag is not None:
        return flag
    target = stream if stream is not None else sys.stdout
    try:
        return not bool(target.isatty())
    except (AttributeError, ValueError):
        return True


def label(key: str) -> str:
    """``changeSetDigest`` -> ``change set digest``."""
    return _CAMEL.sub(lambda match: " " + match.group(1).lower(), key).replace("_", " ")


def short(value: object, width: int = 72) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _scalar(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def generic(value: object, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if _is_scalar(value):
        return [pad + _scalar(value)]
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            name = label(str(key))
            if _is_scalar(item):
                lines.append(f"{pad}{name}: {_scalar(item)}")
            elif isinstance(item, Sequence) and not item:
                lines.append(f"{pad}{name}: -")
            elif isinstance(item, Sequence) and all(_is_scalar(entry) for entry in item):
                lines.append(f"{pad}{name}: " + ", ".join(_scalar(entry) for entry in item))
            elif isinstance(item, Mapping) and not item:
                lines.append(f"{pad}{name}: -")
            else:
                lines.append(f"{pad}{name}:")
                lines.extend(generic(item, indent + 1))
        return lines
    if isinstance(value, Sequence):
        lines = []
        for index, item in enumerate(value, start=1):
            if _is_scalar(item):
                lines.append(f"{pad}- {_scalar(item)}")
            else:
                lines.append(f"{pad}[{index}]")
                lines.extend(generic(item, indent + 1))
        return lines
    return [pad + str(value)]


def table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> list[str]:
    widths = [len(item) for item in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    line = "  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True))
    rule = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
    return [line.rstrip(), rule, *body]


def _location(finding: Mapping[str, Any]) -> str:
    location = finding.get("location") or {}
    where = location.get("path") or "-"
    if location.get("startLine"):
        where += f":{location['startLine']}"
    return str(where)


def _runs(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["No runs recorded. Start one with: harness run start --task <taskId>"]
    rows = [
        [
            str(item["executionId"]),
            str(item["taskId"]),
            str(item["currentPhase"]),
            str(item["status"]),
            str(item.get("updatedAt", ""))[:19],
        ]
        for item in items
    ]
    return table(rows, ["RUN", "TASK", "PHASE", "STATUS", "UPDATED"])


def _tasks(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["No tasks recorded. Create one with: harness task create --file task.yaml"]
    rows = [
        [
            str(item["taskId"]),
            short(item.get("title", ""), 48),
            str(len(item.get("acceptanceCriteria") or ())),
            str(item.get("implementation", {}).get("mode", "-")),
        ]
        for item in items
    ]
    return table(rows, ["TASK", "TITLE", "CRITERIA", "IMPLEMENTATION"])


def _findings(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["No findings were recorded."]
    rows = [
        [
            str(item["severity"]),
            str(item["ruleId"]),
            _location(item),
            short(item.get("message", ""), 60),
        ]
        for item in items
    ]
    return table(rows, ["SEVERITY", "RULE", "LOCATION", "MESSAGE"])


def execution_headline(execution: Mapping[str, Any]) -> str:
    """One line that says what the run is waiting for, without changing the stored status."""
    status = execution.get("status")
    phase = execution.get("currentPhase")
    run_id = execution.get("executionId")
    if status == "BLOCKED" and phase == "DECISION":
        return f"Run {run_id}: automated phases finished, waiting for a human decision."
    if status == "PASSED":
        return f"Run {run_id}: closed (PASSED)."
    if status == "CANCELLED":
        return f"Run {run_id}: cancelled."
    if status in {"FAILED", "BLOCKED", "INCONCLUSIVE", "TIMED_OUT", "ERROR"}:
        reason = execution.get("terminalReason")
        suffix = f" ({reason})" if reason else ""
        return f"Run {run_id}: {status} in {phase}{suffix}."
    return f"Run {run_id}: {status} in {phase}."


def next_steps(execution: Mapping[str, Any]) -> list[str]:
    """The commands a person most likely needs next, ready to copy."""
    run_id = execution.get("executionId")
    status = execution.get("status")
    phase = execution.get("currentPhase")
    if status == "BLOCKED" and phase == "DECISION":
        return [
            f"harness review --run {run_id}",
            f"harness gate decide --run {run_id}   (interactive on a terminal)",
        ]
    if status == "PASSED":
        return [
            f"harness trace --run {run_id} --format markdown",
            f"harness retrospect --run {run_id}",
        ]
    if status == "CANCELLED":
        return []
    if phase == "INTENT" and status == "BLOCKED":
        return [
            f"harness task questions --task {execution.get('taskId')}",
            f"harness task clarify --task {execution.get('taskId')} --file answers.yaml",
        ]
    if status in {"FAILED", "BLOCKED", "INCONCLUSIVE", "TIMED_OUT", "ERROR"}:
        return [
            f"harness review --run {run_id}",
            f"harness run continue --run {run_id}   (after fixing the cause)",
            f"harness run cancel --run {run_id}",
        ]
    return [f"harness run continue --run {run_id}"]


def _execution(value: Mapping[str, Any]) -> list[str]:
    lines = [execution_headline(value), ""]
    for key in ("taskId", "currentPhase", "status", "attempt", "changeSetDigest"):
        if key in value:
            lines.append(f"{label(key)}: {_scalar(value.get(key))}")
    steps = next_steps(value)
    if steps:
        lines.extend(["", "Next:", *(f"  {step}" for step in steps)])
    return lines


def _status(value: Mapping[str, Any]) -> list[str]:
    execution = value["execution"]
    lines = _execution(execution)
    gate = value.get("gate")
    lines.append("")
    if gate:
        lines.append(f"gate: {gate['status']} ({', '.join(gate['reasonCodes'])})")
    else:
        lines.append("gate: not evaluated yet")
    decision = value.get("humanDecision")
    if decision:
        lines.append(f"human decision: {decision['decision']} by {decision['actor']['actorId']}")
    summary = value.get("validationSummary", {})
    by_status = ", ".join(f"{key} {count}" for key, count in summary.get("byStatus", {}).items())
    lines.append(f"validations: {summary.get('total', 0)} ({by_status or 'none'})")
    findings = value.get("findings", {})
    by_severity = ", ".join(
        f"{key} {count}" for key, count in findings.get("bySeverity", {}).items()
    )
    lines.append(f"findings: {findings.get('total', 0)} ({by_severity or 'none'})")
    lines.append(f"event chain valid: {_scalar(value.get('eventChainValid'))}")
    lines.extend(["", "Phases:"])
    rows = [
        [
            str(item["phaseId"]),
            str(item["attempt"]),
            str(item["status"]),
            short(item["summary"], 60),
        ]
        for item in value.get("phases", [])
    ]
    lines.extend("  " + line for line in table(rows, ["PHASE", "ATTEMPT", "STATUS", "SUMMARY"]))
    return lines


def _decision(value: Mapping[str, Any]) -> list[str]:
    decision = value["decision"]
    lines = [
        f"Recorded {decision['decision']} by {decision['actor']['actorId']} "
        f"for {decision['changeSetDigest']}."
    ]
    if decision.get("expiresAt"):
        lines.append(f"The exception expires at {decision['expiresAt']}.")
    lines.extend(["", *_execution(value["execution"])])
    return lines


def _doctor(value: Mapping[str, Any]) -> list[str]:
    lines = [f"harness {value.get('version')}: {value.get('status')}", ""]
    for name, check in value.get("checks", {}).items():
        detail = next(
            (
                str(check[key])
                for key in ("message", "version", "mechanism", "path", "provider", "head")
                if check.get(key)
            ),
            "",
        )
        lines.append(f"{check.get('status', '-'):<15} {label(name):<22} {short(detail, 60)}")
        for item in check.get("validators", ()):
            lines.append(
                f"{'':<15}   {item['id']:<20} {item['status']}"
                + (f" - {item['message']}" if item.get("message") else "")
            )
        if check.get("hint"):
            lines.append(f"{'':<15}   fix: {check['hint']}")
        for item in check.get("validators", ()):
            if item.get("hint"):
                lines.append(f"{'':<15}   fix {item['id']}: {item['hint']}")
    return lines


def _init(value: Mapping[str, Any]) -> list[str]:
    lines = [f"Wrote {value['configuration']}"]
    profiles = ", ".join(
        f"{item['profileId']} ({item['confidence']:.2f})" for item in value.get("profiles", [])
    )
    lines.append(f"Detected profiles: {profiles or 'none'}")
    if "gitignore" in value:
        lines.append(f".gitignore: .harness/ {value['gitignore']}")
    if value.get("exampleTask"):
        lines.append(f"Example task: {value['exampleTask']}")
    lines.extend(["", "Next:", *(f"  {step}" for step in value.get("next", []))])
    return lines


def _review(brief: Mapping[str, Any]) -> list[str]:
    run = brief["run"]
    waiting = ", waiting for your decision" if run["awaitingDecision"] else ""
    lines = [
        f"Decision brief - run {run['executionId']} ({run['status']} in "
        f"{run['currentPhase']}{waiting})",
        f"ChangeSet {run['changeSetDigest'] or 'not computed yet'}",
        "",
        "What was asked",
        f"  {brief['asked']['title']}",
        f"  intent: {short(brief['asked']['intent'], 100)}",
    ]
    for item in brief["asked"]["requirements"]:
        lines.append(f"  requirement {item['requirementId']}: {short(item['text'], 90)}")
    for item in brief["asked"]["acceptanceCriteria"]:
        lines.append(
            f"  criterion {item['criterionId']} [{item['priority']}]: {short(item['text'], 90)}"
        )
    for item in brief["asked"]["constraints"]:
        lines.append(f"  constraint: {short(item, 100)}")
    lines.extend(_assumption_lines(brief["asked"].get("assumptions")))
    changed = brief["changed"]
    totals = changed.get("totals") or {"files": 0, "additions": 0, "deletions": 0}
    lines.extend(
        [
            "",
            f"What changed ({totals['files']} file(s), +{totals['additions']} "
            f"-{totals['deletions']})",
        ]
    )
    for item in changed.get("files", []):
        lines.append(
            f"  {item['status']:<9} {item['path']}  +{item['additions']} -{item['deletions']}"
        )
    gate = brief.get("gate")
    lines.append("")
    if gate:
        lines.append(f"Gate {gate['status']}")
        lines.extend(f"  - {item['explanation']}" for item in gate["reasons"])
    else:
        lines.append("Gate: not evaluated yet")
    risks = brief.get("risks", [])
    lines.extend(["", f"Risks ({len(risks)})"])
    if not risks:
        lines.append("  no current finding")
    for item in risks:
        flag = "BLOCKS" if item["blocking"] else "info"
        lines.append(
            f"  {flag:<6} {item['severity']:<8} {item['ruleId']}  {item['location']}  "
            f"{short(item['message'], 80)}"
        )
    verified = brief["verified"]
    lines.extend(["", f"Verified on {verified['changeSetDigest']}"])
    for item in verified["validations"]:
        kind = "mandatory" if item["mandatory"] else "optional"
        retry = f", {item['attempts']} attempts" if item["attempts"] > 1 else ""
        lines.append(f"  {item['status']:<14} {item['validatorId']} ({kind}{retry})")
    for item in verified["requirements"]:
        tests = ", ".join(item["tests"]) or "no test"
        lines.append(f"  requirement {item['requirementId']}: {tests}")
    if brief.get("notVerified"):
        lines.extend(["", "Not verified"])
        lines.extend(f"  - {item}" for item in brief["notVerified"])
    exceptions = brief.get("exceptions") or []
    if exceptions:
        lines.extend(["", "Exceptions in force"])
        for item in exceptions:
            lines.append(
                f"  {item['exceptionId']} by {item['actorId']} until {item['expiresAt']}: "
                f"{short(item['rationale'], 70)}"
            )
    history = brief["history"]
    lines.extend(
        [
            "",
            "History: "
            f"{history['implementationAttempts']} implementation attempt(s), "
            f"{history['verificationAttempts']} verification attempt(s), "
            f"{history['automaticCorrections']} automatic correction(s), "
            f"{history['requestedChanges']} requested change(s), "
            f"{history['providerRetries']} provider retry(ies), "
            f"{history['supersededFindings']} superseded finding(s)",
        ]
    )
    if history["passedAfterRetry"]:
        lines.append(
            "  passed only after an earlier failure on this digest: "
            + ", ".join(history["passedAfterRetry"])
        )
    delta = brief.get("delta")
    if delta:
        since = delta["sinceDecision"]
        lines.extend(
            [
                "",
                f"Since the last decision ({since['decision']} by {since['actorId']} on "
                f"{since['changeSetDigest']})",
            ]
        )
        if delta.get("unchanged"):
            lines.append("  the ChangeSet is the one that decision was bound to")
        else:
            for key in ("added", "changed", "removed"):
                if delta["files"][key]:
                    lines.append(f"  files {key}: {', '.join(delta['files'][key])}")
            for key, title in (("findingsResolved", "resolved"), ("findingsNew", "new")):
                for item in delta[key]:
                    lines.append(f"  {title}: {item['ruleId']} {item['location']}")
    provenance = brief.get("provenance")
    if provenance:
        lines.extend(
            [
                "",
                f"Provenance: {provenance['agentFiles']} file(s) written by an agent invocation, "
                f"{provenance['outOfBandFiles']} changed out of band",
                *(f"  out of band: {path}" for path in provenance["outOfBandPaths"]),
            ]
        )
    for report in brief.get("selfReports") or []:
        lines.extend(
            ["", f"Agent self-report (REPORTED, not verified) of {report['invocationId']}"]
        )
        lines.extend(f"  assumption: {item}" for item in report["assumptions"])
        lines.extend(
            f"  low confidence: {item.get('path') or '-'}: {item['description']}"
            for item in report["lowConfidenceAreas"]
        )
        lines.extend(
            f"  unrequested change: {item.get('path') or '-'}: {item['description']}"
            for item in report["unrequestedChanges"]
        )
        lines.extend(f"  problem: {item}" for item in report["problems"])
    lines.extend(_ladder_sections(brief))
    if brief.get("next"):
        lines.extend(["", "Next:", *(f"  {step}" for step in brief["next"])])
    if changed.get("diff"):
        lines.extend(["", "Diff", changed["diff"]])
    return lines


def _ladder_sections(brief: Mapping[str, Any]) -> list[str]:
    """Certification, preflight, deferred items, checklist, contract and interruptions (#55)."""
    return [
        *_certification_lines(brief.get("certification")),
        *_preflight_lines(brief.get("preflight")),
        *_deferred_lines(brief.get("deferred") or []),
        *_checklist_lines(brief.get("checklist") or []),
        *_contract_lines(brief.get("contract")),
        *_interruption_lines(brief.get("interruptions")),
    ]


def _certification_lines(certification: Mapping[str, Any] | None) -> list[str]:
    if not certification:
        return []
    lines = ["", f"Certification {certification['status']} ({certification['trigger']})"]
    for item in certification["criteria"]:
        declared = "" if item["declared"] else " (default level)"
        lines.append(
            f"  {item['status']:<14} {item['criterionId']}: requires {item['required']}"
            f"{declared}, reached {item['achieved'] or 'no rung'}"
        )
    return lines


def _preflight_lines(preflight: Mapping[str, Any] | None) -> list[str]:
    if not preflight:
        return []
    lines = ["", f"Preflight {preflight['status']}"]
    lines.extend(f"  - {short(reason, 100)}" for reason in preflight.get("reasons") or [])
    decision = preflight.get("decision")
    if decision:
        lines.append(
            f"  continued uncertified by {decision['actorId']}: {short(decision['rationale'], 70)}"
        )
    return lines


def _deferred_lines(deferred: Sequence[Mapping[str, Any]]) -> list[str]:
    if not deferred:
        return []
    return [
        "",
        "Deferred verification",
        *(
            f"  {item['status']:<8} {item['itemId']} ({short(item['where'], 40)}), expires "
            f"{item['expiresAt'][:19]}"
            for item in deferred
        ),
    ]


def _checklist_lines(checklist: Sequence[Mapping[str, Any]]) -> list[str]:
    if not checklist:
        return []
    lines = ["", "Checklist (ticked by the person who decides)"]
    for item in checklist:
        mark = "x" if item["checked"] else " "
        files = f" [{', '.join(item['attachments'])}]" if item["attachments"] else ""
        lines.append(f"  [{mark}] {item['itemId']}: {short(item['text'], 80)}{files}")
    return lines


def _contract_lines(contract: Mapping[str, Any] | None) -> list[str]:
    if not contract:
        return []
    state = "confirmed" if contract.get("confirmed") else "not confirmed"
    lines = ["", f"Operational contract ({state}, {contract['digest'][:19]})"]
    for item in contract.get("items", []):
        value = item["value"]
        shown = ", ".join(map(str, value)) if isinstance(value, list) else _scalar(value)
        lines.append(f"  {label(item['field']):<20} {short(shown, 70)} ({item['source']})")
    lines.extend(_assumption_lines(contract.get("assumptions")))
    return lines


def _assumption_lines(assumptions: Any) -> list[str]:
    """The points the agent review left open, recorded as assumptions (#79)."""
    return [
        f"  assumption {item.get('assumptionId')}: {short(str(item.get('question')), 90)}"
        for item in assumptions or []
        if isinstance(item, Mapping)
    ]


def _interruption_lines(interruptions: Mapping[str, Any] | None) -> list[str]:
    if not interruptions:
        return []
    budget = "over budget" if interruptions["overBudget"] else "within budget"
    return [
        "",
        f"Interruptions: {interruptions['count']} of a target of "
        f"{interruptions['target']} ({budget})",
        *(
            f"  stop: {item['condition']} - {short(item.get('detail') or '', 70)}"
            for item in interruptions.get("stops") or []
        ),
    ]


def _exceptions(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["No exceptions recorded."]
    rows = [
        [
            str(item["exceptionId"]),
            str(item["status"]),
            str(item["expiresAt"])[:19],
            str(item["actorId"]),
            str(len(item["scope"])),
            str(len(item["appliedIn"])),
            short(item.get("followUp") or "-", 30),
        ]
        for item in items
    ]
    return table(rows, ["EXCEPTION", "STATUS", "EXPIRES", "BY", "SCOPES", "USED IN", "FOLLOW-UP"])


def _inbox(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["Nothing waits for a person."]
    rows = []
    for item in items:
        if item["kind"] == "decision":
            detail = f"gate {item['gateStatus']}, {item['blockingFindings']} blocking finding(s)"
        elif item["kind"] == "deferred":
            detail = f"{item['itemId']} ({short(item['where'], 30)}) {item['status']}" + (
                f", {item['warning']}" if item.get("warning") else ""
            )
        elif item["kind"] == "preflight":
            detail = "preflight UNAVAILABLE: continue uncertified or fix the environment"
        else:
            detail = f"{item['questions']} question(s)"
        rows.append(
            [
                str(item["kind"]),
                str(item["executionId"]),
                short(item["taskTitle"], 36),
                detail,
                f"{item['waitingHours']} h",
                str(item["next"]),
            ]
        )
    return table(rows, ["WAITS FOR", "RUN", "TASK", "STATE", "WAITING", "NEXT"])


def _health(value: Mapping[str, Any]) -> list[str]:
    lines = [
        f"{value['runs']} run(s), {value['correctedRuns']} with corrections; outcomes: "
        + ", ".join(f"{key.lower()} {count}" for key, count in value["outcomes"].items()),
        "",
    ]
    rows = [
        [
            str(item["ruleId"]),
            str(item["fired"]),
            str(item["runs"]),
            str(item["blockedGates"]),
            str(item["excepted"]),
            str(item["correctedRuns"]),
            str(item["rejectedRuns"]),
            str(item["runsWithLaterOutcomes"]),
            item["signal"] or "-",
        ]
        for item in value["rules"]
    ]
    lines.extend(
        table(
            rows,
            [
                "RULE",
                "FIRED",
                "RUNS",
                "BLOCKED",
                "EXCEPTED",
                "CORRECTED",
                "REJECTED",
                "OUTCOMES",
                "SIGNAL",
            ],
        )
        if rows
        else ["No findings recorded."]
    )
    lines.extend(["", "Validators:"])
    lines.extend(
        f"  {item['validatorId']} ({'mandatory' if item['mandatory'] else 'optional'}): "
        f"{item['notPassed']} of {item['results']} result(s) not passed in {item['runs']} run(s)"
        for item in value["validators"]
    )
    return lines


Renderer = Callable[[Any], list[str]]

RENDERERS: dict[str, Renderer] = {
    "runs": _runs,
    "tasks": _tasks,
    "findings": _findings,
    "execution": _execution,
    "status": _status,
    "decision": _decision,
    "doctor": _doctor,
    "init": _init,
    "review": _review,
    "exceptions": _exceptions,
    "inbox": _inbox,
    "health": _health,
}


def register(kind: str, renderer: Renderer) -> None:
    RENDERERS[kind] = renderer


def render_human(value: object, kind: str | None = None) -> str:
    renderer = RENDERERS.get(kind or "")
    lines = renderer(value) if renderer else generic(value)
    return "\n".join(lines)
