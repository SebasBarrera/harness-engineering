# ADR 0003: Normative phases

- Status: Accepted provisionally
- Date: 2026-08-30

## Decision

The primary development workflow has nine normative phases:

1. `INTENT`
2. `DISCOVERY`
3. `SPECIFICATION`
4. `PLANNING`
5. `IMPLEMENTATION`
6. `VERIFICATION`
7. `INDEPENDENT_REVIEW`
8. `DECISION`
9. `CLOSURE`

`RETROSPECTIVE` is a separate post-run workflow. It cannot block release of resources or rewrite the completed run.

## Semantics

- Phases cannot be reordered or deleted.
- A phase may finish `NOT_APPLICABLE` only when a versioned policy explicitly permits it and records evidence for that determination.
- `BLOCKED`, `ERROR`, `INCONCLUSIVE`, `TIMED_OUT`, `CANCELLED` and `SKIPPED` are not success.
- Correction creates a new implementation attempt and ChangeSet digest, followed by fresh verification and review.
- An approval is valid only for the exact ChangeSet, configuration, workflow and policy digests.
