# Normative phases

| Phase | Purpose | Required exit evidence |
|---|---|---|
| INTENT | Identify the requested outcome, constraints and owner | Task, requirements, acceptance criteria, risk hints |
| DISCOVERY | Establish workspace, baseline, technology and impact | Detection evidence, baseline, impacted units |
| SPECIFICATION | Create an executable acceptance and validation contract | Versioned criteria, validation plan, unresolved assumptions |
| PLANNING | Select actions, capabilities and checks before mutation | Plan, capability requests, expected ChangeSet scope |
| IMPLEMENTATION | Produce an owned candidate ChangeSet | Agent/tool invocations, candidate ChangeSet digest |
| VERIFICATION | Run deterministic and technology-specific validators | Normalized validation results and evidence |
| INDEPENDENT_REVIEW | Evaluate the candidate separately from implementation | Findings or an explicit no-finding result with provenance |
| DECISION | Apply policy and obtain required human decision | Gate evaluation and digest-bound decision |
| CLOSURE | Freeze the run, release resources and render trace | Terminal event, final report, cleanup result |

Retrospective runs after closure and only produces recommendations. It never mutates policy automatically.

## Clarifying the intent

`INTENT` also checks that the acceptance criteria say something that can be observed. A
deterministic assessment (fixed vocabularies and patterns, no language model) raises a question
for a criterion without an observable result (`C1`), a quality without a measure (`C2`), a
duplicate criterion (`C3`) and a short intent with no requirements and a single criterion that has
no anchor (`T1`).
With `intake.criteriaPolicy: enforce` the phase is `BLOCKED` until a person answers with
`harness task clarify`; the answers produce a new task revision, and `harness run continue`
re-runs `INTENT` on it. The run never leaves `INTENT` with open questions, and no phase is added:
the questions, the answers, the actor and the previous and new task digests are evidence and
events of the same run. With `warn` the questions are recorded as evidence and `LOW` findings and
the phase passes; with `off` the check is skipped. See the
[configuration reference](../reference/configuration.md#acceptance-criteria-policy).

Under `enforce`, `harness task create` also accepts a task without acceptance criteria (for
example a single sentence of intent) and marks it `criteriaPending`; the other policies refuse
it with exit code 2. For such a task `INTENT` asks rule `C0` instead of the other rules: seven
separate questions about the observable results and how each is checked, the inputs and outputs,
the limits, the errors for invalid input, the behaviours in scope, what is out of scope and the
non-functional constraints. The answer about results becomes the acceptance criteria. A task
without criteria never passes `INTENT`, whatever the policy is when the run starts: the phase
stays `BLOCKED` (exit 6) until a revision has at least one criterion. See
[tasks without acceptance criteria](../reference/configuration.md#tasks-without-acceptance-criteria).

## Correcting a failed verification

A `REQUEST_CHANGES` decision has always returned the run from `DECISION` to `IMPLEMENTATION`. With
`runtime.verificationCorrections: N` (written by `harness init` as 2) a command provider's change
that fails `VERIFICATION` because a mandatory validator failed also returns to `IMPLEMENTATION`,
up to N times per run, instead of stopping. No phase is added and the workflow file is unchanged:
the state machine authorizes the transition from `VERIFICATION` only, the failed verification is
invalidated as a `REQUEST_CHANGES` invalidates the gate, and the next candidate goes through
`VERIFICATION` again, so a run never leaves the phase with a failing mandatory validator. Each
cycle is a `correction.authorized` event (`trigger: VERIFICATION_FAILED`); when the cycles are
used up the run stops in `VERIFICATION` as before. With `runtime.providerFeedback` the next
request tells the agent why (validator output, findings, reason codes and, after
`REQUEST_CHANGES`, the rationale); an agent that reported success on a change that then failed
verification gets an `agent.unsupported-claim` finding. See the
[provider feedback loop](../reference/configuration.md#provider-feedback-loop).
