# Memory and retrospective decisions

The harness keeps **governed memory**: records that a later run may receive as context, each with
its provenance, validity and approval. Nothing enters a context because it was written once; a
record enters when it is in scope, in force and, for the levels that carry authority, approved by
a person. Every run stores a **context manifest** with the records it applied and the candidates
it left out, so the memory behind a run can be audited afterwards.

This guide shows the commands with the output of a real session
(`python scripts/demo_flows.py memory`, checked in CI).

## Levels

| Level | Holds | Enters a context when |
|---|---|---|
| `NORMATIVE` | Stable rules of the way of working | approved and in force |
| `PROJECT` | Knowledge and conventions of the repository | approved and in force |
| `TASK` | Notes of one task | the run belongs to that task |
| `EPHEMERAL` | Notes of one run | the run is that run |
| `RETROSPECTIVE` | Decided retrospective recommendations | accepted or edited by a person |

A record also declares who recorded it (`provenance.actor`), an optional `validUntil`, the record
it `supersedes` and whether it is `sensitive`.

## Record, approve, invalidate

A proposal is recorded but stays out of every context until someone approves it:

```bash
harness memory add --level project --key money.rounding \
  --value "Round money half up to two places." --actor human.author      # exit 0
harness memory approve --memory mem_a0c4…84b4 --actor human.lead          # exit 0
harness memory approve --memory mem_fecb…dff3                             # exit 5: already approved
```

Approval and invalidation never edit a record. Each creates a **new record that supersedes** the
previous one and carries the acting person, so the proposal, its approver and the reason of an
invalidation all stay on record:

```bash
harness memory invalidate --memory mem_fecb…dff3 \
  --reason "Rounding moved to the billing service." --actor human.lead    # exit 0
harness memory list
```

`memory list` shows every record with its status for the given `--task` and `--run`:

| Status | Meaning |
|---|---|
| `active` | Enters the context |
| `superseded` | Replaced by a record that carries authority (`supersededBy`) |
| `expired` | `validUntil` is in the past |
| `unapproved` | A normative, project or retrospective record nobody approved |
| `limit` | Beyond the maximum number of records of a context |
| `out_of_scope` | A task or run record of another task or run |

An unapproved record never supersedes an approved one. `--value` takes a JSON object or plain
text (stored as `{"text": …}`); `--sensitive` withholds the value from the manifest and from the
agent; `--approve` records the entry as approved by the acting person. A `TASK` record needs
`--task` and an `EPHEMERAL` one needs `--run` (exit 2 otherwise); an unknown record is exit 3.

## What a run applied

The manifest is built in `PLANNING`, stored as an artifact and referenced by the agent invocation
(`contextManifestRef`). `harness memory manifest --run <run>` prints it:

```json
{
  "executionId": "run_413a76005c1847fb983a53682d6c7d66",
  "manifestRef": "artifact://sha256/4c7495165f9b79dd6e21fc73114d1bbba47aa128f91aa0d8f4b043e8311b0fb1",
  "digest": "sha256:f9574120c7ff76dbc255165f1f12ccc051558b72beb93f3c8af01d38ec4749f3",
  "records": [
    {"memoryId": "mem_fecb…dff3", "level": "PROJECT", "key": "money.rounding",
     "value": {"text": "Round money half up to two places."}, "provenance": {"…": "human.lead"}},
    {"memoryId": "mem_a1f0…7531", "level": "RETROSPECTIVE",
     "key": "recommendation/recommendation_b382…6b09", "value": {"decision": "ACCEPT", "…": "…"}}
  ],
  "excluded": [
    {"memoryId": "mem_baa8…3457", "level": "NORMATIVE", "key": "release.freeze", "reason": "expired"},
    {"memoryId": "mem_a0c4…84b4", "level": "PROJECT", "key": "money.rounding",
     "reason": "superseded", "supersededBy": "mem_fecb…dff3"}
  ]
}
```

The `digest` covers the applied records. The manifest is a record of the run: approving or
invalidating memory later does not change what a past run reports. Command agent providers receive
the applied records under `context` (see [connecting an external agent](external-agents.md)); a
run without memory sends no `context` key.

## Deciding a retrospective recommendation

Closing a run produces a retrospective with recommendations that require human review. A person
decides each one:

```bash
harness recommendation list --run run_80dd…8c2f
harness recommendation decide --run run_80dd…8c2f --recommendation recommendation_b382…6b09 \
  --decision ACCEPT --actor human.lead \
  --rationale "No findings in this run; keep the controls as they are."   # exit 0
harness recommendation decide … --decision REJECT --rationale "…"         # exit 5: already decided
```

| Decision | Effect |
|---|---|
| `ACCEPT` | Stored as approved retrospective memory; enters the context of later runs |
| `EDIT` | Same, with the text given in `--statement`; the original text is kept |
| `REJECT` | Stored as history, expired and unapproved; never enters a context |

A recommendation takes one decision. The harness applies nothing else: rules, gates and
configuration change only through a versioned change made by a person, and
`retrospectiveAutoApply` is a locked `false` policy.

## Limits

- Memory is local to the project state (`.harness/state.db`); there is no sharing between
  repositories.
- Selection is deterministic (level, key, creation time), not semantic: the harness does not rank
  records by relevance to the task.
- The simulated provider records the manifest but does not read the records; an agent sees them
  only through a command provider that uses `context`.
- Actors are recorded, not authenticated.
