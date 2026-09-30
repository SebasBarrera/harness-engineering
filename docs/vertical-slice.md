# Executable vertical slice

## User story

As a maintainer, I want an AI-assisted change to pass through a controlled execution so that I can reconstruct the intention, plan, actions, exact ChangeSet, validations, independent review, policy decision and human approval from one trace.

## Real components

- project initialization and technology detection;
- typed task and acceptance contract;
- immutable configuration/workflow/policy snapshots;
- nine-phase state machine;
- SQLite event chain and typed projections;
- redacted content-addressed artifact store;
- scoped capability grants;
- simulated or structured-command agent provider;
- task-owned ChangeSet and digest;
- real Python or Node.js test execution;
- optional lint/type-check validators;
- independent deterministic review;
- fail-closed gate evaluation;
- digest-bound human decision;
- closure, metrics, trace and retrospective.

## Reproducible Python path

Use `tests/fixtures/python-project` as a disposable fixture copy, or initialize any compatible repository:

```bash
harness init --path <python-project>
harness task create --path <python-project> --file examples/task-python.yaml
harness run start --path <python-project> --task task_python_add_discount
```

Expected intermediate result:

- execution status: `BLOCKED`;
- current phase: `DECISION`;
- automated validators passed or optional validators are explicitly not applicable;
- ChangeSet digest is present;
- process exit code: `4`.

Then:

```bash
harness status --path <python-project> --run <run-id>
harness gate decide \
  --path <python-project> \
  --run <run-id> \
  --decision APPROVE \
  --change-set-digest <current-digest> \
  --actor human.reviewer \
  --rationale 'Reviewed automated evidence and independent findings'
harness trace --path <python-project> --run <run-id> --format markdown
```

## Reproducible Node.js path

```bash
harness init --path <node-project>
harness task create --path <node-project> --file examples/task-node.yaml
harness run start --path <node-project> --task task_node_precedence
```

The same core entities, phases, event types, gates and decision semantics are used. Only detection, default commands, validator availability and capability scopes come from the Node.js profile.

## Error and recovery paths covered

1. A failing mandatory test stops in `VERIFICATION`.
2. A manual correction followed by `run continue` re-executes verification.
3. A critical secret finding makes the gate `FAILED`.
4. Ordinary approval of a failed gate is rejected.
5. An explicit exception may close the synthetic fixture while preserving finding, rationale and digest.
6. Modifying an owned file after the gate invalidates the old digest and rejects stale approval.
7. Unrelated files are excluded from task ownership and the ChangeSet digest.
8. Event or artifact tampering is detected.
9. A malformed plugin/agent response becomes a protocol error rather than success.

## Definition of Done

- all tests pass with `ResourceWarning` treated as an error;
- public runtime records validate against committed schemas;
- Python and Node.js executions close after exact approval;
- the command-provider adapter completes an end-to-end run;
- no persisted artifact contains the synthetic secret used by security tests;
- event chains verify for successful runs;
- wheel installation passes a clean-environment smoke test;
- benchmark methodology and results are packaged with environment metadata.
