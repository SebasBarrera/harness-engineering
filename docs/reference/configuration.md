# Project configuration

`harness init` writes `.harness/project.yaml`. The file is validated against
`schemas/v1/project-config.schema.json` (unknown keys are rejected) and resolved together with the
technology profiles and the locked core policies into an immutable snapshot whose digests are
recorded on every run. `harness config validate` prints the resolved view.

## File written by `harness init`

This is the file produced for a new Python project (output of `harness init`, unchanged):

```yaml
configVersion: '1.0'
projectId: project_quickstart
workspace:
  root: ..
  units: []
profiles:
- auto
workflow: default_development
capabilities:
  default: deny
  grants: []
validators: []
policies:
  requireHumanDecision: true
  findingBlockSeverities:
  - HIGH
  - CRITICAL
agentProvider: simulated
runtime:
  commandTimeoutSeconds: 900
  maxOutputBytes: 1000000
  maxParallel: 2
  allowNetwork: false
  agentSandbox: enforce
  sandboxWritePaths:
  - /tmp
  - /var/folders
  - ~/.claude
  - ~/.claude.json*
  - ~/.cache
  - ~/Library/Caches
  - ~/.config
  - ~/.npm
retention:
  artifactDays: 30
  eventDays: 365
intake:
  criteriaPolicy: enforce
```

## Fields

| Field | Default | Effect |
|---|---|---|
| `configVersion` | required, `"1.0"` | Configuration format version. |
| `projectId` | required | Identifier used on tasks and runs. `init` derives it from the directory name. |
| `workspace.root` | `..` | Workspace root, relative to `.harness/`. |
| `workspace.units` | `[]` | Declared units (`unitId`, `root`, `profile`). **Declarative: not used by the engine.** |
| `profiles` | `["auto"]` | `auto` selects the detected profiles; otherwise list profile ids (`python_default`, `node_default`). |
| `workflow` | `default_development` | The normative workflow. Only the built-in workflow exists; its phase order is fixed (see issue #3). |
| `capabilities.default` | `deny` | Only `deny` is accepted. |
| `capabilities.grants` | `[]` | Extra grants: `capability`, `scope` (globs or command names), `approvalRequired`, `conditions` (for example `allowDelete`). They are **added** to the profile grants (issue #4). |
| `validators` | `[]` | Validator ids to run; empty means the profile defaults. An id not defined by a selected profile is a configuration error. |
| `policies` | see below | Policy overrides; locked policies can only be restated with their locked value. |
| `agentProvider` | `simulated` | Default provider for `run start` (`--provider` overrides it). |
| `agentProviders` | `{}` | Named command providers: `kind: command`, `command` (argv list, not empty), optional `model`. See [external agents](../guides/external-agents.md). |
| `runtime.commandTimeoutSeconds` | `900` | Timeout of agent-provider processes. Validators use their own `timeoutSeconds` from the profile. |
| `runtime.maxOutputBytes` | `1000000` | Bound on captured stdout/stderr per process (applied after capture, issue #9). |
| `runtime.maxParallel` | `2` | **Declarative: not used by the engine** (phases run sequentially). |
| `runtime.allowNetwork` | `false` | **Declarative: not enforced.** The local runner is not a network sandbox (issue #5). |
| `runtime.agentSandbox` | `off` when the key is absent; `init` writes `enforce` | Write confinement of command-provider (agent) processes: `enforce` or `off`. See [agent sandbox](#agent-sandbox). |
| `runtime.sandboxWritePaths` | none when absent; `init` writes the list above | Paths the agent may write besides the workspace and `$TMPDIR`: absolute or starting with `~/`, no `$` variables, an optional single trailing `*` for a name prefix. |
| `retention` | written by `init` | **Declarative: no retention job exists.** |
| `intake.criteriaPolicy` | `warn` when the section is absent; `init` writes `enforce` | What INTENT does with acceptance criteria that cannot be observed: `enforce`, `warn` or `off`. Only `enforce` accepts a task without acceptance criteria. See [acceptance-criteria policy](#acceptance-criteria-policy). |

## Policies

Resolution order: core policies, then profile policies, then project policies.

| Policy | Core value | Project may change it? |
|---|---|---|
| `requireHumanDecision` | `true` | **No** (locked `true`) |
| `approvalDigestBinding` | `true` | **No** (locked `true`) |
| `mandatoryNonSuccessBlocks` | `true` | **No** (locked `true`) |
| `retrospectiveAutoApply` | `false` | **No** (locked `false`) |
| `findingBlockSeverities` | `["HIGH", "CRITICAL"]` | Yes: severities whose findings make the gate `FAILED` |
| `allowEmptyChangeSet` | not set (false) | Yes: allow a run whose implementation produced no change |
| `repositoryContentTrusted` | `false` | Declared; **not read by any component** (issue #5) |
| `destructiveActionsDefault` | `deny` | Declared; **not read by any component** (issue #5) |

Setting a locked policy to any other value fails with a configuration error (exit code 2), for
example `project configuration may not weaken locked policy requireHumanDecision`.

## Acceptance-criteria policy

INTENT runs a deterministic assessment of the task (no language model) and turns what it finds
into clarification questions with stable ids (`Q-1`, `Q-2`, ...):

| Rule | Asks when |
|---|---|
| `C0` no acceptance criteria | The task has no acceptance criteria (accepted only under `enforce`, see [tasks without acceptance criteria](#tasks-without-acceptance-criteria)). Seven questions, each with its own id, and no other rule: `C0` also asks what `T1` would. |
| `C1` no observable result | A criterion has fewer than four words, or only vague words ("works", "correctly", "properly", "as expected", "good", "fine", "nice", "clean", "robust", "user-friendly", "well"), and no anchor: a digit, quoted or back-quoted text, a code identifier (`name()`, `snake_case`, a path, a file name, a `CamelCase` name such as `ValueError`) or a checkable result verb (returns, raises, rejects, accepts, equals, contains, lists, stores, prints, exits, responds, creates, deletes, matches, passes, fails, at most, at least, within, before, after). |
| `C2` quality without a measure | A criterion says "fast", "quick", "performant", "efficient", "scalable", "secure", "reliable" or "responsive" without a number. |
| `C3` duplicate | Two criteria have the same text once case, spacing and trailing punctuation are folded. |
| `T1` scope without breakdown | The intent has fewer than 25 words, there are no requirements and there is exactly one criterion, and that criterion has no anchor (as in `C1`). It is asked next to a `C1` or `C2` question about the same criterion, because it asks for the scope rather than the result. |

A criterion's `verificationHint` counts as part of what it says can be observed. What happens
with the questions depends on `intake.criteriaPolicy`:

| Policy | INTENT with questions |
|---|---|
| `enforce` | `BLOCKED` with `Intent needs clarification: N question(s)`; `run start` and `run continue` exit with 6. |
| `warn` | `PASSED`; each question is also recorded as a `LOW` finding of `intake.clarification`, which the gate does not count. |
| `off` | No assessment: INTENT behaves as in 1.0.0. |

With `enforce` and `warn` the questions are stored as a `clarification-request` artifact
(INTENT evidence, schema `clarification-request.schema.json`) and an
`intent.clarification.requested` event. A task without questions is not affected by any policy.
A task without acceptance criteria is the exception: INTENT blocks it with the `C0` questions
under every policy, and records the request with policy `enforce`.

A `project.yaml` written before this section existed has no `intake` key and runs with `warn`:
the run continues as before and only the evidence and findings are added. Its configuration
snapshot is serialized without the section, so its digest does not change.

`harness task questions --task T` shows the open request. A person answers it with
`harness task clarify --task T --file answers.yaml [--actor human.id]`:

```yaml
answers:                      # required: question id -> answer
  Q-1: apply_discount(100, 100, 0.1) returns 90.
  Q-2: Only the threshold rule; rounding is out of scope.
replaceCriteria:              # optional: replace a criterion by its id
  - criterionId: ac_works
    text: A subtotal equal to the threshold is reduced by the rate.
addCriteria:                  # optional
  - A subtotal below the threshold is unchanged.
addRequirements:              # optional; stored with source "clarification"
  - Apply the discount only at or above the threshold.
```

The harness stores a new revision of the task and a clarification record (schema
`clarification-record.schema.json`): the human actor, each question with its answer, the
previous and the new task digest and both task revisions as artifacts, recorded as evidence and
as an `intent.clarified` event on the run that asked. An answer about a criterion that the file
does not replace becomes (or extends) that criterion's `verificationHint`; an answer about the
task, when the file adds no requirement or criterion, becomes a requirement with source
`clarification`. `harness run continue` then assesses the revised task in INTENT.

`task clarify` rejects an unknown question id, an empty answer or an unknown field (exit 2), a
task without an open request (exit 3), and a task that has a run past INTENT or an actor id in a
namespace the harness uses for agents, validators or itself (`agent.`, `validator.`, `harness.`)
(exit 5). Actor ids are recorded, not authenticated.

### Tasks without acceptance criteria

`harness task create` refuses a task without acceptance criteria (exit 2, `at least one
acceptance criterion is required`) under `warn`, `off` and a `project.yaml` without the
`intake` section. Under `enforce` it accepts the task and stores it with
`criteriaPending: true`, so a task can start from a single sentence and get its criteria in
INTENT. Only the harness sets the marker (a task file that contains it is refused as having an
unknown field), and the task model accepts no criteria only together with it.

INTENT then asks rule `C0`: one question per thing the criteria must settle, in this order, so
that a person can answer them one by one. Each names the task title.

| Id | Target | Asks for |
|---|---|---|
| `Q-1` | `task:results` | The observable results that show the task is done and how each is checked (input or action -> exact expected result), one per line. |
| `Q-2` | `task:interface` | The main inputs and outputs, with their formats. |
| `Q-3` | `task:limits` | The limits and boundaries (numbers, sizes, times) that must hold. |
| `Q-4` | `task:errors` | The errors or rejections expected for invalid input. |
| `Q-5` | `task:scope` | The behaviours in scope, one per line. |
| `Q-6` | `task:out-of-scope` | What is explicitly out of scope, one per line. |
| `Q-7` | `task:non-functional` | Non-functional constraints (performance, security, persistence) with their measures. |

`harness task clarify` maps the answers to `C0` questions into the task as follows. A leading
list marker (`-`, `*`, `+`, `1.`, `1)`) is removed from each line and empty lines are skipped.

| Answer to | Lands in the task as | Unless the answers file has |
|---|---|---|
| `task:results` | acceptance criteria, one per line (priority `MUST`) | `addCriteria` (then the answer is only recorded) |
| `task:scope` | requirements with source `clarification`, one per line | `addRequirements` |
| `task:interface`, `task:limits`, `task:errors`, `task:non-functional` | one requirement each, with source `clarification` | `addRequirements` |
| `task:out-of-scope` | constraints `Out of scope: <line>`, one per line | (always) |

```yaml
answers:
  Q-1: |
    - POST /rides with a valid body returns 201 and the ride id.
    - GET /rides/{id} for an unknown id returns 404.
  Q-6: Payments
```

The revision must end up with at least one acceptance criterion, from the answer to
`task:results` or from `addCriteria`. While it has none it keeps `criteriaPending: true`, and
`harness run continue` blocks INTENT again with the seven `C0` questions (exit 6). Once it has
criteria the marker is removed and INTENT assesses the criteria with the other rules, as for any
task.

## Agent sandbox

The harness contains its own file handling to the workspace, but a command provider (an agent CLI)
is a separate process with your user's permissions. With `runtime.agentSandbox: enforce` the
harness wraps every command-provider invocation in `IMPLEMENTATION` (the first one and each one
after `REQUEST_CHANGES`) so that the operating system denies any file write outside:

- the workspace (always);
- the resolved `$TMPDIR` of the harness process (always);
- each path in `runtime.sandboxWritePaths`, with `~` expanded and symbolic links resolved (on macOS
  `/tmp` is `/private/tmp` and `/var/folders` is `/private/var/folders`).

Reads, network access and process execution stay allowed: an agent reads the system, calls its
model API and runs tools. The simulated provider and the validators are the harness's own code and
are not wrapped.

| Platform | Mechanism |
|---|---|
| macOS | `/usr/bin/sandbox-exec -p <profile>`: `(allow default)`, `(deny file-write*)`, then `(allow file-write* ...)` for each allowed path (`subpath`, or a `regex` for a trailing `*`) and for `/dev/null`, `/dev/zero`, `/dev/stdout`, `/dev/stderr`, `/dev/ptmx`, `/dev/dtracehelper`, `/dev/fd/*` and `/dev/tty*`. |
| Linux with `bwrap` | `bwrap --ro-bind / / --dev-bind /dev /dev --die-with-parent --bind <path> <path> ... --`. A bind needs an existing source, so a path that does not exist is skipped and recorded, and a trailing `*` binds each existing file that matches. Not exercised in this repository's CI. |
| Linux without `bwrap`, Windows, others | None. `IMPLEMENTATION` is `BLOCKED` before the provider starts (`run start` exits with 6) with a `HIGH` finding `sandbox.unavailable` of `harness.sandbox`. Install bubblewrap, or set `agentSandbox: 'off'` to run the agent unconfined. |

Each confined invocation records `IMPLEMENTATION` evidence (an `agent-sandbox` artifact with the
mechanism, the platform, the profile or `bwrap` arguments, its SHA-256 digest and the allowed and
skipped paths) and an `agent.sandbox.applied` event with the digest. When the provider exits with an
error and its standard error reports a denied write (`Operation not permitted`, `Read-only file
system`, `sandbox`), the run gets a `MEDIUM` finding `sandbox.write-denied` naming the path when
the message contains one. Neither finding is attached to a validation, so neither reaches the gate.

Write paths that `init` declares, and why:

| Path | Why an agent CLI writes there |
|---|---|
| `/tmp` | Shared temporary directory: compilers, npm and git use it. |
| `/var/folders` | Per-user temporary and cache directories of macOS; `$TMPDIR` lives here. |
| `~/.claude` | Claude Code keeps its settings, session state, todos and logs here. |
| `~/.claude.json*` | Claude Code rewrites its configuration file through temporary, backup and lock files next to it. |
| `~/.cache` | XDG cache directory used by agent CLIs, pip, uv and many tools. |
| `~/Library/Caches` | Per-user cache directory of macOS (update checks, node caches). |
| `~/.config` | XDG configuration directory where agent CLIs keep state and credentials. |
| `~/.npm` | npm cache and logs: npx-launched agents and MCP servers write here. |

Add the paths your own agent setup writes to (hooks, plugin logs). A write the sandbox denies is
reported to the agent as `EPERM`/`Operation not permitted`; macOS also logs it, for example
`log show --last 5m --predicate 'sender == "Sandbox"'`. Under `sandbox-exec` a confined process
cannot execute setuid programs (`forbidden-exec-sugid`).

A `project.yaml` written before this setting existed has neither key and runs with `off`, as in
1.0.0; its configuration snapshot is serialized without them, so its digest does not change. YAML
1.1 reads a bare `off` as `false`; the harness accepts that as `off`.

## Technology profiles

Profiles are built into the package (`src/governed_harness/resources/profiles/`). Detection is
read-only and reports a confidence with the marker files it found.

| Profile | Detection markers (weight) | Mandatory validator | Optional validators (when available) |
|---|---|---|---|
| `python_default` | `pyproject.toml` (0.80), `requirements.txt` (0.45), `pytest.ini` (0.35) | `python.pytest`: `python -m pytest -q` | `python.ruff`: `python -m ruff check .`, `python.mypy`: `python -m mypy .` |
| `node_default` | `package.json` (0.80), `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` (0.35 each) | `node.test`: `npm test --silent` (requires a `test` script) | `node.lint`: `npm run lint --silent`, `node.typecheck`: `npm run typecheck --silent` (only if the scripts exist) |

An absent optional validator is recorded as `NOT_APPLICABLE`. An absent mandatory executable is
`BLOCKED`; a mandatory `python -m <module>` whose module is missing currently runs and is reported
as `FAILED` (issue #1).
