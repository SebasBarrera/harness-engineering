# Comparación de implementaciones del piloto Haiku de 2.0.0 (exploratoria)

Análisis determinista, sin llamadas a modelos, de **cómo** difieren las implementaciones de las cinco
corridas del piloto Haiku (brownfield, itsdangerous 2.2.0, `claude-haiku-4-5-20251001`, una
repetición), más allá de las pruebas ocultas. Las métricas son las de la enmienda del 2026-11-11
«comparación de implementaciones» de [`PREREGISTRO.md`](../../../campaign/PREREGISTRO.md). Sobre este
piloto el análisis es **exploratorio**: las corridas ya existían cuando se fijaron las métricas, hay
una sola repetición por condición y no se saca ninguna conclusión sobre el harness de estos números.

## Cómo se produjo

```bash
E=<eval dir>   # venv de la evaluación (ruff 0.17.4, mypy 2.4.1, bandit 1.9.4), work5 del piloto
"$E/venv/Scripts/python.exe" evaluation/analysis/compare_implementations.py \
  --runs "$E/work5/runs/pilot" --out evaluation/results-2.0.0/pilot-2.0.0/comparison
```

Las versiones de las herramientas quedan en cada JSON (`tools`). Solo se versionan métricas
derivadas: los espacios de trabajo, los parches y las llamadas del agente no están en Git. Las
rutas se reemplazan con `agentlib.anonymize`; los extractos de código son del proyecto bajo
prueba (itsdangerous) tal como lo dejó el agente.

## Archivos

| Archivo | Contenido |
|---|---|
| `comparison.md` | Tablas por (prompt, modelo): resultado y requisitos, buenas y malas prácticas, diferencias generadas de los números y diff del código fuente de cada corrida |
| `per-run/*.json` | Una medida por corrida (esquema `compare-implementations/1`) |

## Lo que muestran los números

**Las corridas detenidas no eran peores en las pruebas ocultas.** El registro del piloto
(`pilot.jsonl`) da 18/24 a `harness-anchored` y a `harness` con prompt casual, medidas sobre la
copia del aparato (`quarantine-ws`). En las dos el parche en cuarentena no aplicó a esa copia
(`measures.patchApply.exitCode: 1`: las pruebas de aceptación conservadas «already exists» y, con
CRLF, «patch does not apply»), y los archivos cambiados de la copia son solo `tests/acceptance/*`:
se midió el código fuente de la base. Reconstruidos desde su parche (exportación de la base más el
parche con LF), los dos cambios pasan **24/24** pruebas ocultas (`measuredOn:
quarantine-reconstructed`, `quarantine.apparatusCopy` guarda la medida anterior). El harness los
detuvo por sus propios validadores, no por comportamiento que verifiquen las pruebas ocultas:

- `harness-anchored`: 8 hallazgos nuevos de Ruff, todos en pruebas (`F401` 4, `I001` 4); la suite
  visible del árbol reconstruido termina con código 0; 0 errores nuevos de Mypy con la
  configuración del proyecto.
- `harness` casual: 2 casos de pruebas de aceptación fallan (de 97 en los archivos de prueba
  cambiados). Son `test_rejects_plus_character_str/bytes`, que esperan rechazo para `"SGVsbG8r"`,
  una cadena válida (la `r` no es `+`); el agente los disputó en su autorreporte (2 pruebas
  disputadas, 0 eventos de disputa en el registro).

**Prompt casual (comparación principal; falta la corrida `direct`).** El piloto no tiene corrida
`direct` con prompt casual, así que no hay par. De la corrida gobernada: 11 preguntas en INTENT,
11 respuestas, la tarea quedó con 4 criterios y 16 requisitos (el prompt no daba ninguno), 4
supuestos registrados, 2 ciclos de corrección y 3 validadores que bloquearon
(`harness.acceptance-tests`, `harness.ratchet`, `python.pytest`). El cambio tiene 34 archivos, 29
fuera del alcance (scripts sueltos de depuración en la raíz del repositorio; 15 de ellos se llaman
`test_*.py` y cuentan como archivos de prueba en las tablas), 62 hallazgos nuevos de Ruff (32 en
scripts de la raíz que no son de prueba, 30 en archivos de prueba) y, en `encoding.py`, una función de complejidad 20 y
60 líneas, 14 números mágicos nuevos y 1 `raise` dentro de `except` sin `from`. Costo 2,805 USD.

**Prompt completo (secundaria), las cuatro condiciones pasan 24/24.** Diferencias del código:

- *Fuente*: `direct` deja la complejidad máxima en 11 (1 función nueva por encima de 10) y la
  función más larga en 45 líneas; `harness-core` 6 y 31, `harness` 10 y 38, `harness-anchored` 10
  y 39. `harness-anchored` añade 2 números mágicos; las demás, 0.
- *Pruebas*: funciones de prueba nuevas 3 (`direct`), 12 (`harness-core`), 43 (`harness`) y 42
  (`harness-anchored`); pruebas negativas con `raises` 2, 13, 31 y 30. `direct` usa más
  parametrización (27 casos parametrizados frente a 0, 0 y 20) y, salvo `harness-anchored` (34),
  más literales de borde (19 frente a 7 y 13).
- *A favor de `direct`*: cobertura de las líneas fuente cambiadas 0,9524 frente a 0,6842
  (`harness`) y 0,8889 (`harness-anchored`) (`harness-core`: 1); 0 hallazgos nuevos de Ruff
  frente a 6 y 8 de `harness` y `harness-anchored` (imports sin usar y orden de imports en sus
  pruebas); costo 0,19 USD frente a 0,84 y 1,58 (`harness-core`: 0,12).
- *Recuento por dirección declarada* (`comparison.md`): `harness-core` 9 a favor y 2 en contra;
  `harness` 7 y 6; `harness-anchored` 8 y 6. Los literales de credencial nuevos en pruebas (9 en
  `harness`, 1 en `harness-anchored`: claves de firma de ejemplo como `"my-secret-key"`) se
  cuentan aparte y sin dirección.

## Observaciones del aparato

- Junto a la corrida casual hay un directorio `brownfield-casual-harness-claude-haiku-4-5-20261111T160625Z/ws/`
  (6 scripts `.py` de depuración) con un nombre que no es el de ninguna corrida (el modelo sin la
  fecha): el script lo lista como omitido. Que esté fuera del espacio de trabajo de la corrida se
  registra como observación; no se investigó cómo se escribió.
- El defecto de la medida en cuarentena está en `evaluation/run_eval.py:measure_quarantine` y no
  se corrige aquí: afecta a las cifras 18/24 del piloto y a cualquier corrida detenida de la
  campaña medida con el aparato actual.
