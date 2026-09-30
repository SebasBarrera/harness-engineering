window.BENCHMARK_DATA = {
  "lastUpdate": 1790740239929,
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
      }
    ]
  }
}