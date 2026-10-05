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
retention:
  artifactDays: 30
  eventDays: 365
intake:
  criteriaPolicy: enforce
verification:
  requirementTraceability: enforce
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
| `retention` | written by `init` | **Declarative: no retention job exists.** |
| `intake.criteriaPolicy` | `warn` when the section is absent; `init` writes `enforce` | What INTENT does with acceptance criteria that cannot be observed: `enforce`, `warn` or `off`. See [acceptance-criteria policy](#acceptance-criteria-policy). |
| `verification.requirementTraceability` | `off` when the section or the key is absent; `init` writes `enforce` | What VERIFICATION does with identified requirements that no test names: `enforce`, `warn` or `off`. See [requirement traceability](#requirement-traceability). |

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

## Requirement traceability

Agents tend to report that every requirement has tests. With
`verification.requirementTraceability` set, `VERIFICATION` checks it: after the technology
validators it relates each identified requirement of the task to the tests of the workspace,
deterministically and without running anything.

**Identifier of a requirement.** The token that starts its text, matching
`^\s*(?:\[ID\][.:)]?|ID[.:)])\s` with `ID` = `[A-Z]{1,3}\d{1,3}(?:\.\d+)?`: `A1. Round to
cents.`, `[B12] Reject an empty basket.`, `X8: ...`, `C3.1) ...`. A requirement without such a token
is identified by its `requirementId` when the task file sets one (`requirementId: req_discount`);
the id the harness generates when the file gives none (`req_` followed by 32 hexadecimal digits)
does not count. When both exist, the text token is used. Requirements without an identifier are
skipped; the report counts them.

**Test files.** Anywhere in the workspace: for the Python profile, `test_*.py` and `*_test.py`;
for the Node profile, `*.test.*`, `*-test.*`, `*_test.*`, the same with `spec`, `test-*`, `test.*`
and every file under a `test`, `tests` or `__tests__` directory (extensions `js`, `cjs`, `mjs`,
`ts`, `cts`, `mts`, `jsx`, `tsx`). Hidden directories, `node_modules`, `__pycache__`, `venv`, `site-packages`, `build`,
`dist` and symbolic links are not searched; a file larger than 2,000,000 bytes is listed as unread.

**A test names a requirement** when

- the name of its file, class or function contains the identifier as a token, in any case:
  `test_a1_rounding`, `test_A1`, `TestA1`, `TestA1Rounding`, `tests/test_a1.py`
  (`test_a12` does not name `A1`; a dot becomes an underscore: `test_c3_1_...` names `C3.1`); or
- its docstring, string constants (a `pytest.param(..., id="A1")`, a Node `test('A1: ...')`
  title) or source lines, comments included, contain the identifier as a whole word, case
  sensitive (`A1` matches `A1:` and `[A1]`, not `A12`, `A1.2` or `a1`).

Python tests (functions named `test*`, classes named `Test*` and their methods) are read with
`ast`; a module docstring counts for the file. A Python file that does not parse and every Node
file are read as text, and a Node match outside a test title is attributed to the file.

| Policy | Identified requirement that no test names |
|---|---|
| `enforce` | One `HIGH` finding `traceability.requirement-untested` per requirement. With the default `findingBlockSeverities` the gate is `FAILED`: `APPROVE` exits with 5, and the person decides `REQUEST_CHANGES`, `APPROVE_EXCEPTION` with a rationale, or `REJECT`. |
| `warn` | One `LOW` finding per requirement; the gate does not count it. |
| `off` | No check: VERIFICATION behaves as in 1.0.0. |

The finding names the identifier and the first 80 characters of the requirement text, for example
`No test names requirement A2: A2. A subtotal below the threshold is unchanged.` With `enforce` and
`warn` the check is recorded as a validation result of `traceability.requirements` (`PASSED` when
the check ran, mandatory under `enforce`, so it appears in `validationSummary`) and the mapping,
each requirement with its identifier, where it came from and the tests that name it (node id, file,
how it matched), is stored as `VERIFICATION` evidence of kind `TEST_REPORT` (schema
`requirement-traceability.schema.json`). The plan of `PLANNING` lists the validator.

A `project.yaml` written before this section existed has no `verification` key and runs with
`off`; its configuration snapshot is serialized without the section, so its digest does not
change.

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
