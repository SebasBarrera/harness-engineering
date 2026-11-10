# Campaña de evaluación de la 2.0.0

Scripts, catálogo y plan de la campaña que evalúa la versión 2.0.0 del harness. Todo lo de esta
carpeta se versiona: la carpeta local `.local/` donde vivían los scripts de comparación se perdió
con el equipo anterior, y no debe volver a pasar.

| Archivo | Qué es |
|---|---|
| [`CATALOGO.md`](CATALOGO.md) | Inventario de las pruebas existentes y 55 pruebas nuevas propuestas (H01–H55), con hipótesis, método, métricas, criterio de éxito, costo y sesgos; plan y reglas de integridad. |
| [`PREREGISTRO.md`](PREREGISTRO.md) | Hipótesis, condiciones, métricas y criterio de éxito del top 10 de pruebas nuevas, congelados antes de su primera corrida, con la operacionalización de H01, H03, H05 y H38 (`evaluation/deterministic/h01_weakened_tests.py`, `h03_secrets.py`, `h05_dependencies.py`, `h38_false_fast_lane.py`). |
| `run_deterministic.py` | Corre todas las suites deterministas (sin modelo) sobre un wheel: crea los entornos, verifica el archivo de `itsdangerous` y registra el código de salida de cada suite en `campaign-summary.json`. Sale con 1 si alguna falló. Suites: `fault-probes`, `ladder`, `review`, `friction`, `intent-equivalence`, `block0`, `metrics-validity`, `h01`, `h03`, `h05`, `h38` y, al final, `defect-corpus` (H11). |
| `../deterministic/defect_corpus.py` | H11: une los registros por caso de H01, H03, H05, N01-a (`ladder`) y N02-a (`review`) en una tabla con verdad conocida y calcula, por condición, la tasa de escape y la de falsa alarma por clase, lenguaje y fuente, con intervalos de Wilson, y el criterio pre-registrado. No vuelve a correr ni filtra casos: un caso que falta se lista como faltante. Recibe una lista de directorios (`--inputs`), así que H02, H04 o H09 se suman sin cambiar el código. `--self-check` lo prueba con datos sintéticos. |
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
| `<suite> suite (report)`, uno por `h01`, `h03`, `h05`, `h38` | La suite, con el mismo wheel construido en el job y `bwrap`. | `evaluation-deterministic-<suite>` | `results/deterministic/<suite>/`, `results/deterministic/campaign-summary.json`, `dist/` |
| `defect-escape corpus H11 (report)` | Descarga los artefactos anteriores y corre `defect_corpus.py`. | `evaluation-defect-corpus` | `results/deterministic/defect-corpus/defect-corpus.jsonl`, `defect-corpus-summary.json`, `defect-corpus.md` |

`defect-corpus` necesita en la misma corrida `h01`, `h03`, `h05`, `ladder` y `review`: si alguna
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
| GitHub Actions, `ubuntu-24.04` | Suites deterministas (sin modelo), con el sandbox de escritura real (`bwrap`). | Runners compartidos: los tiempos absolutos son ruidosos; se comparan condiciones dentro de la misma corrida. |
| Windows 10 (equipo actual, sin virtualización) | Corridas con Claude real (piloto y bloques), con la sesión local del CLI. | No hay sandbox de escritura en Windows: las condiciones gobernadas corren con `runtime.agentSandbox: off`, declarado en cada registro como desviación de lo que escribe `harness init`. El código de evaluación con modelo es POSIX y necesita adaptarse. Máquina de 4 núcleos y 12 GB: los tiempos no se comparan con los de macOS de la 0.9.0. |
| **macOS (pendiente: cuando el autor compre una Mac)** | **La campaña completa en un solo equipo**, como la evaluación de la 0.9.0: suites deterministas y todos los bloques con modelo, con `sandbox-exec`, los lanzadores de `evaluation/launchers/` sin cambios y tiempos comparables con las mediciones anteriores en macOS. | — |

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
