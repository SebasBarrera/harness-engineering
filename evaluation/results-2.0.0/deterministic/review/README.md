# N02-a: las partes deterministas del panel de revisión sobre un corpus sembrado

23 cambios sobre un pequeño proyecto de facturación por capas (`evaluation/corpus/review`): 16
sembrados con un defecto cada uno (calidad: lógica incorrecta, código muerto, un argumento por
defecto mutable que verifica una regla de Ruff; arquitectura: un módulo de dominio que importa un
adaptador; resiliencia: una llamada externa sin timeout, una excepción tragada; pruebas: una aserción
tautológica, un mock del sujeto; concurrencia: estado mutable compartido en un pool de hilos;
seguridad del pipeline: un gate de CI debilitado, una ruta peligrosa, una inyección de expresiones,
un archivo temporal predecible; instrucciones dirigidas a un agente en el README; una credencial en
el código; una comprobación de consistencia que falla) y 7 limpios. Cada caso es una rama de
funcionalidad revisada contra `main` con `harness review-code --mode manual`, con la configuración
que escribe `harness init` (`review.panel` en `enforce`), una comprobación de consistencia
(`python -m compileall -q src`) y revisores fixture que no llaman a ningún modelo
(`evaluation/deterministic/review_fixture_reviewer.py`):

1. `silent`: los revisores responden PASS sin hallazgos, así que cada hallazgo viene del propio
   harness (reglas que verifica una herramienta, sus propias comprobaciones, la comprobación de
   consistencia). Detección por dominio frente a la verdad (mismo archivo y misma línea o regla) y
   hallazgos sobre cambios limpios.
2. `repeat`: la misma revisión otra vez (caché global), con el conteo propio del fixture de las
   llamadas que recibió (`fixtureCalls`), junto a `tokens.modelCalls` del reporte. Sobre el código
   anterior a la ola 9 la re-revisión idéntica reportaba 34 llamadas al modelo mientras el fixture no
   recibía ninguna; desde #75 un acierto de la caché global reporta 0
   (`cache.identicalReReviewModelCalls` en `review-summary.json`).
3. `oracle` (`--no-cache`): los revisores responden los hallazgos sembrados de su dominio más dos
   señuelos cada uno: uno en la línea 9999 (fuera del diff) y uno sobre una regla fuera del catálogo
   sin evidencia; `droppedOutside` y `downgraded` del reporte se comparan con los señuelos enviados.
4. `testOnly`: un commit de solo pruebas encima y otra vez la revisión silenciosa: los revisores que
   corrieron y las respuestas que vinieron de la caché por revisor.

La tabla por dominio también dice si el revisor del dominio habría corrido (`domainReviewer`, su
razón de activación): un defecto sembrado que ninguna herramienta verifica queda en manos de un
revisor.

N02-b (revisores reales) usa el mismo corpus: `--reviewer-command '["python", "ADAPTER", ...]'`
(un argv JSON de un proveedor de comando del protocolo de proveedor 1.1 que responde las solicitudes
`review` con el contrato de salida del panel), opcionalmente `--reviewer-model`,
`--provider-env NAME` y `--reps N` (repite el paso 1 sin caché para medir la varianza). Sin él no se
llama a ningún modelo.

Comando (código del worktree):

```bash
SCRATCH=<scratch>/scratchpad
$SCRATCH/venvs/eval-w9/bin/python evaluation/deterministic/review_corpus.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/review --work $SCRATCH/work/review-w9
```

Archivos: `review-corpus.jsonl` (un registro por caso y paso), `review-summary.json`,
`review-catalog.json` (`harness review rules show` del proyecto del corpus), `environment.json`.
