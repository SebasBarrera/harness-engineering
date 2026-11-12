# Trust model

What the harness can be trusted to enforce, against which kind of agent, and what it does not
guarantee. Spanish version: [Modelo de confianza](../es/modelo-de-confianza.md). The statements
below describe 2.0.1 with the configuration `harness init` writes; a `project.yaml` written by an
earlier version keeps the earlier behavior for every key it lacks.

## Trust layers

Repository content is data, not policy. A file may contain prompt injection or malicious scripts.
The harness therefore separates:

- immutable core invariants;
- locked organizational policy;
- validated project configuration;
- untrusted workspace content;
- external tools and plugins;
- probabilistic model output.

A higher-trust layer may constrain a lower-trust layer. A lower-trust layer cannot relax a
higher-trust restriction.

## Three kinds of agent

The harness runs on the developer's machine, as the developer's OS user. What it resists depends on
what the agent tries to do.

| Agent | What it does | What the harness gives |
|---|---|---|
| **Honest** | Follows the task and reports what it did, but may be wrong. | The nine phases always run; validators run without a shell and their exit codes are the evidence, not the agent's summary; a non-success status never becomes success; the person decides on the exact ChangeSet digest. |
| **Careless** | Edits outside the task, deletes or weakens tests, claims checks it did not run, keeps editing after the gate. | Only owned paths enter the ChangeSet and edits to excluded paths are findings; the weakened-controls check (a heuristic over the diff) flags deleted tests, dropped assertions and silenced linters; a claim without evidence is an `agent.unsupported-claim` finding; any edit after the gate changes the digest, so an earlier approval is refused as stale. |
| **Adversarial** | Tries to make an unapproved change look approved: forge a decision, rewrite the record, escape confinement. | Partial protection only. An actor id in the `agent.`, `validator.` or `harness.` namespace is refused; with the `api` section the local API binds a decision to the user of the authenticated token; on macOS and Linux the agent sandbox keeps `.harness/`, `.git` and the run registry read-only for the agent process and the processes it starts; `harness verify` detects an edited, reordered or truncated chain as long as the anchor was not rewritten too. Everything under [What it does not guarantee](#what-it-does-not-guarantee) is open to this agent. |

The design target is the honest and the careless agent: the harness turns their mistakes into
blocking, recorded findings. Against an adversarial process that runs with the user's permissions
it raises the cost of an attack and leaves a trace, but it is not a security boundary.

## What it does not guarantee

### The identity of the person who decides

The actor id of a human decision is **declared, not authenticated**
(`src/governed_harness/domain/actors.py`, `require_human_actor`). On the CLI it comes from
`--actor`, from the Git identity under `governance.deciderIdentity: git` (which `harness init`
writes), or from the configured default. Any process that can run `harness gate decide` can pass
any id outside the reserved namespaces, and anyone can set `git config user.email`. The namespace
check stops an agent from deciding under its own recognizable id; it does not prove who typed the
command.

The local API is the exception: with the `api` section that `harness init` writes, every route
requires a bearer token, a decision needs the `reviewer` or `admin` role, and an `actor_id` that is
not the token's user is refused with 403 (`src/governed_harness/api/app.py`). The tokens come from
environment variables of the user who starts the server, so a process with that user's
environment can read them too. Without the `api` section the API has no authentication at all.

### The barrier in embedded mode

In embedded mode the agent session drives the run through `harness mcp serve`. The MCP server
exposes no decision tool, and its instructions tell the session never to decide for a person
(`src/governed_harness/embedded/mcp_server.py`, `HUMAN_ONLY` and `INSTRUCTIONS`). That is a text
instruction plus the absence of a tool: a session that also has a shell can run
`harness gate decide` itself, with any human-looking actor id. The session is not a process the
harness launches, so `runtime.agentSandbox` does not confine it either.

### Integrity against a process with the user's permissions

Events are chained with SHA-256 digests without a key (`src/governed_harness/events/sqlite_store.py`).
The chain detects an edited or reordered event and the anchor (`governance.chainAnchor`, which
`harness init` sets to `file`) detects a truncated chain, but a process with the user's permissions
can rewrite the SQLite store, recompute every digest and rewrite the anchor file or Git note:
neither is tamper-proof (`src/governed_harness/events/anchor.py`). Evidence carries no external
signature or timestamp. The record detects accidents and partial edits, and reconstructs what
happened as long as nobody rewrote it.

### Isolation on Windows, and of validators everywhere

Write confinement of agent providers exists only on macOS (`sandbox-exec`) and on Linux with
bubblewrap (`bwrap`) (`src/governed_harness/runtime/sandbox.py`). On Windows, or on Linux without
`bwrap`, `runtime.agentSandbox: enforce` blocks `IMPLEMENTATION` before the provider starts (a
`HIGH` finding `sandbox.unavailable`); the only way to run the agent there is
`agentSandbox: off`, which runs it with the user's permissions and without the protections that
depend on the sandbox (read-only state, network denial). Validators and plugins are never confined
on any platform: an authorized command keeps the file-system, network, CPU and memory permissions
of the user. See [This is not a sandbox](https://github.com/SebasBarrera/harness-engineering#this-is-not-a-sandbox)
and [SECURITY.md](https://github.com/SebasBarrera/harness-engineering/blob/develop/SECURITY.md).

## Declared attribution, not non-repudiation

Because the decider's identity is not authenticated and the record has no keyed signature, the
harness offers **declared attribution**: each decision records who it says decided, on which
digest, with which rationale and identity source, in a chain that detects later accidental or
partial edits. It does **not** offer non-repudiation: a person can deny a CLI decision recorded
under their id, and the record alone cannot prove otherwise. With the API's token authentication
the attribution is to the holder of a token, which is stronger but still local to the machine.

The evaluation catalogue and preregistration, written before this page, used "non-repudiation"
(«no repudio») for the decider-identity test H16; the catalogue now says declared attribution, and
the preregistration keeps its criteria with a dated note. H16 measures which decision attempts are
refused, not whether a decision can be repudiated.
