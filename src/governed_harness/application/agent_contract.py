"""Application helpers of the agent contract (wave 11, #97): the schemas, the validation of a
contract document, the registry of script contracts and the evidence of what an agent call
received and returned."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

from governed_harness.agents.contract import (
    RESULT_KEYS,
    AgentCallRequestContract,
    AgentCallResponse,
    request_digest,
    validate_request_contract,
    validate_response_contract,
)
from governed_harness.agents.script_contracts import ScriptContract, registry
from governed_harness.configuration.agent_contract import AgentContractConfig
from governed_harness.domain.errors import ConfigurationError, NotFoundError

if TYPE_CHECKING:
    from governed_harness.orchestration.engine import EngineServices

SCHEMAS: Final[dict[str, type[BaseModel]]] = {
    "request": AgentCallRequestContract,
    "response": AgentCallResponse,
    "script": ScriptContract,
}
DOCUMENT_KINDS: Final[tuple[str, ...]] = ("request", "response", "script", "output")
RECORD_EVENT: Final = "agent.call.recorded"
ITERATION_EVENT: Final = "agent.iteration"


def contract_schema(name: str) -> dict[str, Any]:
    """The JSON Schema of the ``request``, ``response`` or ``script`` contract."""
    model = SCHEMAS.get(name)
    if model is None:
        raise ConfigurationError(f"unknown contract {name!r}; known: {', '.join(SCHEMAS)}")
    return model.model_json_schema(by_alias=True, ref_template="#/$defs/{model}")


def validate_document(name: str, path: Path, call_kind: str | None = None) -> dict[str, Any]:
    """Validate a JSON file against a contract. ``output`` is the JSON an agent prints, checked
    against the limits of ``call_kind``. A violation is returned as a structured result."""
    if name not in DOCUMENT_KINDS:
        raise ConfigurationError(f"unknown document {name!r}; known: {', '.join(DOCUMENT_KINDS)}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"cannot read {path} as JSON: {error}") from error
    try:
        if name == "output":
            problems = output_problems(value, call_kind or "")
            if problems:
                raise ValueError("; ".join(problems))
        elif name == "request":
            validate_request_contract(value)
        elif name == "response":
            validate_response_contract(value)
        else:
            ScriptContract.model_validate(value)
    except ValueError as error:
        return {"document": name, "valid": False, "violation": str(error), "file": str(path)}
    return {"document": name, "valid": True, "violation": None, "file": str(path)}


def output_problems(value: Any, call_kind: str) -> list[str]:
    """What the JSON an agent printed does against the response contract of its call kind."""
    from governed_harness.agents.contract import check_answer

    if call_kind not in RESULT_KEYS:
        raise ConfigurationError(f"--kind must be one of: {', '.join(RESULT_KEYS)}")
    if not isinstance(value, dict):
        return ["the output must be one JSON object"]
    problems: list[str] = []
    if value.get("status") not in {"PASSED", "FAILED", "BLOCKED"}:
        problems.append("status must be PASSED, FAILED or BLOCKED")
    if not isinstance(value.get("summary"), str):
        problems.append("summary must be a string")
    result = value.get("result")
    if result is not None and not isinstance(result, dict):
        problems.append("result must be an object")
        return problems
    return problems + check_answer(call_kind, result, AgentContractConfig())


def script_contracts(path: Path, script_id: str | None = None) -> dict[str, Any]:
    """Every contract an agent may use in the project at ``path`` (``harness check``, the
    validator commands of the gate, the tools of the embedded MCP server), or one by id."""
    from governed_harness.configuration import ConfigurationResolver
    from governed_harness.embedded.mcp_server import TOOLS
    from governed_harness.orchestration.gate_contract import gate_definitions

    try:
        definitions = gate_definitions(ConfigurationResolver().resolve(path))
    except ConfigurationError:
        definitions = []
    contracts = registry(definitions, TOOLS)
    if script_id is not None:
        if script_id not in contracts:
            raise NotFoundError(f"no script contract {script_id!r}; known: {', '.join(contracts)}")
        return contracts[script_id].model_dump(mode="json", by_alias=True)
    return {
        "contractVersion": "1.0",
        "count": len(contracts),
        "scripts": [
            {"id": item.script_id, "kind": item.kind, "summary": item.summary}
            for item in contracts.values()
        ],
    }


def agent_calls(
    services: EngineServices, execution_id: str, kind: str | None, iteration: int | None
) -> dict[str, Any]:
    """The agent calls of a run (``agent.call.recorded`` events), the iterations each agent
    used, and, for one call (``kind`` and ``iteration``), exactly what was sent and received."""
    events = services.events.list(execution_id)
    recorded = [item.payload for item in events if item.event_type == RECORD_EVENT]
    begun: dict[str, int] = {}
    cap = 0
    for item in events:
        if item.event_type == ITERATION_EVENT:
            name = str(item.payload.get("callKind"))
            begun[name] = begun.get(name, 0) + 1
            cap = int(item.payload.get("max", cap))
    summary = {
        "executionId": execution_id,
        "iterations": {name: {"used": count, "max": cap} for name, count in sorted(begun.items())},
        "calls": [
            {
                key: payload.get(key)
                for key in (
                    "callKind",
                    "iteration",
                    "provider",
                    "size",
                    "finalStatus",
                    "limitReached",
                    "sentBytes",
                    "receivedBytes",
                    "evidenceRef",
                )
            }
            for payload in recorded
            if kind is None or payload.get("callKind") == kind
        ],
    }
    if kind is None:
        return summary
    return {**summary, "call": _call_detail(services, recorded, kind, iteration)}


def _call_detail(
    services: EngineServices, recorded: list[dict[str, Any]], kind: str, iteration: int | None
) -> dict[str, Any]:
    matching = [
        item
        for item in recorded
        if item.get("callKind") == kind
        and (iteration is None or item.get("iteration") == iteration)
    ]
    if not matching:
        raise NotFoundError(f"no recorded {kind} call" + (f" {iteration}" if iteration else ""))
    payload = matching[-1]
    record = json.loads(services.artifacts.get(str(payload["evidenceRef"])))
    sent = record["sent"]
    request_ref = sent.get("requestRef")
    request = json.loads(services.artifacts.get(str(request_ref))) if request_ref else None
    return {
        "record": record,
        "request": request,
        "parentReceived": record["received"],
        "verification": _verification(sent, request),
    }


def _verification(sent: dict[str, Any], request: dict[str, Any] | None) -> dict[str, Any]:
    """Whether the stored request is the one the provider got and carries nothing beyond what
    the contract lists (``None`` where the request was not stored)."""
    if request is None:
        return {
            "requestStored": False,
            "note": "The request of this kind is not stored whole; its digest is the prompt "
            "digest of the invocation.",
        }
    contract = request.get("agentContract") or {}
    keys = sorted(request)
    return {
        "requestStored": True,
        "digestMatches": request_digest(request) == sent.get("digest"),
        "keysMatchRecord": keys == sent.get("topLevelKeys"),
        "keysMatchContract": keys == sorted(contract.get("allowedContext") or []),
        "extraKeys": sorted(set(keys) - set(contract.get("allowedContext") or [])),
    }
