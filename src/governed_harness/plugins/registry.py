from __future__ import annotations

from governed_harness.plugins.protocol import PluginDescriptor

_FILESYSTEM_READ = "filesystem.read"
_PROCESS_EXECUTE = "process.execute"


class PluginRegistry:
    def list(self) -> tuple[PluginDescriptor, ...]:
        return (
            PluginDescriptor(
                pluginId="profile/python",
                pluginVersion="1.0.0",
                protocolVersion="1.0",
                operations=("detect", "plan", "execute"),
                capabilities=(_FILESYSTEM_READ, _PROCESS_EXECUTE),
                resultSchemas=("validation-result@1",),
            ),
            PluginDescriptor(
                pluginId="profile/node",
                pluginVersion="1.0.0",
                protocolVersion="1.0",
                operations=("detect", "plan", "execute"),
                capabilities=(_FILESYSTEM_READ, _PROCESS_EXECUTE),
                resultSchemas=("validation-result@1",),
            ),
            PluginDescriptor(
                pluginId="agent/simulated",
                pluginVersion="1.0.0",
                protocolVersion="1.0",
                operations=("plan", "execute"),
                capabilities=(_FILESYSTEM_READ, "filesystem.write", _PROCESS_EXECUTE),
                resultSchemas=("agent-invocation@1",),
            ),
        )
