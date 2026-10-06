# Embedded mode

In embedded mode (since 1.1, #56) an agent drives the governed flow from its own session (Claude
Code, Codex or any client of the Model Context Protocol) while the harness still enforces every
phase, gate, digest and decision. The session implements; the harness verifies, reviews (with
the project's provider, never the session itself) and waits for a person where a person decides.

## Set it up

```bash
harness init --agent-skills          # also writes the skill for Claude Code and Codex
claude mcp add harness -- harness mcp serve --path .
```

`--agent-skills` writes the same skill to `.claude/skills/harness/SKILL.md` and
`.codex/skills/harness/SKILL.md` (an existing file is kept unless `--force`): the flow, the
steps that belong to a person and the rules the harness enforces. A skill is instructions, not
authority. For Codex, register `harness mcp serve --path .` as an MCP server in its
configuration the same way.

## The flow

1. The session writes the task (`implementation: {mode: none}`) and creates it
   (`harness_task_create`).
2. `harness_run_start` starts the run with the provider `session`: INTENT to PLANNING run as
   usual, and IMPLEMENTATION waits (`BLOCKED`, exit 6) until the workspace differs from the
   baseline DISCOVERY recorded.
3. If INTENT asks questions, the session shows them (`harness_task_questions`), asks the person
   and relays the person's exact words (`harness_task_clarify`). The record names the person (the
   Git user under `governance.deciderIdentity: git`) and says the answers were relayed by an
   agent session.
4. The session edits the workspace, following the standards cards (`harness_standards`) and the
   architecture layers, runs `harness_check` until it passes and calls `harness_run_continue`:
   the edits become the candidate ChangeSet, attributed to `agent.session`, and VERIFICATION,
   the review and the gate run. A failed verification is not corrected automatically for the
   session provider: the session reads the findings (`harness_status`), edits and continues.
5. At DECISION (exit 4) the session stops and shows the person `harness review`; the person
   decides with `harness gate decide` in a terminal.

The read-only calls of the run (clarify, review, acceptance, plan, architecture) go to the
project's `agentProvider` (or the provider each call configures), so the author does not review
its own change. With `agentProvider: simulated` they get the simulated provider's empty answers;
configure an adapter (for example `kind: claude-code`) for a real second reviewer.

Under `agentRouting.mode: anchored` (what `harness init` writes, #85) the model of those
read-only calls never goes above the invoking model. The harness cannot see the model of the
session itself (the Model Context Protocol does not carry it), so the invoking model is
`agentRouting.anchorModel` when you set it (set it to the session's model), else the `model` of
`agentProvider`, else the `--model` value of its `command` or `args`; without any of them each
call keeps the provider's own model. The session implements with its own model; the harness
does not route it.

## The MCP server

`harness mcp serve` speaks JSON-RPC 2.0 over standard input and output, one message per line
(`initialize`, `ping`, `tools/list`, `tools/call`; notifications are not answered).

| Tool | What it does |
|---|---|
| `harness_project` | What the harness detects: new or existing, packs, testing strategy, architecture, forge. |
| `harness_status` | The status of a run. |
| `harness_inbox` | Runs waiting for a person. |
| `harness_task_create` | Create a task from a task document. |
| `harness_task_questions` | The open clarification questions of a task. |
| `harness_task_clarify` | Record the person's answers, marked as relayed. |
| `harness_run_start` | Start a run (provider `session` by default). |
| `harness_run_continue` | Continue a run. |
| `harness_check` | The gate's validators and checks, nothing recorded. |
| `harness_review` | The decision brief of a run. |
| `harness_standards` | The standards cards for some files. |

There is no tool for a human decision: gate, acceptance tests, plans, the architecture, budget
raises, exceptions and memory approvals are decided by a person with the CLI. A tool result for a
run carries `exitCode` (the code `harness run start` would give) and, at DECISION, the command a
person runs next. A harness error is a tool result with `isError: true`, the message and the exit
code.

The server and the skill are covered by `tests/integration/test_embedded_mode.py`; they have not
been exercised with the live agents.
