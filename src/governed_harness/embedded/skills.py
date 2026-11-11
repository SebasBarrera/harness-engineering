"""Skill files that teach an agent session the governed flow (#56).

``harness init --agent-skills`` writes the same skill for Claude Code
(``.claude/skills/harness/SKILL.md``) and Codex (``.codex/skills/harness/SKILL.md``). A skill is
instructions, not authority: the harness still enforces every gate, digest and decision, and
the skill tells the agent which steps belong to a person."""

from __future__ import annotations

from pathlib import Path

SKILL_PATHS: tuple[str, ...] = (
    ".claude/skills/harness/SKILL.md",
    ".codex/skills/harness/SKILL.md",
)

SKILL_TEXT = """\
---
name: harness
description: Drive a governed change with the Governed Agent Harness. Use when the user asks for a governed, verified or approved change, or when the repository has .harness/project.yaml.
---

# Governed change with the harness

The harness governs the change; you implement it in this session. Gates, digests and human
decisions are enforced by the harness, not by you.

## Flow

1. Write the task as a task document (title, intent, requirements, acceptance criteria with an
   observable result, constraints) with `implementation: {mode: none}`, and create it:
   MCP tool `harness_task_create`, or `harness task create --file task.yaml`.
2. Start the run with the session provider: `harness_run_start` (provider `session`), or
   `harness run start --task <taskId> --provider session`.
3. If INTENT asks questions (exit 6, `Intent needs clarification`), show them to the person
   (`harness_task_questions`), ask for their answers and relay their exact words with
   `harness_task_clarify`. Never answer for the person. Then `harness_run_continue`.
4. If a phase waits for a person (acceptance tests, a plan, the architecture), tell the person
   the command the status shows (`harness acceptance decide`, `harness plan decide`,
   `harness architecture decide`). Do not run it yourself.
5. When the run waits in IMPLEMENTATION, implement in the workspace. Follow the standards cards
   (`harness_standards` with the files you touch) and the architecture layers. Run
   `harness_check` (or `harness check --run <runId>`) until it passes, then
   `harness_run_continue`.
6. If VERIFICATION or the review reports findings (`harness_status`), fix them in the workspace
   and continue again.
7. When the run reaches DECISION (exit code 4), stop. Show the person `harness review --run
   <runId>`; the person decides with `harness gate decide`. You never decide a gate.

## Rules

- Do not weaken tests, thresholds or checks to make them pass; do not edit `.harness/`.
- Clean up only the code you touch; no unrelated refactors or reformatting.
- With TDD the tests you add must fail before your change: write them first.
- With BDD the approved feature files are frozen: write step definitions, not new scenarios.

## Without the MCP server

Every tool has a command: `harness status`, `harness inbox`, `harness task create`,
`harness task questions`, `harness task clarify`, `harness run start`, `harness run continue`,
`harness check`, `harness review`, `harness standards show --file <path>`.
"""


def write_agent_skills(workspace: Path, *, force: bool = False) -> list[dict[str, str]]:
    """Write the skill files; an existing file is kept unless ``force``."""
    written: list[dict[str, str]] = []
    for relative in SKILL_PATHS:
        target = workspace / relative
        if target.exists() and not force:
            written.append({"path": relative, "status": "KEPT"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(SKILL_TEXT, encoding="utf-8")
        written.append({"path": relative, "status": "WRITTEN"})
    return written


__all__ = ["SKILL_PATHS", "SKILL_TEXT", "write_agent_skills"]
