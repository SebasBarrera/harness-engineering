# Evaluation plan

## Design

Use controlled greenfield and brownfield tasks. Compare the same agent model and version with and without the harness while holding repository, task, tool access, time budget and environment as stable as possible. Prefer paired or counterbalanced tasks to reduce learning effects.

Do not claim statistical significance unless the final sample and design support it. The initial study is formative and comparative.

## Greenfield fixture

- Technology: Python.
- Task: implement a small rules library from an explicit specification.
- Ground truth: visible acceptance criteria plus hidden functional tests and mutation-style edge cases.
- Reset: fresh clone or archive from a signed fixture tag.
- Primary measures: functional correctness, trace completeness, validation coverage, implementation attempts, correction cycles, tokens when provider-reported, review time and perceived control.

## Brownfield fixture

- Technology: Node.js/TypeScript.
- Task: modify a precedence rule in an existing modular codebase without violating an architectural boundary.
- Ground truth: baseline tests, hidden regression tests, dependency-boundary check and known pre-existing warning.
- Reset: fresh clone from a signed fixture tag; dependency cache may be reused but must be recorded.
- Primary measures: correct change location, regressions, introduced findings, distinction between pre-existing and introduced failures, rework and review effort.

## Operational definitions

- Functional correctness: proportion of visible and hidden acceptance tests passed on the final ChangeSet.
- Iteration: a new implementation attempt over the same task/specification version, not a self-reported model statement.
- Correction cycle: failed validation or authorized finding, followed by a new ChangeSet and complete revalidation.
- Replanning: a new plan version caused by changed scope, impact or acceptance contract.
- Rework: time or changed hunks causally linked to correction after the first candidate ChangeSet.
- Trace completeness: required causal relations present divided by required relations for the run type.
- Gate compliance: mandatory gates evaluated with current evidence divided by mandatory gates.
- Tokens/cost: provider-reported when available; otherwise explicitly unavailable. Derived cost requires a versioned pricing snapshot.
