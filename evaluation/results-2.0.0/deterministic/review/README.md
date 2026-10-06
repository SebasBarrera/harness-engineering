# N02-a: the deterministic parts of the review panel on a seeded corpus

23 changes on a small layered billing project (`evaluation/corpus/review`): 16 seeded with one
defect each (quality: wrong logic, dead code, a mutable default argument a Ruff rule verifies;
architecture: a domain module importing an adapter; resilience: an external call without timeout,
a swallowed exception; tests: a tautological assertion, a mock of the subject; concurrency: shared
mutable state on a thread pool; pipeline security: a weakened CI gate, a dangerous path, an
expression injection, a predictable temporary file; instructions addressed to an agent in the
README; a credential in source; a failing consistency check) and 7 clean ones. Each case is a
feature branch reviewed against `main` with `harness review-code --mode manual`, with the
configuration `harness init` writes (`review.panel` in `enforce`), one consistency check
(`python -m compileall -q src`) and fixture reviewers that call no model
(`evaluation/deterministic/review_fixture_reviewer.py`):

1. `silent`: reviewers answer PASS without findings, so every finding comes from the harness
   itself (rules a tool verifies, its own checks, the consistency check). Detection per domain
   against the truth (same file and same line or rule) and findings on clean changes.
2. `repeat`: the same review again (global cache), with the fixture's own count of the calls it
   received (`fixtureCalls`), next to the report's `tokens.modelCalls`.
3. `oracle` (`--no-cache`): reviewers answer the seeded findings of their domain plus two decoys
   each: one at line 9999 (outside the diff) and one on a rule outside the catalog without
   evidence; the report's `droppedOutside` and `downgraded` are compared with the decoys sent.
4. `testOnly`: a test-only commit on top and the silent review again: the reviewers that ran and
   the answers that came from the per-reviewer cache.

The per-domain table also says whether the domain's reviewer would have run (`domainReviewer`,
its activation reason): a seeded defect that no tool verifies is left to a reviewer.

N02-b (real reviewers) uses the same corpus: `--reviewer-command '["python", "ADAPTER", ...]'`
(a JSON argv of a provider-protocol-1.1 command provider that answers `review` requests with the
panel's output contract), optionally `--reviewer-model`, `--provider-env NAME` and `--reps N`
(repeats step 1 without cache for variance). Without it no model is called.

Command (worktree code):

```bash
SCRATCH=/private/tmp/claude-502/-Users-jbarrerapuli-Documents-Repos-Proyecto-de-grado/ceca23e5-5679-47dc-ab0b-75aa4166d2c7/scratchpad
$SCRATCH/venvs/eval/bin/python evaluation/deterministic/review_corpus.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/review --work $SCRATCH/work/review-final
```

Files: `review-corpus.jsonl` (one record per case and step), `review-summary.json`,
`review-catalog.json` (`harness review rules show` of the corpus project), `environment.json`.
