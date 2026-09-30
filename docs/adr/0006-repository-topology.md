# ADR 0006: Repository topology

- Status: Accepted
- Date: 2026-09-29

## Context

Two products come out of the research: the software (the Governed Agent Harness) and the thesis
document (LaTeX) that designs, implements and evaluates it. They differ in:

- **life cycle**: the software keeps evolving after the evaluated cut; the document follows the
  academic calendar;
- **visibility**: the software can be public; the manuscript is under institutional review and
  must not be published before it is approved;
- **license**: the software is Apache-2.0; an academic work must not end up under Apache-2.0;
- **CI**: Python tests, packaging and supply-chain controls versus a LaTeX build, word limits and
  citation checks;
- **privacy**: the research material behind the thesis (interview transcripts, notes, authoring
  prompts) must never be published in any repository.

## Decision

Two repositories, one per product:

1. **`SebasBarrera/harness-engineering`** (this repository): the software, public, Apache-2.0,
   default branch `develop`, releases on `main`.
2. **The thesis document**: a separate, private repository. It is **deferred** by decision of the
   author: for now the manuscript stays on the author's machine, outside Git, next to this
   repository (`Documento/`, ignored by `.gitignore` and blocked by the privacy denylist in CI).

A monorepo was rejected because it would force one visibility and one license on both products
and mix two unrelated CI pipelines. Public visibility for the code also gives, at no cost, code
scanning, secret scanning with push protection, branch rulesets, GitHub Pages and SonarQube Cloud.

## Consequences

- The code repository links to the thesis by its title and institution (see
  [thesis context](../thesis.md)); it never contains the manuscript, its figures or its sources.
- Privacy is enforced mechanically: `.github/privacy-denylist.txt` and `scripts/check_denylist.py`
  fail CI if a denylisted path is committed, and gitleaks scans the whole history.
- When the thesis repository is created, it gets its own CI (LaTeX build, word limits, citation
  checks, privacy denylist) and links back to this repository and to the `v0.8.0` tag.
- The repository has a single maintainer, who integrates work branches into `develop` with merge
  commits and no pull requests; CI must be green on the branch before it is merged. Rulesets block
  force pushes and deletion of `main` and `develop`. With a second maintainer, the flow moves to
  pull requests with required reviews and CODEOWNERS.
