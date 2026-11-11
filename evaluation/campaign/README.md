# Campaña de evaluación de la 2.0.0

La evaluación de la tesis reporta solo la versión 2.0.0 (campaña en curso). Los resultados de versiones anteriores están en el historial de git y en el tag `v0.8.0`, y no forman parte de este análisis.

Scripts, catálogo y plan de la campaña que evalúa la versión 2.0.0 del harness. Todo lo de esta
carpeta se versiona: la carpeta local `.local/` donde vivían los scripts de comparación se perdió
con el equipo anterior, y no debe volver a pasar.

| Archivo | Qué es |
|---|---|
| [`CATALOGO.md`](CATALOGO.md) | Inventario de las pruebas existentes y 55 pruebas nuevas propuestas (H01–H55), con hipótesis, método, métricas, criterio de éxito, costo y sesgos; plan y reglas de integridad. |
| [`PREREGISTRO.md`](PREREGISTRO.md) | Hipótesis, condiciones, métricas y criterio de éxito del top 10 de pruebas nuevas, congelados antes de su primera corrida, con la operacionalización de H01, H03, H05 y H38 (`evaluation/deterministic/h01_weakened_tests.py`, `h03_secrets.py`, `h05_dependencies.py`, `h38_false_fast_lane.py`) y, en una segunda congelación, de H19, H20, H44 y H45 (`h19_size_budgets.py`, `h20_cost_ceilings.py`, `h44_block_clarity.py`, `h45_clean_changes.py`; su proveedor fixture es `budget_agent.py`) y, en una tercera, de H13, H16, H17 y H24 (`h13_bundle_audit.py`, `h16_decider_identity.py`, `h17_retention.py`, `h24_context_bounds.py`, con `audit_support.py`), con tres enmiendas fechadas antes de cualquier resultado. |
| `run_deterministic.py` | Corre todas las suites deterministas (sin modelo) sobre un wheel: crea los entornos, verifica el archivo de `itsdangerous` y registra el código de salida de cada suite en `campaign-summary.json`. Sale con 1 si alguna falló. |
| `../../.github/workflows/evaluation.yml` | El mismo script en Linux de GitHub Actions, con `bwrap` (sandbox real), lanzado a mano sobre la rama o etiqueta a evaluar (`workflow_dispatch`, «Use workflow from»): esa rama debe traer estos scripts. Los resultados quedan como artefacto para revisarlos y versionarlos. |

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
| GitHub Actions, `ubuntu-24.04` | Suites deterministas (sin modelo), con el sandbox de escritura real (`bwrap`). | Runners compartidos: los tiempos absolutos son ruidosos; se comparan condiciones dentro de la misma corrida. |
| Windows 10 (equipo actual, sin virtualización) | Corridas con Claude real (piloto y bloques), con la sesión local del CLI. | No hay sandbox de escritura en Windows: las condiciones gobernadas corren con `runtime.agentSandbox: off`, declarado en cada registro como desviación de lo que escribe `harness init`. El código de evaluación con modelo es POSIX y necesita adaptarse. Máquina de 4 núcleos y 12 GB: los tiempos no son comparables con los de otro equipo o sistema operativo. |
| **macOS (pendiente: cuando el autor compre una Mac)** | **La campaña completa en un solo equipo**: suites deterministas y todos los bloques con modelo, con `sandbox-exec` y los lanzadores de `evaluation/launchers/` sin cambios. | — |

### Pendiente para la Mac

Cuando haya una Mac disponible:

1. Instalar Python 3.12, Git, Node.js y el CLI de Claude Code autenticado con la cuenta de
   evaluación (`agentlib.EXPECTED_ACCOUNT`).
2. Correr `run_deterministic.py` sobre el wheel `v2.0.0` publicado y comparar con los resultados
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
