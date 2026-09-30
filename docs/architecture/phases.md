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
