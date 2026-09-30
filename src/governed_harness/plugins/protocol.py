from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from governed_harness.domain.enums import ResultStatus


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PluginDescriptor(ProtocolModel):
    plugin_id: str = Field(alias="pluginId")
    plugin_version: str = Field(alias="pluginVersion")
    protocol_version: Literal["1.0"] = Field(alias="protocolVersion")
    digest: str | None = None
    operations: tuple[
        Literal[
            "handshake",
            "detect",
            "describeCapabilities",
            "plan",
            "execute",
            "interpret",
            "health",
            "cleanup",
        ],
        ...,
    ]
    capabilities: tuple[str, ...] = ()
    result_schemas: tuple[str, ...] = Field(default=(), alias="resultSchemas")
    supported_platforms: tuple[str, ...] = Field(default=(), alias="supportedPlatforms")


class PluginRequest(ProtocolModel):
    protocol_version: Literal["1.0"] = Field(default="1.0", alias="protocolVersion")
    request_id: str = Field(alias="requestId")
    operation: str
    deadline: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    capability_grant: dict[str, Any] = Field(default_factory=dict, alias="capabilityGrant")
    payload: dict[str, Any] = Field(default_factory=dict)


class PluginResponse(ProtocolModel):
    protocol_version: Literal["1.0"] = Field(default="1.0", alias="protocolVersion")
    request_id: str = Field(alias="requestId")
    status: ResultStatus
    kind: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()


PLUGIN_EXIT_PROTOCOL_ERROR = 10
PLUGIN_EXIT_CONFIGURATION_ERROR = 11
PLUGIN_EXIT_INTERNAL_ERROR = 12
PLUGIN_EXIT_TIMEOUT = 124
PLUGIN_EXIT_CANCELLED = 130
