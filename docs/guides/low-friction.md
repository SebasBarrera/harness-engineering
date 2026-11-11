# Low friction for small changes

A one-line fix should not cost the same as a feature. Since 1.1 (issue #58) the governance of a
run is proportional to its size and risk: a small, risk-free task takes a fast lane, a person can
approve it in advance under a condition the harness checks, several decisions can be taken from
the inbox at once, and a documentation-only or configuration-only change does not need new
tests. A human always decides, the mandatory validators always run before the decision, and
everything the fast lane leaves out is recorded with its reason.

Every key lives in the `friction` section of `project.yaml`. A project without the section keeps
the 1.0.0 behaviour and its configuration digest; `harness init` writes it (see
[the configuration reference](../reference/configuration.md#friction)). This guide follows the
`friction` flow of `scripts/demo_flows.py`, which runs the commands below in CI with the exit
codes shown.

## The fast lane

```yaml
friction:
  fastLane:
    mode: auto
    skip: [ambiguityReview, decomposition, agentReview, preflight, mutation]
    verification: {affectedTestsFirst: true, parallel: true, cache: true}
```

At INTENT the router of #44 sizes the task (requirements, owned files and their lines, risk
flags). Size `S` without a risk flag is the **fast lane**; anything else is the full flow, and
so is a task that asks for stronger evidence (probes, a checklist, or a criterion verified by a
probe, a deferred check, a person or a rung above `L1`). The
lane, the size, the rule that decided and the steps left out are recorded as a `lane.classified`
event and a `lane-decision` evidence, and `harness review` shows them under `friction.lane`.

| Step in `skip` | In the fast lane |
|---|---|
| `ambiguityReview` | No agent ambiguity review at INTENT: the deterministic intake only (rules `C0`-`C3`, `T1`). |
| `decomposition` | No decomposition attempt in PLANNING. |
| `agentReview` | The agent review of INDEPENDENT_REVIEW runs only on a signal: a risk factor, a finding of this change of severity MEDIUM or above, or a ChangeSet larger than `S`. |
| `preflight` | The probes do not run on the baseline in PLANNING (the verification plan is still recorded). |
| `mutation` | No light mutation in VERIFICATION. |
| `acceptanceTests` | Not in the default list: add it to skip the independent acceptance tests. |

When the ChangeSet shows a risk factor (`newDependency`, `authentication`, `network`...) or
outgrows `S`, the run leaves the fast lane (`lane.escalated`, with the reasons) and the remaining
phases run in full.

**Faster verification.** In the fast lane, the Python tests the change affects run first (the
changed test files, the tests named after a changed module and the tests that import it); a
failure stops the attempt early, and the full suite still runs before the gate. Under
`verification.acceptanceTests.disputes: person` the frozen acceptance tests are not among them:
they run in `harness.acceptance-tests` and the full suite, so a failure confined to them can open
a dispute as in the full lane (#161); a failing ordinary affected test still stops the attempt.
The other
validators run side by side (`runtime.maxParallel`), and a `PASSED` validator result is reused for
the same validator, ChangeSet digest, baseline and configuration (`validator.reused`). Other
technologies have no affected-test selection.

## Governance profiles by risk

```yaml
governance:
  riskProfile: auto        # auto | light | full; harness init writes auto
```

`governance.riskProfile` chooses, task by task, between two governance profiles. It is read at
INTENT, from the same size and risk flags as the fast lane.

| Value | Light profile | Full profile |
|---|---|---|
| `auto` | a task the router sizes `S` with no risk flag | every other task |
| `light` | every task without a risk flag, whatever its size | a task with a risk flag |
| `full` | never | every task (no fast lane, even when `friction.fastLane` is on) |
| absent | the fast lane of `friction.fastLane`, as in 1.x | the rest |

A **risk flag** is `security` (a security term in the intent, the constraints or the
requirements, or an owned file matching `agentRouting.riskPaths`) or `interface` (the task
declares an interface); a task that declares verification beyond `L1` (probes, a checklist, a
deferred, manual or probe-verified criterion) always takes the full profile.

**The light profile** is the fast lane above (the `skip` and `verification` of
`friction.fastLane` when the section is there, the defaults otherwise, even when its `mode` is
`off`) and leaves out two more optional steps:

| Step | In the light profile |
|---|---|
| `ambiguityReview`, `decomposition`, `preflight`, `mutation` | Left out, as in the fast lane (or as `fastLane.skip` says). |
| `agentReview` | The agent review, or the review panel, runs only on a signal (a risk factor, a finding of this change of severity MEDIUM or above, a ChangeSet larger than `S`). |
| `locate` | No `locate` call at INTENT (`lane.step.skipped`). |
| `planApproval` | No plan-approval checkpoint under `planApproval: risk`; `always` still asks a person. |

What the light profile **always keeps**: the nine normative phases in their order, the
harness's own verification with every mandatory validator before the gate (tests, the
requirement traceability, the configured validators), the frozen acceptance tests of the task
when there are any (`acceptanceTests` is left out only when `fastLane.skip` names it), the
fail-closed gate, the human decision bound to the ChangeSet digest (or a pre-authorisation under
its condition) and the hash-chained event record. A defective change is blocked in VERIFICATION
as in the full profile. When the ChangeSet shows a risk factor or outgrows `S`, the run leaves
the light profile (`lane.escalated`) and the agent review runs.

**The full profile** is the flow of every setting `harness init` writes: the agent ambiguity
review, `locate` for `M` and `L` tasks, the decomposition, the plan approval of risky and large
tasks, the preflight, the light mutation and the agent review or panel on every run.

`lane.classified` and the `lane-decision` evidence carry `profile` (the configured value) and
`governance` (`light` or `full`) when the key is set; without it they are unchanged.

### The security flag in context

Without `governance.riskProfile` the `security` flag is the substring list of 1.x: `password`,
`secret`, `token`, `authentic`, `authoriz`, `credential`, `card`, `payment` or `encrypt`
anywhere in the text, so "discard", "tokenize" or "product card" make a small task risky. With
any value of the key the text is read in context:

* a strong term flags the task on its own: passwords, credentials, authentication and
  authorisation, OAuth, JWT, CSRF, MFA, encryption, API, private, secret and signing keys,
  access, refresh, bearer, session, auth and reset tokens, credit, debit and payment cards, card
  numbers, CVV, and the Spanish contraseña, credencial, autenticación, autorización, cifrado,
  encriptar and tarjeta de crédito;
* `token`, `secret`, `card` and `payment` count only within three words of a word of their
  context (`token` next to `bearer`, `refresh`, `user`, `rotate`...; `card` next to `credit`,
  `number`, `billing`...) or when an owned file has a sensitive path component (`auth`,
  `security`, `crypto`, `payment`, `session`...).

"Hash the users' passwords", "encrypt the e-mail addresses at rest" and "rotate the token when
the user signs out" are security tasks; "count the tokens of the tokenizer" and "rename the
product card" are not. The ChangeSet's risk factors are read from the diff whatever the task
says, so a change that touches authentication code still leaves the light profile.

## Change types

```yaml
friction:
  changeTypes: true
```

A ChangeSet whose files are all documentation (`.md`, `.rst`, `.adoc`, `.txt`, `LICENSE`...) or
all configuration (`.yaml`, `.toml`, `.ini`, `.json`, `.gitignore`...) is detected from its
paths. It needs no requirement traceability (the validator is recorded `NOT_APPLICABLE` with the
reason), no TDD evidence, no changed-line coverage and no light mutation
(`change-type.exempted` events). Every other check still runs.

## Approve in advance

```yaml
friction:
  preAuthorization: {mode: allow, defaultHours: 24, maxHours: 72}
```

When a person confirms the operational contract they may approve the run in advance. The
pre-authorisation is a human decision recorded before the ChangeSet exists, bound to the contract
digest and to a condition that cannot be relaxed: the automatic gate passed, the ChangeSet has no
risk factor and the task is size `S`. It has an expiry. At DECISION the harness records the
approval on the person's behalf (`human.decision.recorded` by the harness, the decision's actor
being the person, with `preAuthorizationId`) only when every condition holds, the contract digest
is unchanged and no one decided in the run already; otherwise it records why
(`decision.preauthorization.not-applied`) and the person is asked as usual. A manual checklist
item, a risk factor to acknowledge or an expired approval always goes back to the person.

```bash
harness do "The discount applies at or above the threshold." \
  --criterion "README.md states that the discount applies at or above the threshold" \
  --pre-approve --no-interactive          # exit 0: closed in one command
```

`harness task confirm --task TASK --digest DIGEST --pre-approve [--pre-approve-hours 4]` does the
same for a run waiting at INTENT. Confirming the contract and approving in advance is one act and
counts as one interaction.

## One command: harness do

`harness do TEXT` creates the task (the text is its intent and, without `--criterion`, its
acceptance criterion; `--owned` limits the paths), runs it and, on a terminal, asks INTENT's
questions and then shows the decision brief and asks for the decision, bound to the digest you
confirm. Without a terminal (or with `--no-interactive`) it stops where a person is needed and
prints the next command. No task file is needed.

## Decide from the inbox

```bash
harness inbox                                            # what waits, with each digest
harness inbox --approve RUN=sha256:DIGEST --rationale "Documentation only"
harness inbox --decisions decisions.yaml                 # run, decision, changeSetDigest, rationale
harness inbox --batch                                    # on a terminal, one run after the other
```

Each decision is bound to its own ChangeSet digest: a digest that changed is refused for that run
(the others are recorded) and the command exits 5. A decision entry lists in `acknowledgeRisks`
the risk factors an `APPROVE` must acknowledge (`verification.riskFactors`); a batch file gives
them per decision as `acknowledgeRisks: [network]`, and `--batch` asks for each one on the
terminal. The inbox also lists every other wait before DECISION, each with its `kind`, the
`digest` the answer binds to and the command that answers it in `next`: `plan` (the
plan-approval checkpoint), `decomposition`, `acceptance`, `architecture`, `contract`, `deferred`
and `preflight`. Only decisions are recorded in a batch; the other waits are answered with their
own command.

## The plan-approval checkpoint

```yaml
friction:
  planApproval: risk
```

PLANNING declares `approval.request` (#8). Under `risk`, a task the router sizes `L` or that
carries a risk flag waits after PLANNING for a person to approve its plan, bound to the plan
digest (`harness plan show`, `harness plan decide --decision APPROVE --digest DIGEST`); other
tasks skip it and the reason is recorded (`plan.approval.skipped`). A pre-authorisation in force
for the run's contract covers the plan approval (`plan.approval.covered`); the final decision
keeps its own condition. `always` asks for every task; `off` (or no key) keeps 1.0.0.

## Friction targets

```yaml
friction:
  targets:
    S: {interactions: 1, minutes: 30}
    M: {interactions: 3, minutes: 240}
    L: {interactions: 6, minutes: 1440}
```

`harness metrics` reports, per task, the interactions, the approvals, the wall time from the first
event of its first run to the last of its last, and the time the runs waited for a person,
against the target of the task's size. The values `harness init` writes are starting points to
calibrate, not measured optima. See [local metrics](local-metrics.md).

## Known limits

- The affected-test selection reads Python imports and file names; it does not trace dynamic
  imports or fixtures. The full suite runs before the gate whatever it selects.
- The change type is decided from paths: a `.json` file that is data, not configuration, is
  treated as configuration.
- A reused validator result assumes the validator is deterministic for the same files; a flaky
  test that passed once is reused within the same digest.
