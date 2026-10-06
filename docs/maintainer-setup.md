# Maintainer setup

Steps that need a browser or account-level access, so automation cannot do them. Commands assume
the GitHub CLI authenticated as the repository owner.

## SonarQube Cloud (formerly SonarCloud)

The `sonarcloud` workflow skips the analysis with a visible notice until this is done.

1. Sign in to [SonarQube Cloud](https://sonarcloud.io) with GitHub.
2. Create the organization bound to the GitHub account `SebasBarrera` (free plan for public
   repositories) and import `harness-engineering`. The expected keys are the ones in
   `sonar-project.properties`: organization `sebasbarrera`, project key
   `SebasBarrera_harness-engineering`. If SonarQube Cloud assigns different keys, update that file.
3. In the project, **Administration → Analysis Method: turn off Automatic Analysis**. It is
   incompatible with analysis from CI.
4. Create a token (**My Account → Security**) and store it as a repository secret:

   ```bash
   gh secret set SONAR_TOKEN --repo SebasBarrera/harness-engineering
   ```

5. Re-run the workflow (`gh workflow run sonarcloud.yml --ref develop`), check the first analysis,
   and then add the quality-gate badge to the README.

## GitHub token used by automation

Automation uses the GitHub CLI authenticated as the repository owner. Every work branch reaches
`develop` through a pull request (see
[CONTRIBUTING.md](https://github.com/SebasBarrera/harness-engineering/blob/develop/CONTRIBUTING.md)),
so a fine-grained token restricted to this repository needs **Pull requests: Read and write** besides
Administration, Contents, Workflows, Issues, Actions, Pages and Secrets (read and write) and
Attestations, Code scanning alerts and Commit statuses (read).

## Dependabot pull requests

Dependabot opens pull requests against `develop`. Review and merge them in the web UI (or with
`gh pr merge --merge`) once their checks are green. Python version bumps of the Docker base
image should be evaluated deliberately: the image is pinned by digest to `python:3.12-slim`.

## GitHub Pages

Pages is configured with **Source: GitHub Actions** and is deployed by `pages.yml` on pushes to
`main`. The `gh-pages` branch is only a data branch for the benchmark trend written by
`benchmarks.yml`; it is not the Pages source.

## Branch rulesets

`protect-main` and `protect-develop` block deletion and non-fast-forward pushes. Work branches are
merged through pull requests once every blocking check is green. With a second maintainer, add the
`pull_request` rule (one approval, CODEOWNERS review) and make the checks listed in
[monitoring](monitoring.md) required.

## Private vulnerability reporting, secret scanning and Dependabot

Already enabled on the repository: private vulnerability reporting, secret scanning with push
protection, Dependabot alerts and security updates. Review them under **Security**.

## PyPI

Publication to PyPI is disabled. To enable it, configure a Trusted Publisher on pypi.org for this
repository and the `release.yml` workflow, then add a publish job using
`pypa/gh-action-pypi-publish` with `id-token: write`.

## Local workspace of the maintainer

The thesis manuscript (`Documento/`), the authoring prompt, the extracted cut and `.local/` live next
to the repository on the maintainer's machine and are ignored by `.gitignore`. They must never be
committed; CI enforces this with the privacy denylist.
