# Contributing

Thank you for your interest. This repository holds the Governed Agent Harness, the research
artifact of a master's thesis. Contributions are welcome under two constraints that come from that
context:

1. **The evaluated cut is frozen.** `v0.8.0` reproduces the cut evaluated in the thesis byte for
   byte ([provenance](docs/provenance.md)). Tags never move.
2. **Behavior measured in the thesis does not change silently.** Defects whose fix would change
   behavior evaluated on `v0.8.0` are labeled `thesis-impact`. A fix ships in a new minor version,
   with a regression test that fails before the fix, and the changelog states how the results
   differ from the thesis. Quality, infrastructure and documentation work that does not change
   behavior is welcome at any time.

## Development setup

```bash
python3.12 -m venv .venv            # Python >= 3.12
source .venv/bin/activate
python -m pip install -e ".[dev,api]"
pre-commit install                  # optional, see below
```

Node.js LTS and npm are needed only for the Node.js end-to-end test.

## Verification commands

| What | Command |
|---|---|
| Full suite (86+ tests) | `make test` |
| Suite without performance tests | `make test-fast` |
| One family | `python -m pytest tests/unit` (also `contract`, `integration`, `security`, `e2e`, `performance`) |
| Coverage (XML, JSON, JUnit under `reports/`) | `make coverage` |
| Lint and format | `ruff check src tests scripts` and `ruff format --check src tests scripts` |
| Types | `mypy` (strict, package mode) |
| Schemas after a model change | `make schemas`, then `git diff -- schemas src/governed_harness/resources/schemas` |
| Generated references | `python scripts/gen_cli_docs.py`, `python scripts/gen_schema_docs.py` |
| Documented flows and exit codes | `python scripts/demo_flows.py all` |
| Documentation site | `pip install -r docs/requirements.txt && mkdocs build --strict` |
| Packaging | `make build` (`python -m build`) |
| Provenance of v0.8.0 | `git archive v0.8.0 \| tar -x -C /tmp/v080 && python3 /tmp/v080/scripts/verify_provenance.py full --root /tmp/v080` |

CI runs all of these; see [monitoring](docs/monitoring.md) for which checks block.

## Branches and integration

- `main`: released versions only. Every merge into `main` is tagged.
- `develop`: integration branch and default branch.
- Work branches start from `develop`: `feat/*`, `fix/*`, `docs/*`, `ci/*`, `chore/*`, `test/*`,
  `build/*`, `security/*`, `refactor/*`, `style/*`.
- Releases: `release/x.y.z` from `develop`, merged into `main`, tagged, then `main` is merged back
  into `develop`. Hotfixes: `hotfix/*` from `main`, merged into `main` and `develop`.

**Single maintainer.** The repository has one maintainer and GitHub does not allow approving your
own pull request, so the maintainer pushes the work branch, waits until **every blocking CI job on
the branch is green**, and merges it into `develop` with a merge commit (`git merge --no-ff`) whose
message summarizes the change, the verification and the issues it closes. Work branches are kept
after merging. Rulesets on `main` and `develop` block force pushes and deletion.

**External contributors** open a pull request against `develop`: the title must follow Conventional
Commits, the description must reference an issue (`Closes #n` or `Refs #n`) and state whether the
change affects behavior evaluated in the thesis (the pull-request template asks for it). With a
second maintainer, pull requests with one approval and CODEOWNERS review become mandatory.

## Commits

- [Conventional Commits](https://www.conventionalcommits.org/) in English:
  `feat(orchestration): …`, `fix(validators): …`, `docs: …`, `ci: …`, `test: …`, `build: …`,
  `refactor: …`, `style: …`, `security: …`, `chore: …`.
- Small and atomic: one purpose per commit, ideally under 300 changed lines. Explain the *why* in
  the body when it is not obvious, and reference issues (`Closes #n`).
- Formatting-only commits are listed in `.git-blame-ignore-revs`.

## Pre-commit

`.pre-commit-config.yaml` runs Ruff (check and format), whitespace and end-of-file fixers, YAML and
JSON checks, gitleaks and the privacy denylist before each commit:

```bash
pip install pre-commit && pre-commit install
```

It was introduced in v0.8.1 and deliberately not before: its automatic fixes would have altered
files of the frozen cut.

## Architectural rules

These rules come with the cut (see [ARCHITECTURE.md](ARCHITECTURE.md) and the ADRs in
[docs/adr](docs/adr)):

1. Domain and orchestration code must not import technology profiles, package managers, web
   frameworks, provider SDKs or CI vendors.
2. New stacks are added through profiles, validators or adapters; adding one must not modify core
   state semantics.
3. Every privileged operation requires an actor and capability grant.
4. Every externally visible record must have a versioned model/schema and provenance where
   applicable.
5. Mandatory non-success states fail closed.
6. Validators produce results; only the core gate engine decides the global status.
7. An agent/provider cannot approve its own output.
8. Human approvals are always digest-bound and become stale after changes.
9. Retrospective logic may propose but never apply governance changes.
10. Security claims must distinguish application-level authorization from OS-level enforcement.

## Required change evidence

A change should include:

- the rationale and the affected invariant or extension contract;
- tests for the success path and at least one failure or blocking path;
- schema regeneration when a public model changes (breaking changes need a new schema major
  version rather than silently changing historical meaning);
- documentation for new commands, profiles or security assumptions, with outputs copied from a
  real run;
- a benchmark comparison when changing event, artifact, process or gate hot paths.

## Privacy

Never commit research material: interview transcripts, personal notes, the thesis manuscript or
authoring prompts. `.github/privacy-denylist.txt` lists the forbidden paths and CI fails if one is
tracked. Report anything sensitive privately (see [SECURITY.md](SECURITY.md)).
