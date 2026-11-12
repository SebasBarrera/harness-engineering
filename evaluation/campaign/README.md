# Campaña de evaluación de la 2.0.1

La evaluación de la tesis reporta solo la versión 2.0.1 (campaña en curso), instalada desde el
wheel construido a partir del tag anotado `v2.0.1`, nunca desde un árbol de fuentes (enmiendas
del 2026-11-11 y del 2026-11-12 en [`PREREGISTRO.md`](PREREGISTRO.md)). Los pilotos Haiku y
Sonnet corrieron sobre 2.0.0 y, como los pre-chequeos y los intentos listados en la enmienda del
2026-11-12, son comprobaciones del aparato, no resultados. Los directorios de resultados
conservan el nombre `results-2.0.0/`. Los resultados de versiones anteriores están en el historial de git y en el tag `v0.8.0`, y no forman parte de este análisis.

Scripts, catálogo y plan de la campaña que evalúa la versión 2.0.1 del harness. Todo lo de esta
carpeta se versiona: la carpeta local `.local/` donde vivían los scripts de comparación se perdió
con el equipo anterior, y no debe volver a pasar.

| Archivo | Qué es |
|---|---|
| [`CATALOGO.md`](CATALOGO.md) | Inventario de las pruebas existentes y 55 pruebas nuevas propuestas (H01–H55), con hipótesis, método, métricas, criterio de éxito, costo y sesgos; plan y reglas de integridad. |
| [`PREREGISTRO.md`](PREREGISTRO.md) | Hipótesis, condiciones, métricas y criterio de éxito del top 10 de pruebas nuevas, congelados antes de su primera corrida, con la operacionalización de H01, H03, H05 y H38 (`evaluation/deterministic/h01_weakened_tests.py`, `h03_secrets.py`, `h05_dependencies.py`, `h38_false_fast_lane.py`) y, en una segunda congelación, de H19, H20, H44 y H45 (`h19_size_budgets.py`, `h20_cost_ceilings.py`, `h44_block_clarity.py`, `h45_clean_changes.py`; su proveedor fixture es `budget_agent.py`), en una tercera congelación con tres enmiendas previas a cualquier resultado, de H27, H28, H31 y H32 (`h27_layer_templates.py`, `h28_reverse_engineering.py`, `h31_tdd_red_green.py`, `h32_bdd_frozen.py`; su proveedor fixture es `practice_agent.py`), en una tercera tanda, de H06, H07, H10 y H12 (`h06_layer_violations.py`, `h07_unsupported_claim.py`, `h10_uncovered_regressions.py` con `h10_probe.py`, `h12_frozen_acceptance.py`) en una cuarta congelación, de H35, H36, H40 y H41 (`h35_light_mutation.py`, `h36_principles.py`, `h40_embedded_mcp.py`, `h41_routing_invariants.py`) y, en una quinta congelación con tres enmiendas fechadas antes de cualquier resultado, de H13, H16, H17 y H24 (`h13_bundle_audit.py`, `h16_decider_identity.py`, `h17_retention.py`, `h24_context_bounds.py`, con `audit_support.py`). y, en una sexta congelación con su enmienda, de H47, H48, H50 y H51 (`h47_crash_resume.py`, `h48_concurrent_runs.py`, `h50_degraded_provider.py`, `h51_doctor.py`; su proveedor fixture es `robust_agent.py` y el control de procesos, `robustness_support.py`). |
| `run_deterministic.py` | Corre todas las suites deterministas (sin modelo) sobre un wheel: crea los entornos, verifica el archivo de `itsdangerous` y registra el código de salida de cada suite en `campaign-summary.json`. Sale con 1 si alguna falló. Suites: `fault-probes`, `ladder`, `review`, `friction`, `intent-equivalence`, `block0`, `metrics-validity`, `h01`, `h02`, `h03`, `h04`, `h05`, `h06`, `h07`, `h09`, `h10`, `h12`, `h13`, `h14`, `h15`, `h16`, `h17`, `h18`, `h19`, `h20`, `h22`, `h23`, `h24`, `h27`, `h28`, `h31`, `h32`, `h35`, `h36`, `h38`, `h40`, `h41`, `h44`, `h45` y, al final, `defect-corpus` (H11). `h10` lee el archivo de `itsdangerous` ya verificado (`EVAL_CACHE`). Corre en Linux, macOS y Windows (H46); en Windows las suites escriben `runtime.agentSandbox: off` (lo declaran en `environment.json`) y `--work` debe tener a lo sumo 40 caracteres. |
| `../deterministic/portability_compare.py` | H46: compara los resultados de `fault-probes`, `friction`, `ladder`, `review` y `demo_flows.py all` de cada plataforma con los de `linux-bwrap` y clasifica cada diferencia como esperada (E1–E4 de `PREREGISTRO.md`) o inesperada. `--self-check` lo prueba con datos sintéticos. |
| `../deterministic/defect_corpus.py` | H11: une los registros por caso de H01, H02, H03, H04, H05, H06, H07, H09, H10, N01-a (`ladder`) y N02-a (`review`) en una tabla con verdad conocida y calcula, por condición, la tasa de escape y la de falsa alarma por clase, lenguaje y fuente, con intervalos de Wilson, y el criterio pre-registrado. No vuelve a correr ni filtra casos: un caso que falta se lista como faltante. Recibe una lista de directorios (`--inputs`), así que H02, H04 o H09 se suman sin cambiar el código. `--self-check` lo prueba con datos sintéticos. |
| `../../.github/workflows/evaluation.yml` | El mismo script en Linux de GitHub Actions, con `bwrap` (sandbox real), lanzado a mano sobre la rama o etiqueta a evaluar (`workflow_dispatch`, «Use workflow from»): esa rama debe traer estos scripts. Los resultados quedan como artefactos para revisarlos y versionarlos. |

## Cómo lanzar la campaña determinista completa

En GitHub: *Actions* → *evaluation* → *Run workflow*, con «Use workflow from» en la rama o
etiqueta a evaluar y el campo `only` vacío (todas las suites), o una lista separada por comas
(por ejemplo `h01,h03`). Desde la terminal:

```bash
gh workflow run evaluation.yml --ref <rama-o-etiqueta>              # todas las suites
gh workflow run evaluation.yml --ref <rama-o-etiqueta> -f only=h05  # una sola
gh run download <id-de-la-corrida> --dir evaluation-artifacts       # al terminar
```

Las cuatro suites nuevas tardan entre 4 y 5 horas en total (estimación del diseño, aún no
medida) y, sumadas a las anteriores, pasan el límite de 6 horas de un job; por eso el workflow
se divide en jobs:

| Job | Qué corre | Artefacto | Dónde quedan los resultados dentro del artefacto |
|---|---|---|---|
| `select the suites (report)` | Reparte la lista `only` entre los jobs; un nombre desconocido lo hace fallar. | — | — |
| `deterministic suites (report)` | `fault-probes`, `ladder`, `review`, `friction`, `intent-equivalence`, `block0`, `metrics-validity`. | `evaluation-deterministic` | `results/deterministic/<suite>/`, `results/deterministic/campaign-summary.json`, `dist/` (wheel y `commit.txt`) |
| `<suite> suite (report)`, uno por `h01`, `h02`, `h03`, `h04`, `h05`, `h06`, `h07`, `h09`, `h10`, `h12`, `h13`, `h14`, `h15`, `h16`, `h17`, `h18`, `h19`, `h20`, `h22`, `h23`, `h24`, `h27`, `h28`, `h31`, `h32`, `h35`, `h36`, `h38`, `h40`, `h41`, `h44`, `h45` | La suite, con el mismo wheel construido en el job y `bwrap`. | `evaluation-deterministic-<suite>` | `results/deterministic/<suite>/`, `results/deterministic/campaign-summary.json`, `dist/` |
| `defect-escape corpus H11 (report)` | Descarga los artefactos anteriores y corre `defect_corpus.py`. | `evaluation-defect-corpus` | `results/deterministic/defect-corpus/defect-corpus.jsonl`, `defect-corpus-summary.json`, `defect-corpus.md` |
| `portability wheel H46 (report)` | Construye en Linux el único wheel que usan las cuatro plataformas de H46 (nombre `portability` en `only`). | `portability-wheel` | `commit.txt` y el wheel |
| `portability <plataforma> (report)`, uno por `linux-bwrap`, `linux-nobwrap` (`ubuntu-24.04`), `macos` (`macos-15`) y `windows` (`windows-2025`) | `fault-probes`, `friction`, `ladder` y `review` con `run_deterministic.py`, y `scripts/demo_flows.py all` con el mismo entorno virtual. | `portability-<plataforma>` | `results/portability/<plataforma>/<suite>/`, `results/portability/<plataforma>/demo/demo-transcript.json` |
| `portability comparison H46 (report)` | Corre `portability_compare.py` sobre los cuatro artefactos: coincidencias por plataforma frente a `linux-bwrap`, diferencias esperadas (E1–E4 de `PREREGISTRO.md`) e inesperadas, y `sandbox.unavailable`. Sale con 1 si hay una diferencia inesperada o falta una plataforma. | `evaluation-portability` | `results/portability/compare/portability-summary.json`, `portability.md` |

`defect-corpus` necesita en la misma corrida `h01`, `h02`, `h03`, `h04`, `h05`, `h06`, `h07`, `h09`, `h10`, `ladder` y `review`: si alguna
falta o falló, el job corre igual, lista la entrada ausente, marca el criterio como «no
evaluable» y sale con 1. Para versionar, cada carpeta `results/deterministic/<suite>/` se copia a
`evaluation/results-2.0.0/deterministic/<suite>/` tras revisarla.

## Reglas de integridad

- Los criterios de éxito se declaran antes de correr ([`PREREGISTRO.md`](PREREGISTRO.md), el top
  10 del catálogo congelado el 2026-11-10) y no se cambian después de ver resultados.
- **Toda corrida se reporta.** Ninguna prueba se descarta por dar un resultado desfavorable al
  harness; solo se excluyen corridas por causas técnicas declaradas (por ejemplo, el límite de uso
  de la cuenta), con su causa en el registro. Los costos del harness (tiempo, tokens, fricción) se
  presentan como el precio de la gobernanza, junto con lo que compran y la configuración que los
  mitiga.
- La persona simulada es una simulación declarada, no una persona.
- Ninguna cifra mezcla versiones, máquinas ni sistemas operativos: cada resultado lleva su
  `environment.json` (versión, SHA-256 del wheel, commit, plataforma).

## Entornos

| Entorno | Qué corre | Limitaciones declaradas |
|---|---|---|
| GitHub Actions, `ubuntu-24.04` | Suites deterministas (sin modelo), con el sandbox de escritura real (`bwrap`), sobre el wheel de `v2.0.1`. Es el entorno de las cifras de la regla 6 del pre-registro. | Runners compartidos: los tiempos absolutos son ruidosos; se comparan condiciones dentro de la misma corrida. |
| Windows 10 (equipo actual, sin virtualización) | Corridas con Claude real (piloto y bloques), con la sesión local del CLI, sobre el wheel de `v2.0.1` (declarado en la enmienda del 2026-11-12: la regla 6 aplica a las suites deterministas). | No hay sandbox de escritura en Windows: las condiciones gobernadas corren con `runtime.agentSandbox: off`, declarado en cada registro como desviación de lo que escribe `harness init`. El código de evaluación con modelo es POSIX y necesita adaptarse. Máquina de 4 núcleos y 12 GB: los tiempos no son comparables con los de otro equipo o sistema operativo. |
| **macOS (pendiente: cuando el autor compre una Mac)** | **La campaña completa en un solo equipo**: suites deterministas y todos los bloques con modelo, con `sandbox-exec` y los lanzadores de `evaluation/launchers/` sin cambios. | — |

### Pendiente para la Mac

Cuando haya una Mac disponible:

1. Instalar Python 3.12, Git, Node.js y el CLI de Claude Code autenticado con la cuenta de
   evaluación (`agentlib.EXPECTED_ACCOUNT`).
2. Correr `run_deterministic.py` sobre el wheel `v2.0.1` publicado y comparar con los resultados
   de GitHub Actions (mismo código, otro sistema operativo).
3. Repetir el piloto y los bloques A, B, D y embebido con `evaluation/launchers/*.sh`, con el
   sandbox en `enforce` como lo escribe `harness init`, y reemplazar los resultados de Windows en
   la tesis por los de macOS, o presentarlos como réplica.
4. Implementar y correr en la Mac las pruebas nuevas del catálogo que dependen de `sandbox-exec`.

Se sigue en el issue #113.

## Bloque rides (`evaluation/launchers/block-rides.sh`)

El escenario de transporte y domicilios (`evaluation/rides/`, 95 requisitos en `SPEC.md`): las diez
celdas válidas de `run_rides.py` (`direct` × `one_line`, `paragraph`, `requirements`, `super`;
`stepwise` × `super`; `harness` × `one_line`, `paragraph`, `requirements`, `super`; `clarify` ×
`paragraph`) × `EVAL_MODELS` × `EVAL_REPS`, una corrida a la vez. Con los valores por defecto
(3 modelos, 3 repeticiones) son 30 corridas por modelo y 90 en total.

```bash
EVAL_PYTHON=... EVAL_WORK=... EVAL_CACHE=... EVAL_WHEEL=... evaluation/launchers/block-rides.sh
EVAL_RIDES_CELLS="direct:requirements harness:requirements" ...   # solo esas celdas
EVAL_RIDES_PLAN=1 ...                                              # lista las corridas, no corre nada
```

- Reanudable: una corrida ya registrada en `results-2.0.0/rides/rides.jsonl` se salta; una corrida
  interrumpida se retoma al volver a lanzar el bloque (`run_rides.py` reutiliza su directorio). El
  código de salida 75 (límite de uso) espera `EVAL_LIMIT_WAIT` segundos y repite, como `pilot.sh`.
- Las corridas no ven la suite oculta (`copy_code` la quita): al final `rides/rescore.py` puntúa los
  espacios de trabajo guardados y escribe `rides.scored.jsonl` y `summary-rescore.txt`.
- `run_rides.py` no tiene corrida en seco: con `EVAL_DRY=1` el bloque se niega a correr.
- **Tiempo y costo: son límites, no mediciones.** Cada llamada al agente tiene un tope de 4 h
  (`AGENT_TIMEOUT`) y de USD 200 (`AGENT_BUDGET`); cada llamada del product owner simulado de
  `clarify`, 30 min y USD 20 (`product_owner.py`). Una corrida `direct` hace una llamada (≤ 4 h,
  ≤ USD 200); `stepwise` hace 16 (≤ 64 h, ≤ USD 3 200); en `harness` cada tarea gobernada admite
  hasta 2 correcciones (`MAX_CORRECTIONS`), y `super` son 16 tareas, una por paso; `harness` ×
  `one_line` se rechaza al crear la tarea, sin llamar al modelo. El número de llamadas por tarea
  gobernada no está medido. Los tiempos y costos reales se sabrán con el piloto; las esperas por el
  límite de uso no cuentan en estos topes.
