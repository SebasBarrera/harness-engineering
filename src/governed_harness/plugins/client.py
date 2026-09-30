from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from governed_harness.domain.enums import ActorType, ResultStatus
from governed_harness.domain.models import Actor, CapabilityGrant
from governed_harness.plugins.protocol import PluginRequest, PluginResponse
from governed_harness.runtime import CancellationToken, CommandSpec, SafeProcessRunner


class PluginProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class PluginInvocation:
    response: PluginResponse
    stdout: bytes
    stderr: bytes
    exit_code: int | None


class ExternalPluginClient:
    def __init__(self, workspace: Path, command: tuple[str, ...]) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.command = command
        self.runner = SafeProcessRunner(self.workspace)

    def invoke(
        self,
        request: PluginRequest,
        *,
        grants: list[CapabilityGrant],
        timeout_seconds: float = 60,
        cancellation: CancellationToken | None = None,
    ) -> PluginInvocation:
        actor = Actor(actor_type=ActorType.PLUGIN, actor_id="plugin.external")
        # Source-tree executions (tests, editable checkouts) need the package root
        # available to a Python plugin launched from an arbitrary workspace.  The
        # variable is passed explicitly through the runner's allowlist rather than
        # inheriting the parent environment wholesale.  Installed distributions do
        # not depend on this, but the additional path remains harmless.
        package_root = str(Path(__file__).resolve().parents[2])
        existing_pythonpath = os.environ.get("PYTHONPATH")
        pythonpath = os.pathsep.join(
            part for part in (package_root, existing_pythonpath) if part
        )
        result = self.runner.run(
            CommandSpec(
                argv=self.command,
                cwd=self.workspace,
                timeout_seconds=timeout_seconds,
                allowed_environment=("PYTHONPATH",),
                stdin=(json.dumps(request.model_dump(mode="json", by_alias=True)) + "\n").encode("utf-8"),
            ),
            actor=actor,
            grants=grants,
            extra_env={"PYTHONPATH": pythonpath},
            cancellation=cancellation,
        )
        if result.status is ResultStatus.TIMED_OUT:
            raise PluginProtocolError("plugin timed out")
        if result.status is ResultStatus.CANCELLED:
            raise PluginProtocolError("plugin was cancelled")
        if result.exit_code != 0:
            raise PluginProtocolError(
                f"plugin process failed with exit code {result.exit_code}: "
                f"{result.stderr.decode('utf-8', 'replace')[:500]}"
            )
        lines = [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
        if len(lines) != 1:
            raise PluginProtocolError("plugin stdout must contain exactly one JSON response line")
        try:
            response = PluginResponse.model_validate(json.loads(lines[0]))
        except Exception as error:
            raise PluginProtocolError(f"invalid plugin response: {error}") from error
        if response.request_id != request.request_id:
            raise PluginProtocolError("plugin response requestId does not match request")
        return PluginInvocation(response, result.stdout, result.stderr, result.exit_code)
