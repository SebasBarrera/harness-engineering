# Verification ladder

A passing test suite says that the tests pass, not that each acceptance criterion holds the way
a person needs it to. Since 1.1 (issue #55) each criterion can declare the rung of evidence it
requires, and the harness certifies the ChangeSet against it from recorded evidence only:

| Rung | Means | Reached by |
|---|---|---|
| `L0` | Static | The mandatory validators of the gate passed. |
| `L1` | Unit | A passing test names the criterion. |
| `L2` | Integration with the repository's own doubles | A passing test under an `integration` directory names it, or an L2 probe. |
| `L3` | Executable behaviour | A probe runs the program (a CLI, a script, a local server, a simulator test) and its assertions hold. |
| `L4` | External environment | CI, staging or a device lab: evidence attached after the run, or an L4 probe. |
| `L5` | Human | A person checks it and ticks it in DECISION. |

Every setting is optional; `harness init` writes them (see
[the configuration reference](../reference/configuration.md#verification-ladder)). This guide
follows the `ladder` flow of `scripts/demo_flows.py`, which runs every command below in CI with
the exit codes shown.

## Declare the rungs

```yaml
taskId: task_ladder
title: Discount at the threshold, certified
intent: Apply a percentage discount only when the subtotal reaches the threshold.
acceptanceCriteria:
  - criterionId: ac_unit
    text: apply_discount(100, 100, 0.1) returns 90.
    verification: {level: L1}
  - criterionId: ac_cli
    text: The command line prints the discounted total as JSON.
    verification: {level: L3, probe: cli}
  - criterionId: ac_e2e
    text: The checkout flow shows the discount end to end.
    verification: {level: L4, deferred: CI job e2e}
  - criterionId: ac_look
    text: The receipt shows the discount line.
    verification: {level: L5, manual: The receipt layout shows the discount line}
probes:
  - id: cli
    command: [python, -m, sample.cli, "{amount}"]
    output: json
    variants:
      - {name: below, values: {amount: "50"}, env: {PYTHONPATH: src}}
      - {name: above, values: {amount: "200"}, env: {PYTHONPATH: src}}
    assertions:
      - {kind: exitCode, equals: 0}
      - {kind: jsonPath, path: "$.total", present: true}
      - {kind: differs, path: "$.total"}
```

`ac_unit` reaches `L1` through the test `test_ac_unit_at_threshold` the change adds (its name
contains the criterion id). `ac_cli` reaches `L3` through the probe: two variants, an exit code,
a JSON path and a value that must differ between them. `ac_e2e` can only be verified by CI and
`ac_look` only by a person.

## Run, review, decide

```bash
harness run start --task task_ladder                 # exit 4: waits for a decision
harness verification show --run RUN                  # plan, preflight PARTIAL, certification PARTIAL
harness gate decide --run RUN --decision APPROVE --change-set-digest DIGEST \
  --actor human.reviewer --rationale "Checked"       # exit 5: ac_look is not ticked
harness gate decide --run RUN --decision APPROVE --change-set-digest DIGEST \
  --actor human.reviewer --rationale "Checked" --check ac_look   # exit 0: the run closes
```

PLANNING records the verification plan before anything changes: for each criterion the rung it
requires, the rungs this machine can reach (the profile's capabilities, the probes run on a
scratch copy of the baseline) and why. Here the preflight is `PARTIAL`: `ac_e2e` and `ac_look`
can only be reached after the run or by a person. After VERIFICATION the certification is
`PARTIAL` for the same reason, and `harness review` lists it per criterion together with the
checklist and the deferred item.

## Close the deferred item with CI evidence

At CLOSURE the pending item `D-ac_e2e` is bound to the closure commit. A CI job attaches its
JUnit report:

```bash
harness evidence attach --run RUN --item D-ac_e2e --file e2e.xml --actor human.ci   # exit 0
harness verification show --run RUN                  # certification CERTIFIED
```

A failing report closes the item as `FAILED` (the criterion is not certified), a report about
another commit is refused, and an item past `deferredExpiryDays` cannot be closed (the inbox
warns before it expires).

## When a rung is not reached

A task whose criterion declares `L1` while no test names it stops in VERIFICATION with a `HIGH`
`certification.level-not-reached` finding (`harness run start` exits 6; with a command provider
the correction loop sends the finding to the agent). The certification is `NOT_CERTIFIED`.

A task whose probe cannot run on this machine (its executable is missing, refused or too slow)
makes the preflight `UNAVAILABLE`: PLANNING stops before any change (exit 6) and the inbox lists
it. A person either fixes the environment and continues, or decides:

```bash
harness verification decide --run RUN --continue-uncertified --actor human.lead \
  --rationale "No device here; the device lab verifies it"    # exit 4
```

The criterion ends `WAIVED` and is never certified; the decision is on the run's record.

## What else the settings do

- **Light mutation** reverts each changed block of the source in a scratch copy and runs the
  relevant tests: a change no test notices is a `tests.change-not-exercised` finding; new tests
  that pass without the change are `tests.weak`.
- **Operational contract**: INTENT records what was agreed (objective, examples, scope,
  definition of done, verification level, branch, push, pull request, comment, coverage) and,
  when it asks questions anyway, puts the open items in the same message: one clarification
  request carries the deterministic questions (`C0`-`C3`, `T1`), the agent review (`A1`, `A2`),
  the project setup questions (`P1`, `intake.projectSetup`), the localisation questions (`A3`)
  and the contract.
- **Interruption budget**: the human interactions of a run are counted against a target and the
  stop conditions are recorded.
- **Localisation**: a read-only `locate` call for M and L tasks, once per task revision, on the
  cheapest rung of the router, tells the agent where to intervene.
- **Environment preflight**: tools, variables, hooks, a dirty tree and the baseline are checked
  in DISCOVERY.
- **Worktree isolation**: `harness run start --isolate worktree` runs in a worktree of its own on
  a new branch; a collision blocks, nothing is reset.
- **Run registry**: under `runtime.stateDir: auto` the state lives outside the workspace;
  `harness registry` lists the runs of several repositories.
- **Complete delivery**: the change is staged, or pushed with the hooks and proposed as a pull
  or merge request on the project's [forge](forges.md) with a comment when the run is not clean,
  as the contract authorises.
- **`harness config lint`** reports what agent instruction files say against the configuration.

## Where each setting is tested

| Setting | Tests |
|---|---|
| Ladder, probes, preflight, deferred items, mutation, checklist, attachments, extended profiles | `tests/integration/test_verification_ladder.py`, `tests/unit/test_ladder_building_blocks.py` |
| Operational contract, interruptions, locate | `tests/integration/test_intake_contract.py` |
| Environment, run registry, isolation, delivery, config lint | `tests/integration/test_delivery_hygiene.py` |
| The defaults `harness init` writes | the `ladder` flow of `scripts/demo_flows.py` |
