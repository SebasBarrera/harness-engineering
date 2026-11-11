# Piloto Haiku de 2.0.0 (comprobación del aparato, una repetición)

El piloto comprueba que el aparato de evaluación lleva cada condición de principio a fin con un
modelo real antes de la campaña. **No** es la campaña: un modelo (`claude-haiku-4-5-20251001`),
un escenario (brownfield, itsdangerous 2.2.0) y una repetición por condición. Ninguna afirmación
sobre el harness se apoya solo en estos cinco registros; la campaña repite cada celda (ver las
enmiendas fechadas en `evaluation/campaign/PREREGISTRO.md`). Los prompts de este piloto estaban en
inglés; los de la campaña van en español.

## Cómo se produjo

```bash
E=<directorio de evaluación>   # venv con las herramientas, caché con el archivo de itsdangerous
EVAL_PYTHON="$E/venv/Scripts/python.exe" EVAL_WORK="$E/work5" EVAL_CACHE="$E/cache" \
EVAL_EXTRA_PATH="$PWD/src" EVAL_CORE_REFERENCE_SRC="$E/ref-1.0.0/src" \
EVAL_OUT=evaluation/results-2.0.0/pilot-2.0.0 EVAL_DRY=0 EVAL_PARALLEL=0 \
  bash evaluation/launchers/pilot.sh
```

En Windows las rutas se pasan en forma mixta (`cygpath -m`). Fuente: `develop` en `886926e`
(`environment.json`, `source.commit`, sin cambios locales); el harness corrió desde ese árbol de
fuentes, cuya cadena de versión todavía dice 1.0.0 porque la versión se fija en la rama de release.
`agentSandbox` está apagado: en Windows no hay sandbox del sistema operativo, y
`environment.json` lo declara.

## Archivos

| Archivo | Contenido |
|---|---|
| `pilot.jsonl` | Un registro por corrida: condición, prompt, fases y hallazgos del harness, medidas (pruebas ocultas), uso y costo del agente, esperas y decisiones de la persona simulada |
| `summary-blocks.json` | Los mismos registros resumidos por bloque |
| `environment.json` | Versiones de herramientas, sistema operativo, versión de Claude Code, commit de la fuente |

Los logs del lanzador (`log*.txt`) y los directorios crudos de cada corrida (workspaces,
registros de runs, llamadas al agente) no se guardan en Git (`.gitignore`); los tiempos salen de
`pilot.jsonl`.

## Resultados

| Prompt | Condición | Fase final / estado | Desenlace | Pruebas ocultas | Costo (USD) | Tiempo (s) |
|---|---|---|---|---|---|---|
| completo | directa | — | — | 24/24 | 0,19 | 177 |
| completo | harness-core | CLOSURE / PASSED | aprobado | 24/24 | 0,12 | 162 |
| completo | harness | CLOSURE / PASSED | aprobado | 24/24 | 0,84 | 775 |
| completo | harness-anchored | VERIFICATION / FAILED | detenido | 18/24 registrado, no válido (ver abajo) | 1,58 | 1190 |
| casual | harness | VERIFICATION / FAILED | detenido | 18/24 registrado, no válido (ver abajo) | 2,81 | 2157 |

## Lectura honesta de los resultados

- **Todas las condiciones corrieron de principio a fin sin falla del aparato.** Las dos corridas
  gobernadas que no llegaron a DECISION se detuvieron en VERIFICATION por validadores obligatorios
  que el agente no logró satisfacer tras sus ciclos de corrección, no por un defecto del harness ni
  del aparato.
- **Corrección: el 18/24 de las dos corridas detenidas no mide el cambio del agente.** Es un
  defecto del aparato (`evaluation/run_eval.py`, `measure_quarantine`): el parche en cuarentena no
  se aplicó a la copia medida (`measures.patchApply.exitCode: 1` en `pilot.jsonl`). Las pruebas de
  aceptación conservadas ya estaban en el workspace («already exists») y, en Windows, el parche se
  escribió con CRLF («patch does not apply»). La copia medida tenía el código de la línea base, y su
  resultado (18/24) es el de la línea base. Reconstruidos desde sus parches (exportación limpia de la
  base y parche con LF, `evaluation/analysis/compare_implementations.py`), **los dos cambios pasan
  24/24 pruebas ocultas**. Una versión anterior de este README decía que el harness había bloqueado
  dos cambios que eran peores; esa lectura era errónea y se retira.
- **Por qué los detuvo el harness**, según sus propios validadores (no por el comportamiento que
  miden las pruebas ocultas):
  - *harness-anchored*: 8 violaciones nuevas de ruff frente a la línea base, todas en archivos de
    prueba (F401 e I001; `ratchet.regressed`); la suite visible pasa.
  - *casual*: fallan 2 pruebas de aceptación congeladas que esperan el rechazo de una cadena base64
    válida; el agente las disputó en su autorreporte, pero la disputa no se abrió porque también
    fallaban otros validadores (ruff), y la regla solo abre una disputa cuando las pruebas
    disputadas son lo único que falla.
  Son detenciones por calidad del código y por una prueba de aceptación errónea, no por un cambio
  incorrecto: el costo de un gobierno estricto, que la campaña debe medir.
- **Costo:** las condiciones gobernadas cuestan más que la directa (core 0,6×, harness completo
  4,5×, anchored 8,4×, casual 15×, en USD). Las corridas que fallan son las más caras porque los
  ciclos de corrección repiten las llamadas de implementación.
- **Una repetición:** una comprobación previa del aparato, en una rama anterior al release, tuvo la
  corrida anchored aprobada con 24/24 y la casual detenida por el tope de tokens por llamada.
  Diferencias de ese tamaño entre corridas únicas son esperables; la campaña las mide con
  repeticiones.
- **Falta en este piloto:** la condición directa con el prompt casual. Se corre aparte para
  comparar el prompt pobre sin y con harness.
