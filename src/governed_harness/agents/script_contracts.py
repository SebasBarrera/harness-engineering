"""Scripts as black boxes (wave 11, #97): the contract of every command an agent may call.

A script that the harness lets an agent call has a contract: how to invoke it, the inputs it
takes, the outputs it produces and the errors it can return. The agent is told to use only that
contract: it does not read the script's source, it does not spend tokens working out how it
works, and it uses the answer it gets. The registry covers the commands of the harness an agent
runs (``harness check``), the validator commands of the gate and the tools of the embedded MCP
server. Pure: it reads nothing and runs nothing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SCRIPT_CONTRACT_VERSION: Final = "1.0"
ScriptKind = Literal["harness-command", "validator", "mcp-tool"]
ValueType = Literal["string", "integer", "boolean", "path", "array", "object"]

BLACK_BOX_NOTICE: Final = (
    "Scripts and commands are black boxes: use only the contract listed in "
    "agentContract.scripts (invocation, inputs, outputs, errors). Do not read their source "
    "code, do not work out how they work inside, run them and use only the answer they give."
)
"""The sentence added to the instructions of every call of a run with ``agentContract``."""


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, frozen=True, alias_generator=to_camel
    )


class ScriptInput(_Contract):
    name: str = Field(min_length=1)
    value_type: ValueType
    required: bool
    description: str


class ScriptOutput(_Contract):
    name: str = Field(min_length=1)
    channel: Literal["stdout", "exit-code", "file", "result"]
    format: str
    description: str


class ScriptError(_Contract):
    code: str = Field(min_length=1)
    """An exit code or an error name."""
    meaning: str
    agent_action: str


class ScriptContract(_Contract):
    """The contract of one script (schema ``script-contract.schema.json``)."""

    contract_version: Literal["1.0"] = SCRIPT_CONTRACT_VERSION
    script_id: str = Field(min_length=1)
    kind: ScriptKind
    summary: str
    invocation: tuple[str, ...] = Field(min_length=1)
    """The argv (or, for an MCP tool, its name); ``<name>`` marks an input to fill in."""
    inputs: tuple[ScriptInput, ...] = ()
    outputs: tuple[ScriptOutput, ...] = Field(min_length=1)
    errors: tuple[ScriptError, ...] = Field(min_length=1)
    source_is_opaque: Literal[True] = True
    """The agent does not read the source of the script."""


_CHECK_FAILED = "6"
_STDOUT_JSON = "json"
_CHECK = ScriptContract(
    script_id="harness.check",
    kind="harness-command",
    summary="Run the validators of the gate and the enabled diff checks on the workspace; "
    "nothing is recorded.",
    invocation=("harness", "check", "--path", "<workspace>", "--run", "<run id>"),
    inputs=(
        ScriptInput(
            name="--path",
            value_type="path",
            required=False,
            description="Project directory (default: the current directory).",
        ),
        ScriptInput(
            name="--run",
            value_type="string",
            required=False,
            description="Run whose task and baseline the diff checks use.",
        ),
    ),
    outputs=(
        ScriptOutput(
            name="report",
            channel="stdout",
            format=_STDOUT_JSON,
            description="status, validators[] (id, mandatory, status, summary, problems), "
            "checks[] (id, policy, issues) and blockingIssues.",
        ),
        ScriptOutput(
            name="status",
            channel="exit-code",
            format="integer",
            description="0 when every mandatory validator passes and no enforced check "
            "reports a blocking problem.",
        ),
    ),
    errors=(
        ScriptError(
            code=_CHECK_FAILED,
            meaning="A mandatory validator failed or an enforced check found a blocking problem.",
            agent_action="Fix what report.validators[].problems and report.checks[].issues "
            "list, then run it again.",
        ),
        ScriptError(
            code="2",
            meaning="No valid project configuration in the directory.",
            agent_action="Stop and report it: the configuration is a person's to fix.",
        ),
        ScriptError(
            code="3",
            meaning="The run named by --run has no check state.",
            agent_action="Run it without --run.",
        ),
    ),
)

_EXIT_ZERO = "0"


def _validator_contract(definition: Any) -> ScriptContract:
    command = tuple(definition.command or ())
    label = "mandatory" if definition.mandatory else "advisory"
    return ScriptContract(
        script_id=f"validator.{definition.validator_id}",
        kind="validator",
        summary=f"The {label} validator {definition.validator_id} of the gate.",
        invocation=command or ("<no command>",),
        inputs=(
            ScriptInput(
                name="cwd",
                value_type="path",
                required=True,
                description="The workspace root; the command takes no other input.",
            ),
        ),
        outputs=(
            ScriptOutput(
                name="status",
                channel="exit-code",
                format="integer",
                description=f"{_EXIT_ZERO} when the validator passes.",
            ),
            ScriptOutput(
                name="output",
                channel="stdout",
                format="text",
                description="What the tool printed; located problems are parsed from it.",
            ),
        ),
        errors=(
            ScriptError(
                code="non-zero",
                meaning="The validator found a problem or could not run.",
                agent_action="Read the problems in its output and fix the cause; do not edit "
                "the validator or weaken its configuration.",
            ),
        ),
    )


def _tool_contract(tool: Mapping[str, Any]) -> ScriptContract:
    schema = tool.get("inputSchema") or {}
    properties: Mapping[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    inputs = tuple(
        ScriptInput(
            name=str(name),
            value_type=_value_type(spec),
            required=name in required,
            description=str(spec.get("description") or spec.get("type") or ""),
        )
        for name, spec in properties.items()
    )
    return ScriptContract(
        script_id=f"mcp.{tool['name']}",
        kind="mcp-tool",
        summary=str(tool.get("description", "")),
        invocation=(str(tool["name"]),),
        inputs=inputs,
        outputs=(
            ScriptOutput(
                name="content",
                channel="result",
                format=_STDOUT_JSON,
                description="The text content of the MCP tool result.",
            ),
        ),
        errors=(
            ScriptError(
                code="isError",
                meaning="The tool result carries isError true with the reason as text.",
                agent_action="Read the reason and change the arguments; do not retry unchanged.",
            ),
        ),
    )


_VALUE_TYPES: Final[dict[str, ValueType]] = {
    "string": "string",
    "integer": "integer",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _value_type(spec: Mapping[str, Any]) -> ValueType:
    return _VALUE_TYPES.get(str(spec.get("type")), "string")


def registry(
    validators: Iterable[Any] = (), tools: Sequence[Mapping[str, Any]] = ()
) -> dict[str, ScriptContract]:
    """Every contract an agent may use: ``harness check``, the gate's validator commands and
    the tools of the embedded MCP server, by script id."""
    contracts = [_CHECK]
    contracts.extend(_validator_contract(item) for item in validators if item.command)
    contracts.extend(_tool_contract(item) for item in tools)
    return {item.script_id: item for item in contracts}


def brief(contract: ScriptContract) -> dict[str, Any]:
    """The contract as a request carries it: enough to call the script and read its answer."""
    return {
        "id": contract.script_id,
        "invocation": list(contract.invocation),
        "inputs": [item.name for item in contract.inputs if item.required],
        "errors": {item.code: item.agent_action for item in contract.errors},
    }
