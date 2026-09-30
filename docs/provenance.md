# Provenance of the thesis cut

The tag `v0.8.0` reproduces the cut of the Governed Agent Harness that the master's thesis
designed, implemented and evaluated. This page records how that fidelity is established and
checked, and every exception to it.

## Reference

| Item | Value |
|---|---|
| Cut | `governed-agent-harness-0_8_0-current.zip` |
| ZIP SHA-256 recorded in the thesis | `7e1401ad9f324a9e9fe488b5eb4d9c842f3f8ec52ec7340e5913813440b4d0cd` |
| Files in the extracted cut | 236 (macOS `.DS_Store` metadata excluded) |
| Per-file manifest | [`provenance/v0.8.0.sha256`](https://github.com/SebasBarrera/harness-engineering/blob/develop/provenance/v0.8.0.sha256) |
| Verifier | [`scripts/verify_provenance.py`](https://github.com/SebasBarrera/harness-engineering/blob/develop/scripts/verify_provenance.py) |

The ZIP archive itself was no longer available when this repository was published, so its SHA-256
could not be recomputed. Fidelity is anchored to the SHA-256 of each of the 236 extracted files,
recorded before the import started.

## How the import was done

- 15 work branches imported the cut bottom-up along the internal import graph (packaging and package
  root, domain, evidence and events, configuration and resources, storage and capabilities, runtime,
  gates and validators, agents and plugins, memory/telemetry/retrospective/reporting,
  orchestration and application, CLI/API/benchmark, end-to-end and performance tests, examples and
  Docker, documentation and reports, the original CI workflow). Tracking issue: #21.
- Every file was copied byte for byte. No formatting, line-ending, whitespace or import-order change
  was applied during the import; `.gitattributes` keeps LF endings, which is what the cut uses.
- CI verified each branch against the manifest (`verify_provenance.py partial`: every imported file
  must match) before it was merged, and every branch kept the package installable and its tests
  green.
- Paths that must stay local (the thesis manuscript, interview material, authoring prompts, `.env`)
  were excluded through `.git/info/exclude` until `v0.8.0`, so that the cut's `.gitignore` stayed
  identical, and through `.gitignore` afterwards. `scripts/check_denylist.py` checks every commit.

## Exceptions

| Path in the cut | Treatment | Reason |
|---|---|---|
| `dist/governed_agent_harness-0.8.0-py3-none-any.whl` | Attached to the `v0.8.0` release | Binary build artifact |
| `dist/governed_agent_harness-0.8.0.tar.gz` | Attached to the `v0.8.0` release | Binary build artifact |
| `.coverage.release` | Attached as `coverage.release.sqlite` | Binary coverage database |
| `dist/.gitignore` | Not versioned | One-byte file inside an ignored directory |
| `.github/workflows/ci.yml` | Versioned unchanged, then **disabled through the Actions API** | It ran on every push, used mutable action tags and failed on Python ≥ 3.12 (`setup.py` without setuptools). Disabling it avoided changing its bytes (#15). |

`v0.8.0` also contains files that are not part of the cut and do not modify any file of it:
`.gitattributes`, `.editorconfig`, `provenance/`, `scripts/verify_provenance.py`,
`scripts/check_denylist.py`, `.github/privacy-denylist.txt` and the import-phase workflow.

The cut's own `MANIFEST.sha256` is stale (51 of its 88 entries do not match the files it lists,
#13). It is kept unchanged in the tag and attached to the release; it is not used for verification.

## Verify it yourself

```bash
git clone https://github.com/SebasBarrera/harness-engineering.git && cd harness-engineering
git checkout v0.8.0
python3 scripts/verify_provenance.py full
# manifest entries: 236 (excluded binaries: 4)
# matched: 232/232  mismatched: 0  missing: 0
```

The binary assets of the release are checked with the `SHA256SUMS` file attached to it, or with
`scripts/verify_provenance.py release-assets <directory>`. The `security` workflow re-verifies the
`v0.8.0` tag byte for byte on every push and weekly.

## After v0.8.0

From `v0.8.1` the code evolves, so the tree no longer matches the manifest. Behavioral defects of
the cut are kept unfixed while the thesis evaluation depends on them (milestone
*backlog — thesis-impact*); `v0.8.1` only adds infrastructure, documentation and quality fixes that
do not change the behavior evaluated in the thesis. `v0.8.1` and later releases are built by CI with an SPDX SBOM and a build
provenance attestation (`gh attestation verify`); the `v0.8.0` binaries are the original ones and
have no attestation.
