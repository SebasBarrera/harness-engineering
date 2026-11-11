# Bloque 0: verificación técnica (P03, P04, P06, P07, N16)

`evaluation/deterministic/block0_technical.py` escribe un resumen, `block0-summary.json`, con:

- `tests` (P03): la suite completa una vez, `python -m pytest -W error::ResourceWarning
  --cov=governed_harness --cov-branch` (JUnit y JSON de cobertura; el archivo de cobertura se reduce
  a sus totales): pruebas y resultados por familia (unit, contract, integration, security, e2e,
  performance), sentencias, cobertura de líneas y de ramas. `pytest-output.txt` guarda el final de
  la salida.
- `security` (P06): las pruebas de `tests/security` con sus resultados, del mismo reporte JUnit.
- `static`: `ruff check src tests scripts`, `ruff format --check src tests scripts` y `mypy`
  (estricto, modo paquete).
- `demo` (P04): `scripts/demo_flows.py all --workdir DIR --transcript FILE`: flujos, pasos, pasos
  cuyo código de salida difirió de lo esperado y las comprobaciones de salida (líneas `[ok ]` /
  `[BAD]` con `check:`). Se guardan `demo-transcript.json` y `demo-output.txt`.
- `benchmark` (P07): `harness benchmark run` cinco veces (`benchmark-micro-N.json`) y
  `harness benchmark scenarios` (`benchmark-scenarios.json`).
- `large` (N16): la medición de docs/reference/configuration.md ("Large repositories"),
  repetida: un repositorio Git generado de 10,001 archivos versionados (9,996 archivos de texto de
  unos 11.7 kB más los cinco archivos del proyecto de ejemplo), 500 archivos de build ignorados y un
  `.env` ignorado; una tarea patch de dos archivos, el proveedor `simulated` y solo `python.pytest`;
  la configuración que escribe `harness init` con y sin las tres claves de espacio de trabajo
  (`snapshot`, `baseline`, `snapshotCache`), tres repeticiones de cada una: segundos de `run start`,
  memoria residente máxima (`/usr/bin/time -l`), bytes guardados bajo `.harness` y en el registro de
  corridas, el archivo guardado más grande y si se guardó el secreto del `.env`. El generador es el
  de este script (el que está detrás de las cifras del CHANGELOG no está en el repositorio), así que
  sus cifras no son las del CHANGELOG.

Comandos (código del worktree; `static` corrió primero, luego las otras secciones, sin ninguna otra
suite corriendo):

```bash
SCRATCH=<scratch>/scratchpad
.venv/bin/python evaluation/deterministic/block0_technical.py --harness "$PWD/.venv/bin/harness" --repo . \
  --python $SCRATCH/venvs/eval-w9/bin/python --project-python $SCRATCH/venvs/eval-w9/bin/python --ruff "<venv>/bin/ruff" \
  --out evaluation/results-2.0.0/deterministic/block0 --work $SCRATCH/work/block0-w9 --only static
.venv/bin/python evaluation/deterministic/block0_technical.py ... --only tests,demo,benchmark,large
```

Notas sobre esta corrida:

- Se volvió a correr el 2026-10-06 sobre el código con la ola 9 fusionada (`6158cc6`; ola 9 en
  `de84849`), cada sección sin ninguna otra suite corriendo, con las herramientas de desarrollo del
  repositorio (`--python` un entorno virtual con el `src` del worktree primero y los paquetes de
  desarrollo locales: pytest 9.1.1, mypy 1.20.2, Ruff 0.16.7). Resultado: 1189 pruebas, 0 fallidas
  (la primera corrida tuvo 1095 pruebas y 2 fallas por una instantánea vieja de la ola 7, corregidas
  por las fusiones posteriores); mypy sin problemas en 244 archivos fuente; Ruff limpio;
  `demo_flows.py all`: 21 flujos, 204 pasos, 0 discrepancias, 50 comprobaciones de salida ok. No
  forma parte del resultado: la misma corrida de `mypy` con mypy 2.3.1 (también permitido por
  `pyproject.toml`, `mypy>=1.11,<3`) desde otro entorno local reportó 34 errores en 12 archivos; la
  causa no se investigó.
- Las rutas absolutas locales de los archivos de resultados se reemplazaron por `<repo>`, `<work>`,
  `<tmp>` y `~` con `evaluation/deterministic/sanitize_paths.py`.
- En las seis corridas de `large`, `run start` se detuvo en VERIFICATION (salida 6, `BLOCKED`) con
  la configuración completa de `init`; el script borró cada espacio de trabajo después de medirlo y
  no registró la razón del bloqueo, así que los segundos y la memoria residente cubren de INTENT a
  VERIFICATION, no una corrida que llegó a DECISION como en la medición del CHANGELOG.
