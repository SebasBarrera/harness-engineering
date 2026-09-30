# CLAUDE.md

Conventions for working in this repository.

## What this is

The Governed Agent Harness: a Python ≥ 3.12 package (`src/governed_harness`, Typer CLI `harness`,
FastAPI local API) that governs AI-assisted development through nine normative phases, fail-closed
gates and digest-bound human decisions. It is the artifact of a master's thesis; `v0.8.0` is the
evaluated cut, frozen byte for byte.

## Setup and checks

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev,api]"

make test                                 # full suite
python -m pytest tests/unit               # one family: unit contract integration security e2e performance
ruff check src tests scripts && ruff format --check src tests scripts
mypy                                      # strict, package mode, must report no issues
python scripts/generate_schemas.py && git diff --exit-code -- schemas src/governed_harness/resources/schemas
python scripts/gen_cli_docs.py --check && python scripts/gen_schema_docs.py --check
python scripts/demo_flows.py all          # documented flows and exit codes
mkdocs build --strict                     # needs: pip install -r docs/requirements.txt
```

If the developer's global Git config signs commits, the test fixtures are isolated from it
(`tests/conftest.py`); scripts in `scripts/` do the same.

## Rules

- **Never modify the `v0.8.0` tag or rewrite published history.** No force pushes to `main` or
  `develop` (rulesets block them).
- **Do not change behavior evaluated in the thesis.** Defects labeled `thesis-impact` stay unfixed
  until the maintainer decides; open or update the issue instead. Refactors must keep public names
  (`__all__`) and behavior; prove it with the full suite and `scripts/demo_flows.py all`.
- **Evidence before prose.** Every command, output, number or exit code written in docs or commit
  messages must come from something you ran or read. Mark anything unverified as such.
- **Fail closed.** No `|| true`, `continue-on-error`, `--exit-zero` or lowered thresholds to turn a
  blocking check green. Informational CI jobs carry `(report)` or `(non-blocking)` in their name.
- **Privacy.** Never add interview material, personal notes, the thesis manuscript (`Documento/`),
  the authoring prompt, `.env` or `.local/` (see `.github/privacy-denylist.txt`).
- **Commits:** Conventional Commits in English, small and atomic, the *why* in the body,
  `Closes #n` for issues. No commit trailers; commits and tags are not signed.
- **GitHub Actions:** pin actions by full commit SHA with a version comment, least-privilege
  `permissions`, `persist-credentials: false`, no dependency caches, `${{ github.event.* }}` only
  through `env:`. Check with `actionlint` (with shellcheck installed) and `zizmor --offline`.

## Branch flow

Work branches start from `develop` (`feat/`, `fix/`, `docs/`, `ci/`, `chore/`, `test/`, `build/`,
`security/`, `refactor/`, `style/`). Single maintainer, no pull requests: push the branch, wait until
every blocking job of every workflow on the branch head is green, then
`git merge --no-ff <branch>` into `develop` with a message that summarizes the change, the CI run
and the issues it closes, and push. Keep the branch on the remote. Releases: `release/x.y.z` →
`main` (merge commit, annotated unsigned tag `vx.y.z`, `release.yml` builds and attests the
artifacts) → merge `main` back into `develop`.

## Generated files

`docs/reference/cli.md` (`scripts/gen_cli_docs.py`), `docs/reference/task-file.md`
(`scripts/gen_schema_docs.py`) and `schemas/v1/*` plus `src/governed_harness/resources/schemas/v1/*`
(`scripts/generate_schemas.py`) are generated: edit the source and regenerate.
