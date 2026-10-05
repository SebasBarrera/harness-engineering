"""The environment an agent provider receives (``passEnv`` and ``env``, since 1.1).

The process runner gives every command only ``PATH``, ``HOME``, ``SYSTEMROOT``, ``TMPDIR``,
``TEMP``, ``LANG`` and ``LC_ALL``: an agent CLI did not receive its API key, a proxy or a
certificate bundle, and the adapter template of the external-agents guide failed with
``KeyError: 'AGENT_COMMAND'``. A provider now declares what it needs:

* ``passEnv``: names of variables of the harness's environment passed as they are (a variable
  that is not set is not passed);
* ``env``: variables set for the provider, as a literal value or ``{fromEnv: NAME}`` (the value
  of ``NAME`` in the harness's environment; a missing ``NAME`` stops IMPLEMENTATION before the
  provider starts, instead of running the agent without its credentials).

Only the names are recorded as evidence. Every value that comes from the environment is a
secret literal of the artifact store: it is redacted from every stored artifact and from the
agent's summary.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from governed_harness.configuration.models import (
    AgentProviderConfiguration,
    ProjectConfiguration,
    ProviderEnvReference,
)


@dataclass(frozen=True)
class ProviderEnvironment:
    allowed: tuple[str, ...] = ()
    """Names the process may receive (``passEnv`` and the keys of ``env``)."""
    values: dict[str, str] = field(default_factory=dict)
    """Values set from ``env`` (literals and resolved ``fromEnv`` references)."""
    missing: tuple[str, ...] = ()
    """``fromEnv`` variables that are not set in the harness's environment."""
    passed: tuple[str, ...] = ()
    """``passEnv`` names that were set and are passed."""

    def evidence(self) -> dict[str, list[str]]:
        """Names only, never values."""
        return {
            "passEnv": list(self.passed),
            "env": sorted(self.values),
            "missing": list(self.missing),
        }


def provider_environment(
    config: AgentProviderConfiguration, source: Mapping[str, str] | None = None
) -> ProviderEnvironment:
    environ = os.environ if source is None else source
    pass_env = tuple(config.pass_env or ())
    values: dict[str, str] = {}
    missing: list[str] = []
    for name, value in sorted((config.env or {}).items()):
        if isinstance(value, ProviderEnvReference):
            if value.from_env not in environ:
                missing.append(value.from_env)
                continue
            values[name] = environ[value.from_env]
        else:
            values[name] = value
    return ProviderEnvironment(
        allowed=tuple(dict.fromkeys((*pass_env, *sorted(values)))),
        values=values,
        missing=tuple(missing),
        passed=tuple(name for name in pass_env if name in environ),
    )


def secret_values(
    project: ProjectConfiguration, source: Mapping[str, str] | None = None
) -> tuple[bytes, ...]:
    """The values of every configured provider and project validator that come from the
    environment (``passEnv`` and ``fromEnv``), to be redacted from the artifacts."""
    environ = os.environ if source is None else source
    names: list[str] = []
    for config in project.agent_providers.values():
        names.extend(config.pass_env or ())
        names.extend(
            value.from_env
            for value in (config.env or {}).values()
            if isinstance(value, ProviderEnvReference)
        )
    for validator in project.toolchain_settings.validators or ():
        names.extend(validator.pass_env or ())
    return _encoded(environ[name] for name in dict.fromkeys(names) if name in environ)


def _encoded(values: Iterable[str]) -> tuple[bytes, ...]:
    return tuple(value.encode("utf-8") for value in values if value)
