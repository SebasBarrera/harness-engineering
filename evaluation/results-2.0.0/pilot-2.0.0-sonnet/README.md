# Piloto Sonnet de 2.0.0 (comprobación del aparato, una repetición)

El mismo diseño del piloto Haiku (`../pilot-2.0.0/README.md`) con `claude-sonnet-5-5`: un
escenario (brownfield, itsdangerous 2.2.0) y una repetición por condición. Es una comprobación del
aparato, no la campaña; ninguna afirmación sobre el harness se apoya solo en estos registros. Los
prompts estaban en inglés y la persona simulada usaba el modelo de la corrida con el prompt
anterior; la campaña usa prompts en español y una persona simulada fija (ver las enmiendas
fechadas en `evaluation/campaign/PREREGISTRO.md`).

## Cómo se produjo

```bash
EVAL_PILOT_MODEL=claude-sonnet-5-5 EVAL_PYTHON="$E/venv/Scripts/python.exe" EVAL_WORK="$E/work6" \
EVAL_CACHE="$E/cache" EVAL_EXTRA_PATH="$PWD/src" EVAL_CORE_REFERENCE_SRC="$E/ref-1.0.0/src" \
EVAL_OUT=<salida> EVAL_DRY=0 EVAL_PARALLEL=0 bash evaluation/launchers/pilot.sh
```

Fuente: el commit `e079846` de la rama de resultados del piloto, que solo añade documentación
sobre `develop` en `886926e`; el código del harness es el mismo del piloto Haiku. `agentSandbox`
está apagado (Windows, sin sandbox del sistema operativo). Los archivos son los mismos que en el
piloto Haiku (`pilot.jsonl`, `summary-blocks.json`, `environment.json`).

## Resultados

| Prompt | Condición | Fase final / estado | Desenlace | Pruebas ocultas | Costo (USD) | Tiempo (s) |
|---|---|---|---|---|---|---|
| completo | directa | — | — | 24/24 | 0,06 | 27 |
| completo | harness-core | CLOSURE / PASSED | aprobado | 24/24 | 0,08 | 86 |
| completo | harness | CLOSURE / PASSED | aprobado | 24/24 | 0,90 | 519 |
| completo | harness-anchored | CLOSURE / PASSED | aprobado | 24/24 | 0,87 | 590 |
| casual | harness | CLOSURE / PASSED | aprobado | 24/24 | 0,50 | 448 |

## Lectura honesta

- **Las cuatro corridas gobernadas llegaron a DECISION y cerraron aprobadas**, sin falla del
  aparato, incluida la del prompt casual. Todas pasaron por la aprobación humana del plan (bandera
  de riesgo de seguridad).
- **Con el prompt completo, las pruebas ocultas no distinguen las condiciones:** todas dan 24/24.
  El puntaje no dice cómo difieren las implementaciones; eso lo mide la comparación de
  implementaciones (ver su enmienda en el preregistro).
- **Costo y tiempo:** la directa es la más barata y rápida (0,06 USD, 27 s); el harness completo
  cuesta unas 14 veces más y tarda unas 19 veces más. Ese es el precio del gobierno: aclaración,
  arquitectura, pruebas de aceptación, plan, verificación, revisión y decisión.
- **Frente a Haiku:** con Sonnet ninguna corrida gobernada se bloqueó en VERIFICATION; con Haiku
  dos sí (y las pruebas ocultas confirmaron que esos cambios eran peores). Con una repetición no
  se generaliza.
- **Falta en este piloto:** la condición directa con el prompt casual y las del prompt pobre; se
  corren aparte.
