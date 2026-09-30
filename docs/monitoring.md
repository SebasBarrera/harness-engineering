# Repository monitoring

Where each health signal of this repository is observed, and what to do when it turns red. For
the process metrics of governed runs, see [metrics](metrics.md).

| Signal | Where | Produced by | What to do when it alerts |
|---|---|---|---|
| Tests (CPython 3.12–3.14 Ubuntu, 3.12 macOS) | Actions → `ci`; job summary per test family | `ci.yml` | Reproduce with `make test`; a red blocking job must be fixed before merging. Windows is informational (not verified by the thesis). |
| Coverage | `test-reports` artifact of `ci` (XML and HTML); Pages `/coverage/`; SonarQube Cloud | `ci.yml`, `pages.yml`, `sonarcloud.yml` | Coverage is reported, not gated; investigate drops in the modules you touched. |
| Lint, types, workflows, data files | Actions → `lint` | `lint.yml` | Ruff, mypy, actionlint, zizmor and YAML/JSON validation are blocking; fix locally with `ruff check --fix`, `ruff format`, `mypy`. |
| Packaging | Actions → `build` | `build.yml` | Wheel content or clean-install smoke test failed: check `pyproject.toml` package data. |
| README quickstart and exit codes | Actions → `docs-smoke` | `docs-smoke.yml` | The documented commands no longer behave as written: update the docs or fix the regression. Also fails when the generated CLI or task references are stale. |
| Secrets | Actions → `security` (`secrets`); Security → Secret scanning | gitleaks, GitHub secret scanning with push protection | Rotate the credential first, then remove it from history with the maintainer; never force-push `main` or `develop` without an explicit decision. |
| Dependency vulnerabilities | Actions → `security` (`dependency audit`); Security → Dependabot | pip-audit, Dependabot alerts and updates | Take the Dependabot update, or pin/upgrade the dependency; document accepted risks in the issue. |
| Static security analysis | Security → Code scanning (categories `bandit`, `trivy`, CodeQL, `scorecard`) | `security.yml`, `docker.yml`, `codeql.yml`, `scorecard.yml` | Triage each alert: fix, or dismiss with a written reason in the alert. Bandit medium+ and fixable critical image vulnerabilities fail the build. |
| Provenance of v0.8.0 | Actions → `security` (`provenance (v0.8.0 tag)`) | `scripts/verify_provenance.py` | The tag no longer matches the thesis cut: stop and investigate; the tag must never move. |
| Privacy denylist | Actions → `security` (`privacy`) | `scripts/check_denylist.py` | A denylisted path was committed: remove it and, if it contained personal data, treat it as an incident. |
| Supply-chain posture | OpenSSF Scorecard badge and code scanning | `scorecard.yml` (main, weekly) | Review the failing checks; most are addressed by branch protection, pinned actions and token permissions. |
| Code quality gate | SonarQube Cloud project dashboard | `sonarcloud.yml` (needs `SONAR_TOKEN`) | New issues on changed code should be fixed before release. |
| Benchmarks | Pages `/benchmarks/` (report) and `/benchmarks/trend/` (history on `gh-pages`) | `benchmarks.yml` | A >150 % regression comments on the commit; rerun before acting, runners are noisy. |
| Links | Actions → `links` (weekly) | lychee | Update or remove the broken link. |
| Stale issues | Issues with label `stale` | `stale.yml` | Items labeled `thesis-impact`, `needs-decision` or `priority:high` are never marked stale. |

## Branch integration

The repository has a single maintainer, who merges work branches into `develop` with merge commits
after their CI is green (see [CONTRIBUTING.md](https://github.com/SebasBarrera/harness-engineering/blob/develop/CONTRIBUTING.md)).
Rulesets block force pushes and deletion of `main` and `develop`.
