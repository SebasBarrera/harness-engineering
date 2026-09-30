from __future__ import annotations

from governed_harness.plugins.protocol import PluginDescriptor


class PluginRegistry:
    def list(self) -> tuple[PluginDescriptor, ...]:
        return (
            PluginDescriptor(
                pluginId="profile/python",
                pluginVersion="1.0.0",
                protocolVersion="1.0",
                operations=("detect", "plan", "execute"),
                capabilities=("filesystem.read", "process.execute"),
                resultSchemas=("validation-result@1",),
            ),
            PluginDescriptor(
                pluginId="profile/node",
                pluginVersion="1.0.0",
                protocolVersion="1.0",
                operations=("detect", "plan", "execute"),
                capabilities=("filesystem.read", "process.execute"),
                resultSchemas=("validation-result@1",),
            ),
            PluginDescriptor(
                pluginId="agent/simulated",
                pluginVersion="1.0.0",
                protocolVersion="1.0",
                operations=("plan", "execute"),
                capabilities=("filesystem.read", "filesystem.write", "process.execute"),
                resultSchemas=("agent-invocation@1",),
            ),
        )
