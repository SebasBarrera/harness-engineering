# Desglose del tiempo propio del harness

Tiempo propio = duración del comando menos el tiempo de las llamadas al agente de su tramo. Las cifras salen de `harness.commands` y `agentCalls` del registro de cada corrida.

| Corrida | comandos cortos (s) | INTENT a PLANNING (s) | IMPLEMENTATION/VERIFICATION/REVIEW (s) | cierre (s) | Total propio (s) |
|---|---:|---:|---:|---:|---:|
| brownfield / harness-core #1 (17:05:29) | 9.0 | 0.0 | 25.3 | 3.2 | 37.5 |
| brownfield / harness #1 (17:07:15) | 57.9 | 60.6 | 116.0 | 11.5 | 245.9 |
| brownfield / harness-anchored #1 (17:16:11) | 61.7 | 80.5 | 119.4 | 14.3 | 275.9 |
| brownfield / harness #1 (17:26:32) | 71.1 | 111.9 | 70.7 | 10.9 | 264.6 |

El tramo de comandos cortos incluye el arranque de la CLI en cada invocación.

## Validadores de brownfield / harness #1 (17:07:15)

- Pasada 1 (41.1 s): python.pytest 7.9 s, python.ruff 1.1 s, python.mypy 13.7 s, traceability.requirements 0.4 s, harness.principles 0.8 s, harness.layers 0.2 s, harness.architecture 0.2 s, harness.security-patterns 0.2 s, harness.constraints 0.3 s, harness.weakened-controls 0.5 s, harness.secrets 0.4 s, harness.test-quality 7.5 s, harness.risk-factors 0.7 s, baseline 6.0 s, harness.ratchet 0.5 s, harness.mutation 0.7 s
- Pasada 2 (43.9 s): python.pytest 3.6 s, python.ruff 0.8 s, python.mypy 7.4 s, traceability.requirements 0.3 s, harness.reproduce-first 0.4 s, harness.principles 0.5 s, harness.layers 0.2 s, harness.architecture 0.2 s, harness.security-patterns 0.2 s, harness.constraints 0.2 s, harness.weakened-controls 0.2 s, harness.secrets 0.3 s, harness.test-quality 6.5 s, harness.risk-factors 0.2 s, harness.ratchet 0.1 s, harness.mutation 22.0 s, harness.certification 0.8 s

## Validadores de brownfield / harness-anchored #1 (17:16:11)

- Pasada 1 (86.1 s): python.pytest 5.2 s, python.ruff 4.6 s, python.mypy 18.4 s, traceability.requirements 0.4 s, harness.acceptance-tests 2.4 s, harness.principles 0.9 s, harness.layers 0.7 s, harness.architecture 0.5 s, harness.security-patterns 0.6 s, harness.constraints 0.9 s, harness.weakened-controls 1.0 s, harness.secrets 1.1 s, harness.test-quality 12.6 s, harness.risk-factors 0.8 s, baseline 3.2 s, baseline 4.6 s, harness.ratchet 1.9 s, harness.mutation 24.3 s, harness.certification 1.9 s

## Validadores de brownfield / harness #1 (17:26:32)

- Pasada 1 (57.3 s): python.pytest 3.1 s, python.ruff 0.8 s, python.mypy 22.4 s, traceability.requirements 0.7 s, harness.acceptance-tests 1.6 s, harness.principles 0.7 s, harness.layers 0.4 s, harness.architecture 0.4 s, harness.security-patterns 0.3 s, harness.constraints 0.3 s, harness.weakened-controls 0.2 s, harness.secrets 0.3 s, harness.test-quality 7.4 s, harness.risk-factors 0.6 s, baseline 5.5 s, harness.ratchet 0.4 s, harness.mutation 11.4 s, harness.certification 0.9 s
