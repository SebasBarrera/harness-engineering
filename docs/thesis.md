# Thesis context

This software is the artifact of a master's thesis:

> **Diseño y evaluación de una arquitectura de harness engineering para el desarrollo de software
> asistido por inteligencia artificial, con supervisión humana y retrospectiva basada en
> evidencia.**
> Juan Sebastián Barrera Pulido. Trabajo de grado, Maestría en Informática, Escuela Colombiana de
> Ingeniería Julio Garavito, Bogotá, Colombia.

The thesis asks to what extent a harness engineering architecture based on normative phases,
verifiable gates, controlled memory, human supervision and evidence-based retrospective improves
the quality, traceability and control of AI-assisted software development in greenfield and
brownfield scenarios. It follows Design Science Research: the harness is the designed artifact,
and its evaluation is formative and descriptive (no statistical generalization).

## Evaluated versions

The thesis keeps chapters 1 to 4 of its manuscript; chapters 5 to 8 are rewritten with the results
of `2.0.1`, the version of the [controlled evaluation](evaluation/results.md) (campaign in
progress, `evaluation/campaign/`). [`v0.8.0`](https://github.com/SebasBarrera/harness-engineering/releases/tag/v0.8.0)
is an earlier, obsolete Design Science Research iteration: it stays documented and reproducible
byte for byte (see [provenance](provenance.md)), its tag is never moved, and its behavior may be
changed in new versions. Results of earlier versions are in the git history and the `v0.8.0` tag
and are not part of the `2.0.1` analysis. `v0.8.1` adds
infrastructure, documentation and quality fixes without behavior change. `v0.9.0` fixes seven
behavioral defects; the only difference visible in the thesis cases is the brownfield closure, which
is now a plain `APPROVE` instead of an `APPROVE_EXCEPTION` (see the
[brownfield guide](guides/brownfield.md)). `v1.0.0` adds, after the evaluation, governed memory
operations, decisions on retrospective recommendations and the usage reported by agent providers
(see [memory and retrospective decisions](guides/memory.md)); with no memory records the run path
is that of the earlier iteration. The defects labeled `thesis-impact` are no longer
held back: they may be fixed in new versions.

The controlled evaluation installs the harness from the wheel built from its release tag, not from
a source tree, so the version under test is the one each `environment.json` records (with the
wheel's SHA-256 and the commit). The deterministic suites run on GitHub Actions; the runs with a
model run on the author's Windows PC, declared as such. Pilot and precheck attempts made before
the campaign are apparatus checks and are listed, with their cause, in the preregistration
(`evaluation/campaign/PREREGISTRO.md`, amendment of 2026-11-12).

## Snapshot of the earlier iteration (v0.8.0)

The figures of the earlier iteration `v0.8.0`, reported by its thesis manuscript (they describe
that frozen cut, not the current state of `develop` nor the `2.0.1` evaluation):

| Fact | Value |
|---|---|
| Python modules in `src/governed_harness` | 74, with 6,746 lines |
| JSON Schema contracts | 25 |
| CLI commands | 21 |
| API routes | 9 (seven queries, one decision POST, the dashboard at `/`) |
| Tests | 86: core 79, E2E Python 4, E2E Node.js 1, performance 2 |
| Full suite in one process | exit 0 in three runs, 19–21 s |
| Core coverage | lines 80.60 %, branches 59.29 % (77.48 % combined), 3,268 statements |
| Ruff 0.16.5 | 140 findings in `src`, 11 in `tests` (60 F401, 40 B008) |
| Mypy strict | package mode blocked by the missing `py.typed`; 54 errors in 15 files on sources |
| Wheel in a clean environment | installs its runtime dependencies (18 counted during publication) and `harness` runs |
| Exit codes | 0, 1, 2, 3, 4, 5, 6, 130 |
| Brownfield case | `pallets/itsdangerous` 2.2.0; baseline broken by a missing `freezegun`; 298 tests after installing it; ChangeSet digest invariant |

The re-verification of these figures during publication is recorded in the
[`v0.8.0` release notes](https://github.com/SebasBarrera/harness-engineering/releases/tag/v0.8.0).

## How to cite

Use the citation metadata in [`CITATION.cff`](https://github.com/SebasBarrera/harness-engineering/blob/develop/CITATION.cff)
(GitHub shows a "Cite this repository" button), and cite the version you used, for example
`v0.8.0` for the earlier, documented iteration or `v2.0.1` for the controlled evaluation.
