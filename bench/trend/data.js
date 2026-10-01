window.BENCHMARK_DATA = {
  "lastUpdate": 1790852942383,
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
          "id": "b18ae666234aed93ea5a9360eb75f8da6181610d",
          "message": "Merge branch 'fix/task-loader-required-fields' into develop\n\n- fix(application): reject task files with missing fields or unknown keys\n\nCloses #29\n\nVerification on the branch head 5947bb2d2f5c: 21 CI job(s) succeeded, no blocking job failed.\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732717655\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732717668\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732717689\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732717847\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732717879\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36732717939\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T09:57:05-05:00",
          "tree_id": "cbaa97188aeaab5529d3aa4929dfcf8ae7c6651e",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/b18ae666234aed93ea5a9360eb75f8da6181610d"
        },
        "date": 1790780264869,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001222,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001913,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011261,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074194,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.261352,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.009258,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.640932,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 5.764,
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
          "id": "50621d7f54f5e364f1937857050114c23f99a93b",
          "message": "Merge branch 'fix/gate-decide-camelcase-output' into develop\n\n- fix(cli): print camelCase keys from gate decide\n\nCloses #30\n\nVerification on the branch head db5e6defe797: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733011481\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733011533\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733011611\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733011768\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733011786\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733011835\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T09:59:57-05:00",
          "tree_id": "794a11df0adfd644625a97d561f0b617f7f5ec98",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/50621d7f54f5e364f1937857050114c23f99a93b"
        },
        "date": 1790780433603,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001222,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001913,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011231,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073958,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.248781,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.250363,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.995344,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 6.445,
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
          "id": "7371884b4b8d7b286e1f156a380d82d36cfce5f3",
          "message": "Merge branch 'fix/windows-artifact-store' into develop\n\n- fix(evidence): write artifacts on platforms without os.fchmod\n- fix(runtime): resolve .cmd executables such as npm on Windows\n\nVerification on the branch head b6e3106be8fa: 21 CI job(s) succeeded, no blocking job failed.\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729490390\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729490460\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729490589\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729490641\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729490673\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729490747\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T10:00:43-05:00",
          "tree_id": "af97ab3d20fbbd2ce8d202bb6f62ed41aedf4cb5",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/7371884b4b8d7b286e1f156a380d82d36cfce5f3"
        },
        "date": 1790780480766,
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
            "value": 0.011291,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074609,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.25087,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.957076,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.537014,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.94,
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
          "id": "efaa5d001c6c2ecd9b01613a24631b450ab22705",
          "message": "Merge branch 'ci/windows-supported' into develop\n\n- ci(windows): run the documented flows and drop the informational label\n\nCloses #31\n\nVerification on the branch head a024f7f5c234: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729987424\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729987439\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729987444\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729987465\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729987470\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36729987695\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T10:01:17-05:00",
          "tree_id": "ccc19f405393a330eff2d8503a8b2a55f6b40622",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/efaa5d001c6c2ecd9b01613a24631b450ab22705"
        },
        "date": 1790780570042,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001232,
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
            "value": 0.074219,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.271502,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.963607,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 12.606609,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.713,
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
          "id": "159353dd5318f5ebb41c131638ef1979063da748",
          "message": "Merge branch 'docs/v0.9.0-behavior' into develop\n\n- docs(brownfield): the itsdangerous case closes with a plain APPROVE\n- docs: describe the 0.9.0 behavior and its difference with the thesis cut\n\nVerification on the branch head 6600713efa3f: 21 CI job(s) succeeded, no blocking job failed.\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733550327\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733550417\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733550522\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733550542\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733550750\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36733550770\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T10:03:36-05:00",
          "tree_id": "bd5014b213523a29f7b979187f3a50ce0ce1f131",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/159353dd5318f5ebb41c131638ef1979063da748"
        },
        "date": 1790780710595,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.000697,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001141,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.006994,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.030693,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.212816,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 7.325581,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 7.642133,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.394,
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
          "id": "97225ce2ad1d65e306814e165179b710779a7bed",
          "message": "Merge branch 'release/0.9.0' into main\n\n- chore(release): prepare 0.9.0\n\nBehavioral fixes #1, #2, #9, #19, #29, #30 and #31 (see CHANGELOG.md). v0.8.0 keeps the evaluated behavior.\n\nVerification on the branch head e1ae4b60ec45: 22 CI job(s) succeeded, no blocking job failed.\n  windows: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251442\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251465\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251469\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251480\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251559\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251625\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36734251663\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T10:14:59-05:00",
          "tree_id": "0136a3305cef8696f7bbc75bac9a89c9c32bea48",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/97225ce2ad1d65e306814e165179b710779a7bed"
        },
        "date": 1790781337977,
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
            "value": 0.01135,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073487,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.27274,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.048117,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.983732,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 7.856,
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
          "id": "9170caca6fcfafd547a9362d141d49d056c7d10b",
          "message": "Merge branch 'main' into develop\n\nBack-merge of the v0.9.0 release (chore(release): prepare 0.9.0).",
          "timestamp": "2026-09-30T10:15:45-05:00",
          "tree_id": "0136a3305cef8696f7bbc75bac9a89c9c32bea48",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/9170caca6fcfafd547a9362d141d49d056c7d10b"
        },
        "date": 1790781428169,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001233,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001923,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011361,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073939,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.256575,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.903478,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.363513,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.499,
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
          "id": "1cb019f12b71528325cb1ad1382e151d0428b6db",
          "message": "Merge branch 'test/controlled-evaluation' into develop\n\n- chore(security): allow the fictitious password of an evaluation test\n- test(evaluation): add the evaluation scenarios, tasks and hidden tests\n- test(evaluation): add the agent adapter, orchestrator and measurement\n- test(evaluation): add fault-injection probes and the post-hoc review\n- test(evaluation): add the report and the results of the v0.9.0 evaluation\n- docs(evaluation): summarize the controlled evaluation of v0.9.0\n\nVerification on the branch head 5645b58639c9: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36752513313\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36752513343\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36752513384\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36752513573\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36752513780\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36752513808\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T12:37:40-05:00",
          "tree_id": "4b409bc7279b94d8c90dc8ce9a310cf595708cfe",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/1cb019f12b71528325cb1ad1382e151d0428b6db"
        },
        "date": 1790789904816,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001191,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001783,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011126,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.058176,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.210004,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 12.254524,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 12.660452,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 3.296,
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
          "id": "e05e1bc730c92ca4b732bf11c66789fc4fd7a3e3",
          "message": "Merge branch 'test/codex-evaluation' into develop\n\nExtend the controlled evaluation of v0.9.0 with three blocks and their\npublished data under evaluation/.\n\n- Codex CLI backend (codex_provider.py, --agent codex): 35 valid runs\n  with gpt-6.1-sol and gpt-6-luna; the 18 governed runs were approved at\n  the first gate with the eight traceability relations.\n- Prompt levels with Claude Code: a minimal task (one acceptance\n  criterion) with and without the harness, and a one-line casual prompt\n  without it; the harness rejects the casual prompt at task creation.\n  81 runs, summarized in results/summary-prompts.json.\n- Longitudinal experiment: the same library built in five increments\n  with one-line prompts or with structured tasks through the harness,\n  with an oracle that reports defects after each increment. 18 sessions,\n  17 valid, summarized in results/summary-longitudinal.json.\n\nNo change under src/ or tests/: the harness behavior is untouched.\n\nVerification on the branch head e08081a9c605: 21 CI job(s) succeeded, no blocking job failed.\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36809555517\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36809555521\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36809555535\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36809555556\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36809555581\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36809555629\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-09-30T22:15:40-05:00",
          "tree_id": "5985dbb146433ddc10c7ae7274ec09a1316514c1",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/e05e1bc730c92ca4b732bf11c66789fc4fd7a3e3"
        },
        "date": 1790824594245,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.000931,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001412,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.008753,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.044871,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.191553,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.460413,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 9.832141,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 3.869,
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
          "id": "276e80838ec4eda94b7cb6d1e915cd736faa28e0",
          "message": "Merge branch 'test/bytecode-cache-race' into develop\n\n- test: stop the repaired-baseline test from reusing stale bytecode\n\nVerification on the branch head 895b8921489e: 21 CI job(s) succeeded, no blocking job failed.\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819228217\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819228226\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819228236\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819228252\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819228297\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819228341\nFiles of the v0.8.0 cut in the tree: matched: 131/232  mismatched: 77  missing: 24",
          "timestamp": "2026-10-01T00:21:13-05:00",
          "tree_id": "e420aedd300bdcf97cf3c5df716d76e82a439b24",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/276e80838ec4eda94b7cb6d1e915cd736faa28e0"
        },
        "date": 1790832134524,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001223,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001933,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011331,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073924,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.257327,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.844013,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.314739,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.317,
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
          "id": "2a1316161bdd02365b5c6f85b3f575ff69813a10",
          "message": "Merge branch 'feat/memory-operations' into develop\n\n- feat(memory): record why a record did not enter a context\n- feat(cli): add memory add, list, approve and invalidate\n- feat(agents): pass the selected memory to the agent provider\n\nCloses #6\n\nVerification on the branch head 18b7aeae4705: 21 CI job(s) succeeded, no blocking job failed.\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819368658\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819368674\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819368691\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819368704\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819368706\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819368707\nFiles of the v0.8.0 cut in the tree: matched: 129/232  mismatched: 79  missing: 24",
          "timestamp": "2026-10-01T00:23:36-05:00",
          "tree_id": "2f6fe7b241835c72f6b012593e2c622a6f90090b",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/2a1316161bdd02365b5c6f85b3f575ff69813a10"
        },
        "date": 1790832247952,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.000912,
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
            "value": 0.045063,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.197283,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.518304,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 9.825157,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 3.352,
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
          "id": "a04d662e0dadb5e91dce60ae21ceb5ba621cd824",
          "message": "Merge branch 'feat/retrospective-decisions' into develop\n\n- feat(retrospective): record the decision on a recommendation\n\nVerification on the branch head 970fa8e1969e: 21 CI job(s) succeeded, no blocking job failed.\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819555544\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819555557\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819555559\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819555574\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819555579\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819555624\nFiles of the v0.8.0 cut in the tree: matched: 128/232  mismatched: 80  missing: 24",
          "timestamp": "2026-10-01T00:26:21-05:00",
          "tree_id": "dac25f0bd0d079e9fa300b152c604e49bd74a596",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/a04d662e0dadb5e91dce60ae21ceb5ba621cd824"
        },
        "date": 1790832415057,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001213,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001904,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011211,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073933,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.245334,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.864673,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.288203,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 3.965,
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
          "id": "143840f19c635c43c6f39d4651b6e6afc98caa7b",
          "message": "Merge branch 'feat/provider-usage' into develop\n\n- feat(agents): record the usage a command provider reports\n\nVerification on the branch head 4d371bd78e17: 21 CI job(s) succeeded, no blocking job failed.\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819774936\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819774937\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819774942\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819774945\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819774951\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36819774972\nFiles of the v0.8.0 cut in the tree: matched: 128/232  mismatched: 80  missing: 24",
          "timestamp": "2026-10-01T00:29:26-05:00",
          "tree_id": "8a4260ae5b3c18f3e54a03083200b4070b323dc6",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/143840f19c635c43c6f39d4651b6e6afc98caa7b"
        },
        "date": 1790832599476,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001213,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001904,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011211,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074119,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.255583,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.961801,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 12.707356,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 5.214,
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
          "id": "c15dd48192f4a33b5cb65fa499807059c3ceb51c",
          "message": "Merge branch 'feat/memory-manifest-command' into develop\n\n- feat(cli): show the context manifest recorded for a run\n\nVerification on the branch head 1756c95bae3f: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820012833\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820012836\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820012838\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820012895\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820012961\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820012989\nFiles of the v0.8.0 cut in the tree: matched: 128/232  mismatched: 80  missing: 24",
          "timestamp": "2026-10-01T00:32:14-05:00",
          "tree_id": "e6ce84775f31df3ef243af172dc4e28d9d5380da",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/c15dd48192f4a33b5cb65fa499807059c3ceb51c"
        },
        "date": 1790832766774,
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
            "value": 0.011341,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.0746,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.233913,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.87424,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.242646,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 3.463,
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
          "id": "4a30aa5c4775e7b81ad5fca0510d098ec7d01729",
          "message": "Merge branch 'docs/memory-and-retrospective' into develop\n\n- test(demo): add the memory, recommendation and usage flow\n- docs: describe memory, recommendation decisions and reported usage\n\nVerification on the branch head 01d1c7f1c336: 20 CI job(s) succeeded, no blocking job failed.\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820248050\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820248082\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820248085\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820248103\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36820248114\nFiles of the v0.8.0 cut in the tree: matched: 128/232  mismatched: 80  missing: 24",
          "timestamp": "2026-10-01T00:34:58-05:00",
          "tree_id": "1417f138d79522ee315cf848a42ebaedd90f8042",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/4a30aa5c4775e7b81ad5fca0510d098ec7d01729"
        },
        "date": 1790832944595,
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
            "value": 0.011361,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074026,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.257606,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.992365,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.361837,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 3.587,
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
          "id": "012756dce786b8563463095ee4be827d55ea17a0",
          "message": "Merge branch 'test/evaluation-phase-data' into develop\n\n- test(evaluation): publish phase durations and retrospective recommendations\n- docs(evaluation): describe every evaluation block and its result files\n\nVerification on the branch head cde0a86a0a39: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36822720936\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36822720938\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36822720960\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36822720966\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36822720969\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36822720986\nFiles of the v0.8.0 cut in the tree: matched: 127/232  mismatched: 81  missing: 24",
          "timestamp": "2026-10-01T01:05:04-05:00",
          "tree_id": "8163509693376cea466e023008c664db5134ff79",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/012756dce786b8563463095ee4be827d55ea17a0"
        },
        "date": 1790834759766,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001202,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001772,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011157,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.057778,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.207928,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 12.712157,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 13.492824,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.415,
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
          "id": "cc9062294e36d00534b86b0b8906bf7023f4c9e7",
          "message": "Merge branch 'fix/manifest-contract-names' into develop\n\n- fix(memory): use the public contract field names in the context manifest\n\nVerification on the branch head 7477a05adf88: 21 CI job(s) succeeded, no blocking job failed.\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36823161216\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36823161256\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36823161302\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36823161309\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36823161328\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36823161333\nFiles of the v0.8.0 cut in the tree: matched: 127/232  mismatched: 81  missing: 24",
          "timestamp": "2026-10-01T01:10:35-05:00",
          "tree_id": "25b71a24bb9c949762b1114e4334562cdc14098e",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/cc9062294e36d00534b86b0b8906bf7023f4c9e7"
        },
        "date": 1790835069616,
        "tool": "customSmallerIsBetter",
        "benches": [
          {
            "name": "State transition (median)",
            "value": 0.001232,
            "unit": "ms"
          },
          {
            "name": "Gate evaluation (median)",
            "value": 0.001924,
            "unit": "ms"
          },
          {
            "name": "Canonical hash (digest) (median)",
            "value": 0.011422,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.074555,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.251001,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 10.957545,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.457719,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.418,
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
          "id": "dd775d8067afd321b659add0163fecc3824d17d7",
          "message": "Merge branch 'test/locked-retrospective-policy' into develop\n\n- test(configuration): cover the locked retrospective policy\n\nVerification on the branch head 1ea0c6bd8c7c: 21 CI job(s) succeeded, no blocking job failed.\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36824571907\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36824571908\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36824571909\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36824571915\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36824572050\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36824572082\nFiles of the v0.8.0 cut in the tree: matched: 126/232  mismatched: 82  missing: 24",
          "timestamp": "2026-10-01T01:26:30-05:00",
          "tree_id": "46d5a8968d121da2021a3be9da610fbb240f24e1",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/dd775d8067afd321b659add0163fecc3824d17d7"
        },
        "date": 1790836059673,
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
            "value": 0.011332,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.073954,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.246328,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 11.111631,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 11.73369,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 5.073,
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
          "id": "785ff7aaf46aa69e9fc4ec50129ba87a734831d5",
          "message": "Merge branch 'fix/benchmark-git-isolation' into develop\n\n- fix(benchmark): isolate the scenario fixture from the user's Git configuration\n- style(benchmark): drop the extra blank line after the import block\n\nVerification on the branch head 0b08c9486ef0: 21 CI job(s) succeeded, no blocking job failed.\n  security: https://github.com/SebasBarrera/harness-engineering/actions/runs/36829711092\n  lint: https://github.com/SebasBarrera/harness-engineering/actions/runs/36829711138\n  docs-smoke: https://github.com/SebasBarrera/harness-engineering/actions/runs/36829711171\n  ci: https://github.com/SebasBarrera/harness-engineering/actions/runs/36829711181\n  docker: https://github.com/SebasBarrera/harness-engineering/actions/runs/36829711206\n  build: https://github.com/SebasBarrera/harness-engineering/actions/runs/36829711234\nFiles of the v0.8.0 cut in the tree: matched: 125/232  mismatched: 83  missing: 24",
          "timestamp": "2026-10-01T06:08:17-05:00",
          "tree_id": "62c8af1bacd78c3574823abd69c94ec2fdf288e1",
          "url": "https://github.com/SebasBarrera/harness-engineering/commit/785ff7aaf46aa69e9fc4ec50129ba87a734831d5"
        },
        "date": 1790852941506,
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
            "value": 0.008743,
            "unit": "ms"
          },
          {
            "name": "Artifact put (deduplicated) (median)",
            "value": 0.044957,
            "unit": "ms"
          },
          {
            "name": "Event append (hash chain) (median)",
            "value": 0.19794,
            "unit": "ms"
          },
          {
            "name": "Process launch, direct (median)",
            "value": 9.610288,
            "unit": "ms"
          },
          {
            "name": "Process launch, governed (median)",
            "value": 9.993924,
            "unit": "ms"
          },
          {
            "name": "Governed process overhead (relative median)",
            "value": 4.841,
            "unit": "%"
          }
        ]
      }
    ]
  }
}