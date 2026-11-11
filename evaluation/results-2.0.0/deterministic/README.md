# Evaluación determinista de la 2.0.0 (sin llamadas al modelo)

Las suites de este directorio no llaman a ningún modelo: cada agente es un proveedor de comando
fixture (`evaluation/fault_provider.py`, `evaluation/deterministic/review_fixture_reviewer.py`, los
fixtures dentro de `friction.py`) o el proveedor `simulated` del harness. Cada suite escribe un
`environment.json` con el ejecutable del harness, su versión (`harness --version`), el wheel y
su SHA-256 cuando se da uno (`--wheel` o `$HARNESS_WHEEL`), el head del repositorio y un digest del
código de evaluación.

| Suite | Ids de prueba | Script | Resultados |
|---|---|---|---|
| Sondas de fallas (13 de la tesis + 11 de integridad + 5 de confinamiento, 3 repeticiones) | P08, N13, N12 | `evaluation/fault_probes.py` | `fault-probes/` |
| Corpus de la escalera de verificación | N01-a | `evaluation/deterministic/ladder_corpus.py` | `ladder/` |
| Corpus del panel de revisión | N02-a (gancho para N02-b) | `evaluation/deterministic/review_corpus.py` | `review/` |
| Fricción con el proveedor fixture | N03-a | `evaluation/deterministic/friction.py` | `friction/` |
| Equivalencia de INTENT | P24 | `evaluation/deterministic/intent_equivalence.py` | `intent-equivalence/` |
| Validez de las métricas | N04 | `evaluation/deterministic/metrics_validity.py` | `metrics-validity/` |
| Verificación técnica | Bloque 0: P03, P04, P06, P07, N16 | `evaluation/deterministic/block0_technical.py` | `block0/` |

Cada subdirectorio tiene un README con el comando exacto que lo produjo. Los directorios crudos de
las corridas (espacios de trabajo, registros de corridas) no se guardan en Git: las suites los
escriben bajo `--work`.

## Simulación declarada

Las esperas de una persona antes de DECISION (preguntas de INTENT, pruebas de aceptación, una
aprobación de plan o una descomposición, un preflight no disponible) las responde una persona
simulada, `human.reviewer-simulated` (`evaluation/deterministic/common.py`, `simulated_person`):
responde las preguntas con "keep the behaviour the task describes", aprueba las pruebas de
aceptación y los planes, y continúa sin certificar un preflight no disponible. Lee la espera de
`harness inbox --json`, que desde la ola 9 (#73) lista cada espera antes de DECISION con su tipo y
su digest (se conserva una alternativa con `plan show` para un harness que no lo tenga). En DECISION
cada suite aplica su propia regla declarada (ver su README). Es una simulación de una persona, no
una persona.

## Entorno de estos resultados

Se corrió sobre el código del worktree de la rama `test/evaluation-2-0-0-deterministic` con la ola 9
fusionada (origin/develop, la rama de la ola 7 y `fix/wave9-hardening` en `de84849`, PR #89), cuyo
paquete todavía declara la versión `1.0.0` (`harness --version` imprime `harness 1.0.0`); la 2.0.0
aún no tiene tag. Python 3.12.11 en macOS arm64; los validadores de los proyectos corrieron con
pytest 9.1.1, Ruff 0.16.7, mypy y freezegun 1.4.0 desde un entorno virtual local. Todas las suites
se volvieron a correr sobre ese código el 2026-10-06 (los primeros resultados, sobre el código
anterior a la ola 9, están en el historial de esta rama). Después, el PR #89 se fusionó en develop
con un commit más (`12b13d9`: `harness init` agrega su entrada al archivo de ignorados en lugar de
reescribirlo), fusionado aquí más tarde; las suites no se volvieron a correr por él, y se corren una
vez más sobre el wheel v2.0.0.

Qué cambió la ola 9 en estos resultados (el README de cada subdirectorio tiene el detalle): las
sondas de fallas `unauthorized-command` (rechazado antes de empezar, #87) y `destructive-command`
(BLOCKED, salida 6, #76), la sonda nueva `reverify-continue` y la reverificación esperada de
`later-change` (#78); ninguna sonda deja ya `.gitignore` cambiado (#86, init escribe
`.git/info/exclude`); la re-revisión idéntica del corpus de revisión reporta 0 llamadas al modelo
ante un acierto de la caché global (#75); la sonda de línea de comandos del corpus de la escalera
recuperó su segunda variante (#74).

## Volver a correr sobre el wheel v2.0.0

```bash
# 1. Un entorno virtual con el wheel y las herramientas que usan los validadores de los proyectos.
python3.12 -m venv "$W/venv"
"$W/venv/bin/pip" install governed_agent_harness-2.0.0-py3-none-any.whl \
  pytest pytest-cov freezegun==1.4.0 ruff mypy coverage
python3.12 -m venv --without-pip "$W/nopytest"          # para la sonda de herramienta faltante
export HARNESS_BIN="$W/venv/bin/harness" HARNESS_WHEEL="$PWD/governed_agent_harness-2.0.0-py3-none-any.whl"
PY="$W/venv/bin/python"; R=evaluation/results-2.0.0/deterministic
# El archivo brownfield (SHA-256 7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b):
mkdir -p "$W/cache" && curl -sSfL -o "$W/cache/itsdangerous-2.2.0.tar.gz" \
  https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz

# 2. Las suites (directorios de trabajo fuera de cualquier repositorio: Ruff lee la configuración más cercana).
"$PY" evaluation/fault_probes.py --work "$W/probes" --cache "$W/cache" --out $R/fault-probes/fault-probes.jsonl \
  --reps 3 --project-venv "$W/venv" --nopytest-venv "$W/nopytest"
"$PY" evaluation/deterministic/ladder_corpus.py --out $R/ladder --work "$W/ladder" --project-venv "$W/venv"
"$PY" evaluation/deterministic/review_corpus.py --out $R/review --work "$W/review" --project-venv "$W/venv"
"$PY" evaluation/deterministic/friction.py --out $R/friction --work "$W/friction" --project-venv "$W/venv"
"$PY" evaluation/deterministic/intent_equivalence.py --out $R/intent-equivalence
# 3. El bloque 0 solo en la máquina (mide tiempos de benchmarks), desde un checkout del tag v2.0.0:
"$PY" evaluation/deterministic/block0_technical.py --repo "$W/v2.0.0" --python "$PY" \
  --project-python "$PY" --ruff "$W/venv/bin/ruff" --out $R/block0 --work "$W/block0"
# 4. N04 sobre las corridas de fricción y los proyectos de demostración del bloque 0:
"$PY" evaluation/deterministic/metrics_validity.py --out $R/metrics-validity \
  --runs-glob "$W/friction/friction-*" --demo-workdir "$W/block0/demo" --demo-state "$W/block0/demo-state"
```

`--harness` reemplaza a `$HARNESS_BIN`; cada suite registra `harness --version` y el SHA-256 del
wheel. La sonda de decisión vencida mueve el reloj de un `harness run continue` con
`evaluation/deterministic/timeshift.py`, ejecutado por el intérprete del harness (`--harness-python`,
por defecto el `python` que está junto al ejecutable del harness).
