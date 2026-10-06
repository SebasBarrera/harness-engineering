"""Architecture of the project: survey, options, decision and layer rules (#56).

* **Existing project** (``architecture.mode: agent``): DISCOVERY makes one read-only
  ``architecture`` call (``mode: survey``) per project. The answer (the patterns in use and the
  layer rules they imply) is cached in ``.harness/architecture.md`` and
  ``.harness/architecture.json``, keyed by the digest of the source directory layout, and reused
  by every later run without another call. When it infers layer rules, DISCOVERY waits until a
  person approves or rejects them, bound to their digest (``harness architecture decide``). The
  survey runs again only on demand (``harness architecture refresh``) or, under
  ``refresh: auto``, when the layout changed materially (fewer than 80 % of the source
  directories in common).
* **New project**: INTENT makes one ``architecture`` call (``mode: advise``) for options with
  trade-offs (DDD, hexagonal, clean, layered, modular monolith, microservices, MVVM, MVI). A
  person chooses one (``harness architecture decide --option``), bound to the digest of the
  options; the choice is recorded as an ADR (an artifact with its digest, and
  ``.harness/adr/ADR-0001-architecture.md``) and its layer rules become the project's.
* **Configured layers** (``architecture.layers`` and ``allow``) are the rules as written; no call.

The rules are enforced in VERIFICATION by the layer check (``harness.layers``, rule
``architecture.layer-violation``), the #40 forbidden-dependency check generalized to every
language the standards packs know."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.checks.layers import LayerRules
from governed_harness.checks.principles import is_source
from governed_harness.configuration.engineering import ARCHITECTURE_STYLES, ArchitectureSettings
from governed_harness.domain.enums import ActorType, DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.errors import NotFoundError, PolicyViolationError
from governed_harness.domain.models import Actor, Execution, PhaseExecution, Task, utc_now
from governed_harness.evidence.hashing import sha256_json
from governed_harness.intake.project_kind import ProjectKind, detect_project_kind
from governed_harness.orchestration.engine_types import PhaseOutcome
from governed_harness.runtime.workspace import DEFAULT_EXCLUDES

if TYPE_CHECKING:
    from governed_harness.orchestration.hosts import ResultsHost

STATE_FILE = "architecture.json"
SURVEY_FILE = "architecture.md"
ADR_FILE = "adr/ADR-0001-architecture.md"
MATERIAL_CHANGE = 0.8
"""Under ``refresh: auto`` the survey runs again when fewer than this share of the source
directories is common to the cached layout and the current one (Jaccard similarity)."""
_MAX_LAYOUT = 400
_MAX_LAYERS = 20


# ----- layout ---------------------------------------------------------------------------------
def source_layout(workspace: Path, depth: int = 4) -> list[str]:
    """The directories (up to ``depth`` levels) that hold source files, sorted."""
    root = workspace.resolve()
    found: set[str] = set()
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack and len(found) < _MAX_LAYOUT:
        directory, level = stack.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        visible = [entry for entry in entries if not _hidden(entry)]
        if level < depth:
            stack.extend((entry, level + 1) for entry in visible if entry.is_dir())
        if any(entry.is_file() and is_source(entry.name) for entry in visible):
            found.add(directory.relative_to(root).as_posix() or ".")
    return sorted(found)


def _hidden(entry: Path) -> bool:
    """An excluded, hidden or symbolic-link entry the layout does not look into."""
    return entry.name in DEFAULT_EXCLUDES or entry.name.startswith(".") or entry.is_symlink()


def similarity(left: list[str], right: list[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


# ----- validation of the agent's answers ----------------------------------------------------------
def _layer(item: Any) -> dict[str, Any]:
    """One layer of an answer: a normalised name and relative path globs or modules."""
    if not isinstance(item, dict) or not str(item.get("name") or "").strip():
        raise ValueError("every layer needs a name")
    name = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in str(item["name"]).lower()
    )[:40]
    paths = [str(path) for path in item.get("paths") or [] if str(path).strip()]
    modules = [str(module) for module in item.get("modules") or [] if str(module).strip()]
    if any(path.startswith("/") or ".." in path.split("/") for path in paths):
        raise ValueError(f"layer {name}: paths must be relative globs")
    if not paths and not modules:
        raise ValueError(f"layer {name} needs paths or modules")
    return {"name": name, "paths": paths, "modules": modules}


def _allow(raw: Any, names: set[str]) -> dict[str, list[str]]:
    """The allowed dependencies of an answer, between the layers it names."""
    allow: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return allow
    for key, targets in raw.items():
        source = str(key).lower()
        listed = [str(item).lower() for item in targets or []] if isinstance(targets, list) else []
        unknown = [item for item in (source, *listed) if item not in names]
        if unknown:
            raise ValueError(f"allow names unknown layer(s): {', '.join(sorted(set(unknown)))}")
        allow[source] = listed
    return allow


def _layers(value: Any) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    raw = value.get("layers") if isinstance(value, dict) else None
    layers = [_layer(item) for item in (raw if isinstance(raw, list) else [])]
    if len(layers) > _MAX_LAYERS:
        raise ValueError(f"at most {_MAX_LAYERS} layers")
    allow_raw = value.get("allow") if isinstance(value, dict) else None
    return layers, _allow(allow_raw, {item["name"] for item in layers})


def validate_survey(result: dict[str, Any]) -> dict[str, Any]:
    style = str(result.get("style") or "custom").strip().lower()
    if style not in ARCHITECTURE_STYLES:
        style = "custom"
    summary = result.get("summary")
    if not isinstance(summary, str):
        raise ValueError("the survey needs a 'summary' text")
    layers, allow = _layers(result)
    return {"style": style, "summary": summary.strip()[:20_000], "layers": layers, "allow": allow}


def validate_options(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("options")
    if not isinstance(raw, list):
        raise ValueError("the advice needs an 'options' list")
    options: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:6], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"option {index} is not an object")
        style = str(item.get("style") or "").strip().lower()
        if style not in ARCHITECTURE_STYLES:
            raise ValueError(f"option {index} has an unknown style {item.get('style')!r}")
        layers, allow = _layers(item)
        options.append(
            {
                "id": str(item.get("id") or f"option-{index}")[:40],
                "style": style,
                "title": str(item.get("title") or style)[:200],
                "benefits": [str(text)[:400] for text in item.get("benefits") or []][:10],
                "costs": [str(text)[:400] for text in item.get("costs") or []][:10],
                "fit": str(item.get("fit") or "")[:1000],
                "recommended": bool(item.get("recommended")),
                "layers": layers,
                "allow": allow,
            }
        )
    ids = [item["id"] for item in options]
    if len(ids) != len(set(ids)):
        raise ValueError("option ids must be unique")
    return options


def render_adr(option: dict[str, Any], *, decided_by: str, rationale: str, digest: str) -> str:
    """The architecture decision record of a chosen option (Markdown)."""
    lines = [
        f"# ADR-0001: {option['title']}",
        "",
        "Status: accepted",
        f"Decided by: {decided_by}",
        f"Options digest: {digest}",
        "",
        "## Decision",
        "",
        f"The project follows the {option['style']} style ({option['id']}). {rationale}",
        "",
        "## Consequences",
        "",
        *[f"- Benefit: {item}" for item in option["benefits"]],
        *[f"- Cost: {item}" for item in option["costs"]],
        "",
        "## Layer rules",
        "",
    ]
    for layer in option["layers"]:
        allowed = ", ".join(option["allow"].get(layer["name"], [])) or "no other layer"
        where = ", ".join([*layer["paths"], *layer["modules"]])
        lines.append(f"- {layer['name']} ({where}) may depend on: {allowed}")
    return "\n".join(lines).rstrip() + "\n"


# ----- the flow ---------------------------------------------------------------------------------------
class ArchitectureFlow:
    def __init__(self, results: ResultsHost) -> None:
        self.results = results

    @property
    def config(self) -> ArchitectureSettings | None:
        return self.results.project.architecture

    @property
    def harness_dir(self) -> Path:
        return self.results.s.paths.harness_dir

    def state(self) -> dict[str, Any] | None:
        return read_state(self.harness_dir)

    def _write(self, value: dict[str, Any]) -> None:
        write_state(self.harness_dir, value)

    def kind(self) -> ProjectKind:
        return detect_project_kind(self.results.s.paths.workspace)

    # ----- the rules in force ------------------------------------------------------------------
    def rules(self) -> LayerRules | None:
        return effective_rules(self.config, self.harness_dir)

    def request_extra(self) -> dict[str, Any] | None:
        rules = self.rules()
        if rules is None or not rules.layers:
            return None
        state = self.state() or {}
        return {
            "style": (self.config.style if self.config and self.config.style else None)
            or state.get("style"),
            **rules.as_dict(),
        }

    # ----- INTENT: options for a new project -------------------------------------------------
    def advise(
        self, execution: Execution, phase: PhaseExecution, task: Task
    ) -> PhaseOutcome | None:
        config = self.config
        if config is None or not config.agent_enabled or config.layers or config.style:
            return None
        state = self.state()
        if state is not None and state.get("status") in {"APPROVED", "REJECTED", "NONE"}:
            return None
        if state is not None and state.get("status") == "OPTIONS":
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"{len(state['options'])} architecture option(s) wait for a person: harness "
                f"architecture decide --run {execution.execution_id} --option <id> --digest "
                f"{state['digest']}",
                (state["ref"],),
            )
        kind = self.kind()
        if not kind.new:
            return None
        outcome = self.results.call_agent(
            execution,
            phase,
            "architecture",
            {"mode": "advise", "styles": list(ARCHITECTURE_STYLES), "projectKind": kind.as_dict()},
            task=task,
            instruction_values={"mode": "advise", "styles": ", ".join(ARCHITECTURE_STYLES)},
            validate=validate_options,
        )
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The architecture call did not answer ({outcome.status}): {outcome.summary}",
                outcome.evidence_refs,
            )
        try:
            options = validate_options(outcome.result)
        except ValueError as error:
            return self._malformed(execution, error, outcome.evidence_refs)
        digest = sha256_json({"options": options})
        record = {
            "status": "OPTIONS" if options else "NONE",
            "mode": "advise",
            "digest": digest,
            "options": options,
            "projectKind": kind.as_dict(),
            "invocationId": outcome.invocation_id,
            "createdAt": utc_now().isoformat(),
        }
        ref = self.results.record_json(
            execution,
            PhaseId.INTENT,
            record,
            kind="architecture-options",
            summary=f"Architecture options for a new project: {len(options)}",
        )
        self._write({**record, "ref": ref, "executionId": execution.execution_id})
        self.results.s.events.append(
            execution.execution_id,
            "architecture.options.proposed",
            {"digest": digest, "options": [item["id"] for item in options], "evidenceRef": ref},
            phase_execution_id=phase.phase_execution_id,
        )
        if not options:
            return None
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"{len(options)} architecture option(s) wait for a person: harness architecture "
            f"decide --run {execution.execution_id} --option <id> --digest {digest}",
            (ref,),
        )

    # ----- DISCOVERY: survey of an existing project ------------------------------------------
    def survey(
        self, execution: Execution, phase: PhaseExecution, task: Task
    ) -> PhaseOutcome | None:
        config = self.config
        if config is None or not config.agent_enabled or config.layers:
            return None
        layout = source_layout(self.results.s.paths.workspace)
        state = self.state()
        if state is not None and state.get("mode") == "advise":
            return None  # a new project: the ADR decides
        if state is not None and state.get("status") == "PROPOSED":
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                "The inferred architecture rules wait for a person: harness architecture decide "
                f"--run {execution.execution_id} --decision APPROVE --digest {state['digest']}",
                (state["ref"],),
            )
        if state is not None and self._reused(execution, phase, config, state, layout):
            return None
        if self.kind().new:
            return None
        outcome = self.results.call_agent(
            execution,
            phase,
            "architecture",
            {"mode": "survey", "layout": layout, "styles": list(ARCHITECTURE_STYLES)},
            task=task,
            instruction_values={"mode": "survey", "styles": ", ".join(ARCHITECTURE_STYLES)},
            validate=validate_survey,
        )
        if outcome.status is not ResultStatus.PASSED or outcome.result is None:
            return PhaseOutcome(
                ResultStatus.BLOCKED,
                f"The architecture survey did not answer ({outcome.status}): {outcome.summary}",
                outcome.evidence_refs,
            )
        try:
            survey = validate_survey(outcome.result)
        except ValueError as error:
            return self._malformed(execution, error, outcome.evidence_refs)
        rules = {"layers": survey["layers"], "allow": survey["allow"]}
        digest = sha256_json({"style": survey["style"], **rules})
        status = "PROPOSED" if survey["layers"] else "NONE"
        record = {
            "status": status,
            "mode": "survey",
            "style": survey["style"],
            "digest": digest,
            "rules": rules,
            "layout": layout,
            "layoutDigest": sha256_json(layout),
            "invocationId": outcome.invocation_id,
            "createdAt": utc_now().isoformat(),
        }
        ref = self.results.record_json(
            execution,
            PhaseId.DISCOVERY,
            {**record, "summary": survey["summary"]},
            kind="architecture-survey",
            summary=f"Architecture survey: {survey['style']}, {len(survey['layers'])} layer(s)",
        )
        (self.harness_dir / SURVEY_FILE).write_text(
            f"# Architecture of the project ({survey['style']})\n\n{survey['summary']}\n",
            encoding="utf-8",
        )
        self._write({**record, "ref": ref, "executionId": execution.execution_id})
        self.results.s.events.append(
            execution.execution_id,
            "architecture.survey.completed",
            {
                "digest": digest,
                "status": status,
                "layers": len(survey["layers"]),
                "evidenceRef": ref,
            },
            phase_execution_id=phase.phase_execution_id,
        )
        if status == "NONE":
            return None
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"The survey inferred {len(survey['layers'])} layer(s): a person approves the rules "
            f"with harness architecture decide --run {execution.execution_id} --decision "
            f"APPROVE --digest {digest}",
            (ref,),
        )

    def _reused(
        self,
        execution: Execution,
        phase: PhaseExecution,
        config: ArchitectureSettings,
        state: dict[str, Any],
        layout: list[str],
    ) -> bool:
        """Whether the recorded survey still holds (recorded as reused): it is not stale and,
        under ``refresh: auto``, the source layout did not change materially."""
        if state.get("stale"):
            return False
        refresh = (config.refresh or "manual") == "auto"
        if refresh and similarity(state.get("layout") or [], layout) < MATERIAL_CHANGE:
            return False
        self.results.s.events.append(
            execution.execution_id,
            "architecture.survey.reused",
            {
                "digest": state.get("digest"),
                "status": state.get("status"),
                "layoutSimilarity": round(similarity(state.get("layout") or [], layout), 3),
            },
            phase_execution_id=phase.phase_execution_id,
        )
        return True

    def _malformed(
        self, execution: Execution, error: ValueError, refs: tuple[str, ...]
    ) -> PhaseOutcome:
        from governed_harness.domain.enums import FindingSeverity

        self.results.record_finding(
            execution,
            validator_id="harness.architecture-call",
            rule_id="architecture.malformed",
            category="agent-protocol",
            severity=FindingSeverity.HIGH,
            message=f"The architecture call returned a malformed result: {error}",
            evidence_refs=refs,
        )
        return PhaseOutcome(
            ResultStatus.BLOCKED,
            f"The architecture call returned a malformed result: {error}",
            refs,
        )

    # ----- decision (a person) ---------------------------------------------------------------
    def decide(
        self,
        execution: Execution,
        *,
        actor: Actor,
        digest: str,
        rationale: str,
        decision: DecisionKind | None = None,
        option: str | None = None,
    ) -> dict[str, Any]:
        if actor.actor_type is not ActorType.HUMAN:
            raise PolicyViolationError("only a person decides the architecture")
        state = self.state()
        if state is None or state.get("status") not in {"OPTIONS", "PROPOSED"}:
            raise NotFoundError("no architecture options or inferred rules wait for a decision")
        if state["digest"] != digest:
            raise PolicyViolationError("the digest does not match the architecture proposal")
        if not rationale.strip():
            raise PolicyViolationError("a rationale is required")
        decided = {
            "decidedBy": actor.actor_id,
            "rationale": rationale,
            "decidedAt": utc_now().isoformat(),
            "decisionExecutionId": execution.execution_id,
        }
        if state["status"] == "OPTIONS":
            chosen = next((item for item in state["options"] if item["id"] == option), None)
            if option is None or chosen is None:
                known = ", ".join(item["id"] for item in state["options"])
                raise PolicyViolationError(f"choose one option with --option ({known})")
            adr = render_adr(chosen, decided_by=actor.actor_id, rationale=rationale, digest=digest)
            adr_ref = self.results.s.artifacts.put(
                adr.encode("utf-8"),
                media_type="text/markdown",
                metadata={"kind": "architecture-adr", "executionId": execution.execution_id},
            )
            target = self.harness_dir / ADR_FILE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(adr, encoding="utf-8")
            updated = {
                **state,
                **decided,
                "status": "APPROVED",
                "style": chosen["style"],
                "option": chosen["id"],
                "rules": {"layers": chosen["layers"], "allow": chosen["allow"]},
                "adrRef": adr_ref.uri,
                "adrDigest": adr_ref.digest,
            }
            event = {
                "decision": "CHOOSE",
                "option": chosen["id"],
                "digest": digest,
                "adrRef": adr_ref.uri,
                "adrDigest": adr_ref.digest,
            }
        else:
            if decision not in {DecisionKind.APPROVE, DecisionKind.REJECT}:
                raise PolicyViolationError("inferred rules are approved or rejected")
            status = "APPROVED" if decision is DecisionKind.APPROVE else "REJECTED"
            updated = {**state, **decided, "status": status}
            event = {"decision": decision.value, "digest": digest}
        self._write(updated)
        self.results.s.events.append(
            execution.execution_id, "architecture.decided", event, actor=actor
        )
        return updated

    def refresh(self) -> dict[str, Any]:
        state = self.state()
        if state is None:
            return {"status": "NONE", "note": "no survey cached; the next run surveys"}
        self._write({**state, "stale": True})
        return {"status": "STALE", "note": "the next run surveys the project again"}


# ----- state file -----------------------------------------------------------------------------------
def read_state(harness_dir: Path) -> dict[str, Any] | None:
    path = harness_dir / STATE_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_state(harness_dir: Path, value: dict[str, Any]) -> None:
    path = harness_dir / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def effective_rules(config: ArchitectureSettings | None, harness_dir: Path) -> LayerRules | None:
    """The layer rules in force: the configured layers, else the rules a person approved (from
    a survey or an ADR). ``None`` without an ``architecture`` section."""
    if config is None:
        return None
    if config.layers:
        return LayerRules.from_mapping(
            [item.model_dump(mode="json", by_alias=True) for item in config.layers],
            {key: list(value) for key, value in (config.allow or {}).items()},
        )
    state = read_state(harness_dir)
    if state is None or state.get("status") != "APPROVED":
        return None
    rules = state.get("rules") or {}
    return LayerRules.from_mapping(rules.get("layers") or [], rules.get("allow") or {})


__all__ = [
    "ADR_FILE",
    "MATERIAL_CHANGE",
    "STATE_FILE",
    "SURVEY_FILE",
    "ArchitectureFlow",
    "effective_rules",
    "read_state",
    "render_adr",
    "similarity",
    "source_layout",
    "validate_options",
    "validate_survey",
    "write_state",
]
