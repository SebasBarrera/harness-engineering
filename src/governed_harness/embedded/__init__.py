"""Embedded mode (#56): the harness inside an agent session (MCP server and skill files)."""

from __future__ import annotations

from governed_harness.embedded.mcp_server import TOOLS, McpServer, serve_stdio
from governed_harness.embedded.skills import SKILL_PATHS, SKILL_TEXT, write_agent_skills

__all__ = ["SKILL_PATHS", "SKILL_TEXT", "TOOLS", "McpServer", "serve_stdio", "write_agent_skills"]
