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

The thesis documents the cut released as [`v0.8.0`](https://github.com/SebasBarrera/harness-engineering/releases/tag/v0.8.0),
which reproduces it byte for byte (see [provenance](provenance.md)), and runs its
[controlled evaluation](evaluation/results.md) on `v0.9.0`. `v0.8.1` adds
infrastructure, documentation and quality fixes without behavior change. `v0.9.0` fixes seven
behavioral defects; the only difference visible in the thesis cases is the brownfield closure, which
is now a plain `APPROVE` instead of an `APPROVE_EXCEPTION` (see the
[brownfield guide](guides/brownfield.md)). `v1.0.0` adds, after the evaluation, governed memory
operations, decisions on retrospective recommendations and the usage reported by agent providers
(see [memory and retrospective decisions](guides/memory.md)); with no memory records the run path
is the evaluated one. The remaining defects are tracked in the milestone
*backlog — thesis-impact*.

## Snapshot reported for the evaluated cut

The thesis reports these figures for `v0.8.0` (they describe the frozen cut, not the current
state of `develop`):

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
| Governed-process overhead | 10.09 % and 11.23 % in two runs, about 1.2 ms |
| Exit codes | 0, 1, 2, 3, 4, 5, 6, 130 |
| Brownfield case | `pallets/itsdangerous` 2.2.0; baseline broken by a missing `freezegun`; 298 tests after installing it; ChangeSet digest invariant |

The re-verification of these figures during publication is recorded in the
[`v0.8.0` release notes](https://github.com/SebasBarrera/harness-engineering/releases/tag/v0.8.0).

## How to cite

Use the citation metadata in [`CITATION.cff`](https://github.com/SebasBarrera/harness-engineering/blob/develop/CITATION.cff)
(GitHub shows a "Cite this repository" button), and cite the version you used, for example
`v0.8.0` for the documented cut or `v0.9.0` for the controlled evaluation.
