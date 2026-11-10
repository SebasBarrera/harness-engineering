# Pilot on wave 9 (2026-10-06): one Haiku run per condition, brownfield

A second pilot, after wave 9 (PR #89, `fix/wave9-hardening` at `de84849`) fixed what the first
pilot (`../pilot/`) found. Same design: `launchers/pilot.sh`, model `claude-haiku-4-5-20251001`,
scenario `brownfield` (itsdangerous 2.2.0), one repetition per condition, one run at a time:
complete prompt x {`direct`, `harness-core`, `harness`, `harness-anchored`} and the casual prompt
under `harness` with the simulated product owner. Five runs, none cut (the spare run allowed for a
cut run was not used). Code: the worktree source of the runner branch at `198a6a8` (wave 9
merged; `harness --version` prints `harness 1.0.0`, 2.0.0 is not tagged), recorded in
`environment.json`; Claude Code 2.1.287.

Note on the direct run: the launcher was first started at `5dfc82e` (wave 9 at `b7d39e6`); it was
stopped during the direct run, when wave 9 gained `de84849`, and started again at `198a6a8`. The
direct run (Claude Code alone, no harness) finished and was kept; the evaluation code copy has the
same digest in both starts (`codeDigest` `b8f610a2b8271078`), and every governed run ran on the
`198a6a8` harness.

Command (account checked before the launch and again by the launcher; the launcher ran from an
export of the committed `evaluation/` tree so that the worktree could change branch meanwhile):

```bash
EVAL_PYTHON=<worktree>/.venv/bin/python EVAL_EXTRA_PATH=<worktree>/src:<tools site-packages> \
EVAL_CACHE=<dir with itsdangerous-2.2.0.tar.gz> EVAL_CORE_REFERENCE_SRC=<harness 1.0.0 src> \
EVAL_DRY=0 EVAL_OUT=evaluation/results-2.0.0/pilot-wave9 EVAL_WORK=<work dir> \
  bash <export>/evaluation/launchers/pilot.sh
```

A dry run of the same launcher (`EVAL_DRY=1`, fake CLI) recorded the five runs before it.

Files: `pilot.jsonl` (5 records), `summary-blocks.json` (`report.py --v2`), `environment.json`. The
run directories and logs stay outside the repository.

## Results (every number from `pilot.jsonl`)

| prompt | condition | outcome | final phase / status | reached IMPLEMENTATION | reached DECISION | calls governance / implement | agent USD | product owner USD | wall s | hidden tests |
|---|---|---|---|---|---|---|---|---|---|---|
| full | direct | delivered | - | yes | - | 0 / 1 | 0.226347 | - | 158.195 | 24/24 |
| full | harness-core | approved | CLOSURE / PASSED (gate PASSED) | yes | yes | 0 / 1 | 0.161907 | - | 153.881 | 24/24 |
| full | harness | stopped | INTENT / BLOCKED | no | no | 1 / 0 | 0.106104 | - | 82.313 | 18/24 (baseline) |
| full | harness-anchored | stopped | VERIFICATION / FAILED | yes | no | 6 / 3 | 1.971764 | 0.013992 | 1355.714 | 24/24 (quarantine copy) |
| casual | harness | stopped | VERIFICATION / FAILED | yes | no | 4 / 3 | 1.387260 | 0.012166 | 950.871 | 24/24 (quarantine copy) |

18/24 is what the unchanged baseline passes; "quarantine copy" is the run's own change, measured on
a copy of the workspace with the patch that stop the line quarantined.

Wave 9 behaviour observed in the governed runs:

- Provider grant (#87): every governed record has `grantCheck.providerWarnings` empty; harness-core
  kept only 1.0.0 keys (`coreCheck.extraKeys` `[]`) and the same configuration digest as 1.0.0
  (`coreCheck.digestEqual` true) with the grant in `capabilities.grants`.
- Converging clarification (#79): the anchored and the casual runs left INTENT after one answered
  round (6 and 7 questions); in the first pilot two runs stopped in INTENT after three rounds.
- Inbox (#73): every wait the person answered before DECISION (architecture rules, acceptance
  tests, plan approval) was read from the inbox (`source: inbox`), and `plan decide
  --no-continue` exited 0 (#83).
- Anchored routing (#85): the 9 routed calls of the anchored run ran on Haiku with `anchor`
  Haiku (the ceiling).
- Neither run that reached IMPLEMENTATION needed the harness's read-only retry (#80): no
  `agent.call.contract-retry` event.

## Why the governed runs did not reach DECISION

- `harness` (complete prompt): the first read-only call (`clarify`, INTENT) answered with valid
  questions, but Haiku also wrote `test_current_behavior.py` in the workspace with its Write tool
  (its two Bash attempts were denied). The harness undid the write, recorded a HIGH
  `agent.read-only-violation`, discarded the answer and blocked INTENT; the contract retry (#80)
  does not cover a call that changed the workspace. The evaluation adapter gives read-only calls
  the direct condition's tools (Write and Edit included), as the built-in `claude-code` adapter
  does (`--permission-mode acceptEdits` for every call but the panel's isolated reviewers).
- `harness-anchored` (complete prompt): IMPLEMENTATION ran three times (the change and the two
  corrections of `runtime.verificationCorrections`); VERIFICATION still failed on
  `harness.acceptance-tests`: 4 frozen acceptance tests failed. The tests Haiku proposed in
  SPECIFICATION are wrong: one expects `base64_decode("SGVsbG8") == b"Hellon"` (it is `b"Hello"`),
  three expect a value with three trailing `=` to decode, which the clarified task itself says must
  be rejected. The simulated person approved them: its rule checks that the proposal has files with
  path and content under the shown digest, not what the tests assert. The change passed 24 of 24
  hidden tests.
- `harness` (casual prompt): VERIFICATION failed after two corrections on
  `harness.acceptance-tests` (HIGH `acceptance.modified`: the implementing agent rewrote the three
  frozen acceptance files with its own tests) and `harness.layers` (HIGH
  `architecture.layer-violation`: "encoding may depend on no other layer" for
  `from .exc import BadData`, an import the baseline's `encoding.py` already has; the layer rules
  were inferred by the Haiku survey and approved by the simulated person), plus a HIGH
  `ratchet.regressed` (two E501 lines in acceptance files). The change passed 24 of 24 hidden tests.

## Compared with the first pilot (`../pilot/pilot.jsonl`, same model, scenario and prompts)

| prompt | condition | first pilot: outcome, final phase | calls gov/impl | agent USD | wave 9 pilot: outcome, final phase | calls gov/impl | agent USD |
|---|---|---|---|---|---|---|---|
| full | direct | delivered, 24/24 | 0/1 | 0.258259 | delivered, 24/24 | 0/1 | 0.226347 |
| full | harness-core | approved, CLOSURE | 0/1 | 0.244589 | approved, CLOSURE | 0/1 | 0.161907 |
| full | harness | stopped, SPECIFICATION | 4/0 | 0.479146 | stopped, INTENT | 1/0 | 0.106104 |
| full | harness-tiered / harness-anchored | stopped, INTENT | 8/0 | 0.540783 | stopped, VERIFICATION | 6/3 | 1.971764 |
| casual | harness | stopped, INTENT | 6/0 | 0.675645 | stopped, VERIFICATION | 4/3 | 1.387260 |

In the first pilot no governed run with the init configuration reached IMPLEMENTATION; in this
one two of three did, and none reached DECISION. One run per cell: these are observations of a
pilot, not estimates.
