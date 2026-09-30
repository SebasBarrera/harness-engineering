window.BENCHMARK_DATA = {
  "lastUpdate": 1790741046553,
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
      }
    ]
  }
}