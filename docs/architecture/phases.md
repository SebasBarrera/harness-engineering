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

## Tracing requirements to tests

After the technology validators, `VERIFICATION` relates every identified requirement of the task to
the tests of the workspace (validator `traceability.requirements`, no language model, nothing
executed). A requirement is identified by the token that starts its text (`A1. `, `[B12] `,
`X8: `) or by a `requirementId` written in the task file; requirements without one are skipped
and counted. A test names a requirement when its file, class or function name contains the
identifier as a token (`test_a1_rounding`, `TestA1`) or when its source, docstring or string
constants (a `parametrize` id) contain the identifier as a whole word. The mapping is recorded as
`VERIFICATION` evidence (`requirement-traceability.schema.json`) and the check appears in the
validation summary like any validator. With `verification.requirementTraceability: enforce`
(written by `init`) each untraced requirement is a `HIGH` finding, so the gate is `FAILED` and the
person deciding sees which requirement lacks a test before approving or requesting changes; with
`warn` it is a `LOW` finding; with `off` the check does not run. See the
[configuration reference](../reference/configuration.md#requirement-traceability).
