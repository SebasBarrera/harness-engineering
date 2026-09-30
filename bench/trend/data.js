window.BENCHMARK_DATA = {
  "lastUpdate": 1790780124622,
  "repoUrl": "https://github.com/SebasBarrera/harness-engineering",
  "entries": {
    "Governed Agent Harness microbenchmarks": [
      {
        "commit": {
          "author": {
            "name": "Sebastian Barrera",
            "username": "SebasBarrera",
            "email": "47837394+SebasBarrera@users.noreply.github.com"
          },
          "committer": {
            "name": "Sebastian Barrera",
            "username": "SebasBarrera",
            "email": "47837394+SebasBarrera@users.noreply.github.com"
          },
          "id": "d87784c5b5a2d9707d80ff94b70dca3e370e91d3",
          "message": "ci(benchmarks): install pytest for the scenario benchmark\n\nharness benchmark scenarios runs the generated Python fixture's tests with\npytest; the workflow installed only the api extra, so the first run on\ndevelop failed with 'No module named pytest'.",
          "timestamp": "2026-09-30T03:49:48Z",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/d87784c5b5a2d9707d80ff94b70dca3e370e91d3"
        },
        "date": 1790740239455,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001223,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001904,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011301,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074129,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.250163,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.967363,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.032017,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.858,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "c1b9792f971a2a4b73043f12de3b879284342163",
          "message": "Merge branch 'ci/benchmarks-test-dependency' into develop\n\n- ci(benchmarks): install pytest for the scenario benchmark\n\nVerification on the branch head d87784c5b5a2: 21 CI job(s) succeeded, no blocking job failed.\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666170314\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666170325\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666170383\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666170421\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666170482\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666170537\nFiles of the v0.8.0 cut in the tree: matched: 135/232  mismatched: 73  missing: 24",
          "timestamp": "2026-09-29T22:51:59-05:00",
          "tree_id": "b6927e2774121e339c459a088026f81b8c5404c2",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/c1b9792f971a2a4b73043f12de3b879284342163"
        },
        "date": 1790740355385,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001222,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001914,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011281,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.075652,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.239409,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.015697,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.026198,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.423,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "18c93ae57b3024d354df167ee1c35b476ff7f9b8",
          "message": "Merge branch 'release/0.8.1' into main\n\nRelease 0.8.1: repository infrastructure, documentation and quality release on\ntop of the v0.8.0 thesis cut, with no change to the behavior evaluated in the\nthesis. See CHANGELOG.md.\n\nCloses the milestone \"v0.8.1 - repository infrastructure and quality\".\n\nVerification on the branch head ba4fe33e639e: 21 CI job(s) succeeded, no blocking job failed.\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666328774\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666328783\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666328795\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666328798\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666328841\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36666328854\nFiles of the v0.8.0 cut in the tree: matched: 133/232  mismatched: 75  missing: 24",
          "timestamp": "2026-09-29T22:54:29-05:00",
          "tree_id": "e0e169aaac6c6722f73fd61d9fd2628159657be0",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/18c93ae57b3024d354df167ee1c35b476ff7f9b8"
        },
        "date": 1790740513514,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001222,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001903,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011321,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073808,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.264392,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.857204,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 10.927495,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.597,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "5e0b1f7fccae7dbd19578217774dc569fa585148",
          "message": "Merge branch 'main' into develop\n\nBack-merge of the v0.8.1 release into develop (tag v0.8.1).",
          "timestamp": "2026-09-29T23:00:28-05:00",
          "tree_id": "e0e169aaac6c6722f73fd61d9fd2628159657be0",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/5e0b1f7fccae7dbd19578217774dc569fa585148"
        },
        "date": 1790740871097,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001212,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001913,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011271,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.07435,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.254613,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.94303,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.025661,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.846,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "1e1de64fba74fa0e87365d1950accccf7d1aa32a",
          "message": "Merge branch 'ci/scorecard-default-branch' into develop\n\n- ci(scorecard): run on the default branch\n- docs(monitoring): Scorecard runs on develop\n\nVerification on the branch head 46070cc14413: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667027468\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667027482\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667027488\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667027510\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667027546\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667027634\nFiles of the v0.8.0 cut in the tree: matched: 133/232  mismatched: 75  missing: 24",
          "timestamp": "2026-09-29T23:03:23-05:00",
          "tree_id": "4d0a928a74df85a7d76633e2c8bf9de6336886de",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/1e1de64fba74fa0e87365d1950accccf7d1aa32a"
        },
        "date": 1790741045535,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.000921,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001382,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.008693,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.045028,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.188999,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.514032,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 9.562244,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.794,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "71f8617884d92e84f03083e32a168cfe4b082dad",
          "message": "Merge branch 'docs/full-license-text' into develop\n\n- docs: ship the full Apache-2.0 license text\n\nVerification on the branch head 9d3eb370d651: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667439315\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667439356\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667439425\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667439447\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667439462\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36667439523\nFiles of the v0.8.0 cut in the tree: matched: 132/232  mismatched: 76  missing: 24",
          "timestamp": "2026-09-29T23:09:03-05:00",
          "tree_id": "048c1724fed11c23ce87675a940340f477a74b44",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/71f8617884d92e84f03083e32a168cfe4b082dad"
        },
        "date": 1790741381570,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001126,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001904,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.010656,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.057918,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.174968,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.658811,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 9.629506,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.376,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "sebasbarrera981119@gmail.com",
            "name": "Juan Sebastián Barrera Pulido",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "82ae5f8d3a8c1fa52cd9540a9ca4a6506f2015c2",
          "message": "Merge pull request #28 from SebasBarrera/dependabot/pip/develop/mypy-gte-1.11-and-lt-3\n\nbuild(deps-dev): update mypy requirement from <2,>=1.11 to >=1.11,<3",
          "timestamp": "2026-09-30T08:48:34-05:00",
          "tree_id": "f813d61d657d19ebd0c923653a5893ee9c6c0355",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/82ae5f8d3a8c1fa52cd9540a9ca4a6506f2015c2"
        },
        "date": 1790776142580,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.000696,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001136,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.006976,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.030597,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.21812,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 7.191397,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 7.185875,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": -0.077,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "5c0a0bbcd7c27e0654f3ceeea911c8fd8e5e6fb3",
          "message": "Merge branch 'docs/sonarcloud-badges' into develop\n\n- docs(readme): add SonarQube Cloud badges\n\nVerification on the branch head 3e89163183e0: 21 CI job(s) succeeded, no blocking job failed.\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730549307\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730549315\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730549377\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730549386\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730549416\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730549453\nFiles of the v0.8.0 cut in the tree: matched: 132/232  mismatched: 76  missing: 24",
          "timestamp": "2026-09-30T09:39:27-05:00",
          "tree_id": "a04a884545f69c3ae88188ce96c7a66c5dc638e6",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/5c0a0bbcd7c27e0654f3ceeea911c8fd8e5e6fb3"
        },
        "date": 1790779238699,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001222,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001904,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011201,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074579,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.257827,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.218502,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.273796,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.795,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "9318e80eb22663a6c9d9e99ada00264633d5650c",
          "message": "Merge branch 'fix/validator-missing-module-blocked' into develop\n\n- fix(validators): block a mandatory validator whose Python module is missing\n\nCloses #1\n\nVerification on the branch head c904abeec3c4: 21 CI job(s) succeeded, no blocking job failed.\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730778750\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730778803\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730778852\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730778960\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730779121\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36730779373\nFiles of the v0.8.0 cut in the tree: matched: 132/232  mismatched: 76  missing: 24",
          "timestamp": "2026-09-30T09:41:47-05:00",
          "tree_id": "dcf4e3ab34e3f83bc307ba4afd56b55552f4f20a",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/9318e80eb22663a6c9d9e99ada00264633d5650c"
        },
        "date": 1790779354943,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001232,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001923,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011802,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.075341,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.259735,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.18609,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.310607,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 1.097,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "531e891312c53b04d949aad46f8e77baf7110b4b",
          "message": "Merge branch 'fix/gate-latest-attempt' into develop\n\n- fix(gates): consolidate only the latest attempt of each validator\n- test: update the demonstration flows to the latest-attempt gate\n\nCloses #2\n\nVerification on the branch head 74054b3a0cea: 21 CI job(s) succeeded, no blocking job failed.\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36731069545\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36731069551\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36731069761\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36731069805\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36731069934\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36731069959\nFiles of the v0.8.0 cut in the tree: matched: 132/232  mismatched: 76  missing: 24",
          "timestamp": "2026-09-30T09:49:18-05:00",
          "tree_id": "0b0f483628ae7b75ddf9059ee83769014bae3739",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/531e891312c53b04d949aad46f8e77baf7110b4b"
        },
        "date": 1790779800393,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001222,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001923,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011301,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073588,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.274985,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.883767,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 10.99021,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 0.976,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "1d57ff516d0f32cfba770b6e602c26c411df32e0",
          "message": "Merge branch 'fix/process-output-streaming-bound' into develop\n\n- fix(runtime): bound process output while reading it\n\nCloses #9\n\nVerification on the branch head a43f20d50de5: 21 CI job(s) succeeded, no blocking job failed.\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732009162\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732009202\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732009232\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732009243\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732009342\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732009366\nFiles of the v0.8.0 cut in the tree: matched: 132/232  mismatched: 76  missing: 24",
          "timestamp": "2026-09-30T09:51:50-05:00",
          "tree_id": "fdba7dd86350e4492a320399007a5b5af2a2f834",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/1d57ff516d0f32cfba770b6e602c26c411df32e0"
        },
        "date": 1790779939152,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.000912,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001392,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.008653,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.045403,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.190601,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.807769,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 10.198906,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.086,
            "unit": "%"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "committer": {
            "email": "47837394+SebasBarrera@users.noreply.github.com",
            "name": "Sebastian Barrera",
            "username": "SebasBarrera"
          },
          "distinct": true,
          "id": "bf80ff0149aa65a3c5cbac1996b885b2aa1e925f",
          "message": "Merge branch 'fix/contained-path-symlinked-ancestor' into develop\n\n- fix(capabilities): accept workspace paths reached through a symlinked ancestor\n\nCloses #19\n\nVerification on the branch head 69e0437e1b2c: 21 CI job(s) succeeded, no blocking job failed.\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732327633\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732327636\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732327654\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732327807\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732327825\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732327832\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T09:54:48-05:00",
          "tree_id": "8a95dcf0943fb892b406d9bacc4a92462b6ce354",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/bf80ff0149aa65a3c5cbac1996b885b2aa1e925f"
        },
        "date": 1790780123639,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001125,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001929,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.01067,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.057632,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.15737,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.38389,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 9.840252,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 5.382,
            "unit": "%"
          }
        ]
      }
    ]
  }
}