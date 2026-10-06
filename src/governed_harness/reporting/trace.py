from __future__ import annotations

import json
from typing import Any

from governed_harness.domain.models import (
    Execution,
    Finding,
    GateEvaluation,
    HumanDecision,
    PhaseExecution,
    Retrospective,
    Task,
    ValidationResult,
)
from governed_harness.events import StoredEvent
from governed_harness.reporting.fingerprint import FINGERPRINT_VERSION, finding_fingerprint
from governed_harness.telemetry import MetricValue


class TraceReporter:
    def as_dict(
        self,
        *,
        execution: Execution,
        task: Task,
        phases: list[PhaseExecution],
        validations: list[ValidationResult],
        findings: list[Finding],
        gate: GateEvaluation | None,
        decision: HumanDecision | None,
        events: list[StoredEvent],
        metrics: dict[str, MetricValue],
        retrospective: Retrospective | None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "execution": execution.model_dump(mode="json", by_alias=True),
            "task": task.model_dump(mode="json", by_alias=True),
            "phases": [item.model_dump(mode="json", by_alias=True) for item in phases],
            "validations": [item.model_dump(mode="json", by_alias=True) for item in validations],
            "findings": [item.model_dump(mode="json", by_alias=True) for item in findings],
            "gate": gate.model_dump(mode="json", by_alias=True) if gate else None,
            "humanDecision": decision.model_dump(mode="json", by_alias=True) if decision else None,
            "events": [event.as_dict() for event in events],
            "metrics": {key: value.as_dict() for key, value in metrics.items()},
            "retrospective": retrospective.model_dump(mode="json", by_alias=True)
            if retrospective
            else None,
        }

    def render_json(self, **kwargs: Any) -> bytes:
        return json.dumps(
            self.as_dict(**kwargs), indent=2, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")

    def render_markdown(self, **kwargs: Any) -> bytes:
        data = self.as_dict(**kwargs)
        execution = data["execution"]
        task = data["task"]
        lines = [
            f"# Execution trace `{execution['executionId']}`",
            "",
            f"- **Task:** {task['title']} (`{task['taskId']}`)",
            f"- **Status:** `{execution['status']}`",
            f"- **Current phase:** `{execution['currentPhase']}`",
            f"- **ChangeSet digest:** `{execution.get('changeSetDigest') or 'not available'}`",
            f"- **Configuration digest:** `{execution['configurationDigest']}`",
            f"- **Workflow digest:** `{execution['workflowDigest']}`",
            f"- **Policy digest:** `{execution['policyDigest']}`",
            "",
            "## Normative phases",
            "",
            "| Phase | Attempt | Status | Summary |",
            "|---|---:|---|---|",
        ]
        for phase in data["phases"]:
            summary = str(phase.get("summary", "")).replace("|", "\\|")
            lines.append(
                f"| `{phase['phaseId']}` | {phase['attempt']} | `{phase['status']}` | {summary} |"
            )
        lines.extend(
            [
                "",
                "## Validation results",
                "",
                "| Validator | Mandatory | Status | Kind | Summary |",
                "|---|:---:|---|---|---|",
            ]
        )
        for result in data["validations"]:
            summary = str(result["summary"]).replace("|", "\\|")
            lines.append(
                f"| `{result['validatorId']}` | {result['mandatory']} | `{result['status']}` | `{result['kind']}` | {summary} |"
            )
        lines.extend(["", "## Findings", ""])
        if data["findings"]:
            for finding in data["findings"]:
                location = finding.get("location") or {}
                where = location.get("path") or "execution"
                if location.get("startLine"):
                    where += f":{location['startLine']}"
                lines.append(
                    f"- **{finding['severity']}** `{finding['ruleId']}` at `{where}`: {finding['message']}"
                )
        else:
            lines.append("No findings were recorded.")
        lines.extend(["", "## Gate and human decision", ""])
        if data["gate"]:
            lines.append(
                f"Automatic gate `{data['gate']['gateEvaluationId']}`: **{data['gate']['status']}**."
            )
            lines.append(
                "Reasons: " + ", ".join(f"`{item}`" for item in data["gate"]["reasonCodes"])
            )
        else:
            lines.append("No gate evaluation has been recorded.")
        if data["humanDecision"]:
            decision = data["humanDecision"]
            lines.append(
                f"Human decision: **{decision['decision']}** by `{decision['actor']['actorId']}` for `{decision['changeSetDigest']}`."
            )
        else:
            lines.append("No human decision has been recorded.")
        lines.extend(
            [
                "",
                "## Operational metrics",
                "",
                "| Metric | Value | Unit | Quality |",
                "|---|---:|---|---|",
            ]
        )
        for key, metric in sorted(data["metrics"].items()):
            value = "N/A" if metric["value"] is None else metric["value"]
            lines.append(f"| `{key}` | {value} | {metric['unit']} | `{metric['quality']}` |")
        lines.extend(["", "## Retrospective", ""])
        if data["retrospective"]:
            for recommendation in data["retrospective"]["recommendations"]:
                lines.append(
                    f"- **{recommendation['category']}** ({recommendation['confidence']:.2f}): {recommendation['statement']}"
                )
            lines.append("")
            lines.append("Recommendations require human review and were not applied automatically.")
        else:
            lines.append("No retrospective has been generated yet.")
        lines.extend(["", "## Event chain", ""])
        for event in data["events"]:
            lines.append(f"{event['sequence']}. `{event['eventType']}` — `{event['eventDigest']}`")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def render_sarif(self, findings: list[Finding]) -> bytes:
        rules: dict[str, dict[str, Any]] = {}
        results = []
        level_map = {
            "INFO": "note",
            "LOW": "note",
            "MEDIUM": "warning",
            "HIGH": "error",
            "CRITICAL": "error",
        }
        for finding in findings:
            rules.setdefault(
                finding.rule_id,
                {
                    "id": finding.rule_id,
                    "name": finding.rule_id,
                    "shortDescription": {"text": finding.category},
                    "help": {"text": finding.recommendation or finding.message},
                },
            )
            result: dict[str, Any] = {
                "ruleId": finding.rule_id,
                "level": level_map[finding.severity],
                "message": {"text": finding.message},
                # Stable across attempts and runs (rule, validator, path and message without
                # positions), so code-scanning tools can track one problem over time.
                "partialFingerprints": {FINGERPRINT_VERSION: finding_fingerprint(finding)},
                "properties": {
                    "findingId": finding.finding_id,
                    "executionId": finding.execution_id,
                    "validatorId": finding.validator_id,
                    "severity": finding.severity.value,
                    "category": finding.category,
                },
            }
            if finding.location and finding.location.path:
                result["locations"] = [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": finding.location.path},
                            "region": {
                                "startLine": finding.location.start_line or 1,
                                "endLine": finding.location.end_line
                                or finding.location.start_line
                                or 1,
                            },
                        }
                    }
                ]
            results.append(result)
        sarif = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {"name": "Governed Agent Harness", "rules": list(rules.values())}
                    },
                    "results": results,
                }
            ],
        }
        return json.dumps(sarif, indent=2, sort_keys=True).encode("utf-8")
