# Forges and CI templates

Since 2.0 (#56) the harness proposes and reports a governed change on GitHub, GitLab, Bitbucket,
Azure DevOps and Gitea (or Forgejo) through one interface. Nothing is decided on a forge: the
comment, the quality report and the commit status inform the reviewers, and the approval is the
run's digest-bound decision, which `harness verify-approval` checks in CI.

## Which forge

The forge is detected from the `origin` remote; `delivery.forge` or `--forge` override it.

| Remote host | Forge | REST API base | Token variable |
|---|---|---|---|
| `github.com` (or a host starting with `github.`) | `github` | `https://api.github.com` (GitHub Enterprise: `https://HOST/api/v3`) | `GITHUB_TOKEN` |
| `gitlab.com`, or a host with `gitlab` in its name | `gitlab` | `https://HOST/api/v4` | `GITLAB_TOKEN` |
| `bitbucket.org` | `bitbucket` | `https://api.bitbucket.org/2.0` | `BITBUCKET_TOKEN` |
| `dev.azure.com`, `ssh.dev.azure.com`, `*.visualstudio.com` | `azure-devops` | `https://dev.azure.com/ORGANIZATION` | `AZURE_DEVOPS_TOKEN` |
| `codeberg.org`, or a host with `gitea` or `forgejo` in its name | `gitea` | `https://HOST/api/v1` | `GITEA_TOKEN` |

A self-hosted forge on another host needs `delivery.forge.kind` (and `repository` when there is
no `origin`). `harness pr forge` shows what the workspace resolves to and calls nothing.

```yaml
delivery:
  forge:
    kind: gitlab                 # auto (default), github, gitlab, bitbucket, azure-devops, gitea
    transport: api               # api (HTTPS, token from tokenEnv) or cli (gh or glab)
    repository: team/shop        # default: from origin
    apiUrl: https://code.example.com/api/v4
    tokenEnv: CI_FORGE_TOKEN     # default: per forge, see the table
    baseBranch: main             # target of harness pr create
    labels: [governed]
    template: .gitlab/merge_request_templates/Default.md
    draft: false
    codeQuality: true
```

Tokens are read only from the environment variable `tokenEnv`, never from `project.yaml` and
never logged or recorded. The `cli` transport uses the forge's own CLI (`gh api`, `glab api`)
with the authentication it already has; Bitbucket, Azure DevOps and Gitea use `api`.

## Commands

| Command | What it does |
|---|---|
| `harness pr publish --pr N` | One comment per run on the pull or merge request with the decision brief, updated when published again (marker `governed-harness:RUN`), and the findings as the forge's quality report. Without `delivery.forge` and with a GitHub `origin` it is the GitHub publisher of 1.1 unchanged. |
| `harness pr create [--base B] [--label L]... [--draft]` | A pull or merge request from the closure branch of the run (`delivery.branch`, default `harness/RUN`): the title is the task title, the description is the repository's pull request template followed by the decision brief, the labels are `delivery.forge.labels` plus `--label`. Push the branch first. Recorded as a `delivery.pull-request.created` event. |
| `harness pr status --commit SHA` | The commit status `governed-harness`: `success` once the run closed approved, `pending` while it waits for a decision, `failure` otherwise. |
| `harness pr forge` | The resolved forge, API URL, transport and token variable. |
| `harness trace --format codequality` | The findings as a GitLab Code Quality report, with the same fingerprints as the SARIF report. |

Complete delivery at CLOSURE (#55, `delivery.push`, `delivery.pullRequest.create` and
`delivery.comment`, when the task's operational contract authorises them) goes through the same
forge: after the push it creates the pull or merge request as `harness pr create` does (base
`delivery.forge.baseBranch`, else the remote's default branch) and posts the run's comment as
`harness pr publish` does, without the quality report. See
[complete delivery](../reference/configuration.md#complete-delivery).

What each forge receives as the quality report:

| Forge | Report |
|---|---|
| GitHub | SARIF uploaded to code scanning (`code-scanning/sarifs`). |
| GitLab | None through the API: GitLab reads `gl-code-quality-report.json` from the pipeline (`artifacts:reports:codequality`), see the template below. |
| Bitbucket | A Code Insights report on the commit with one annotation per finding (at most 100). |
| Azure DevOps | None through the API: publish `harness.sarif` as the `CodeAnalysisLogs` build artifact. |
| Gitea | None. |

The pull request templates looked for, in order, when `template` is not set:
`.github/pull_request_template.md` (GitHub), `.gitlab/merge_request_templates/Default.md`
(GitLab), `PULL_REQUEST_TEMPLATE.md` (Bitbucket), `.azuredevops/pull_request_template.md`
(Azure DevOps) and `.gitea/pull_request_template.md` (Gitea), among others listed in
`governed_harness.forges.TEMPLATE_CANDIDATES`.

The providers are covered by `tests/unit/test_forges.py` (requests of every provider through a
recording fake transport) and `tests/integration/test_forge_delivery.py`; the `gitlab` flow of
`scripts/demo_flows.py` runs the commands against a local fake GitLab server. They have not been
run against the live forges.

## CI templates

The templates verify, on a pull or merge request, that the merged tree is a ChangeSet a person
approved: the run's evidence bundle (`harness export --bundle`) is kept on a branch
`harness-evidence` that is never merged, `harness verify --bundle` checks it and
`harness verify-approval` recomputes the ChangeSet digest of the range and requires an approval
bound to it (see [CI integration](ci-integration.md)). They show the commands; they have not
been run as part of this repository's CI, so adapt the install step and the branch names.

### GitHub Actions

```yaml
name: approved-change

on:
  pull_request:

permissions:
  contents: read

jobs:
  verify-approval:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          fetch-depth: 0
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - name: Install the harness
        run: python -m pip install governed-agent-harness  # pin the version you use
      - name: Verify that the merged tree is an approved ChangeSet
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          git fetch --no-tags origin harness-evidence
          run_id=$(git log --format='%(trailers:key=Harness-Run,valueonly)' "origin/${BASE_REF}..HEAD" | sed -n '/./{p;q}')
          git show "origin/harness-evidence:${run_id}.tar.gz" > approval.tar.gz
          harness verify --bundle approval.tar.gz
          harness verify-approval --base "origin/${BASE_REF}" --head HEAD \
            --bundle approval.tar.gz --no-workspace
```

### GitLab CI

The second job attaches the Code Quality report of a governed run kept in the evidence branch;
GitLab shows it in the merge request widget.

```yaml
stages: [verify]

verify-approval:
  stage: verify
  image: python:3.12
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - python -m pip install governed-agent-harness  # pin the version you use
    - git fetch --no-tags origin harness-evidence "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - run_id=$(git log --format='%(trailers:key=Harness-Run,valueonly)' "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME..HEAD" | sed -n '/./{p;q}')
    - git show "origin/harness-evidence:${run_id}.tar.gz" > approval.tar.gz
    - git show "origin/harness-evidence:${run_id}.codequality.json" > gl-code-quality-report.json
    - harness verify --bundle approval.tar.gz
    - harness verify-approval --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --head HEAD --bundle approval.tar.gz --no-workspace
  artifacts:
    when: always
    reports:
      codequality: gl-code-quality-report.json
```

Where the run happened, `harness trace --run RUN --format codequality --output
RUN.codequality.json` writes the report kept next to the bundle.

### Jenkins

```groovy
pipeline {
  agent { docker { image 'python:3.12' } }
  options { timeout(time: 10, unit: 'MINUTES') }
  stages {
    stage('Verify approval') {
      when { changeRequest() }
      steps {
        sh '''
          python -m pip install governed-agent-harness  # pin the version you use
          git fetch --no-tags origin harness-evidence "$CHANGE_TARGET"
          run_id=$(git log --format='%(trailers:key=Harness-Run,valueonly)' "origin/$CHANGE_TARGET..HEAD" | sed -n '/./{p;q}')
          git show "origin/harness-evidence:${run_id}.tar.gz" > approval.tar.gz
          harness verify --bundle approval.tar.gz
          harness verify-approval --base "origin/$CHANGE_TARGET" --head HEAD --bundle approval.tar.gz --no-workspace
        '''
      }
    }
  }
}
```

### Azure Pipelines

```yaml
trigger: none
pr:
  branches:
    include: ["*"]

pool:
  vmImage: ubuntu-latest

steps:
  - checkout: self
    fetchDepth: 0
    persistCredentials: false
  - task: UsePythonVersion@0
    inputs:
      versionSpec: "3.12"
  - script: |
      python -m pip install governed-agent-harness  # pin the version you use
      target="${SYSTEM_PULLREQUEST_TARGETBRANCH#refs/heads/}"
      git fetch --no-tags origin harness-evidence "$target"
      run_id=$(git log --format='%(trailers:key=Harness-Run,valueonly)' "origin/$target..HEAD" | sed -n '/./{p;q}')
      git show "origin/harness-evidence:${run_id}.tar.gz" > approval.tar.gz
      harness verify --bundle approval.tar.gz
      harness verify-approval --base "origin/$target" --head HEAD --bundle approval.tar.gz --no-workspace
    displayName: Verify that the merged tree is an approved ChangeSet
```

In every template the job fails (exit 5 or 6) when no approval in the bundle is bound to the
digest of the range; a human decision is never made in CI.
