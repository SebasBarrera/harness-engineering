# N01-a: escalera de verificación sobre un corpus sembrado

16 cambios sembrados sobre la biblioteca de inventario del bloque D (`evaluation/corpus/ladder`,
partes A y B en la línea base), cada uno una tarea en modo patch aplicada por el proveedor
`simulated`, con la configuración que escribe `harness init` (certificación bajo
`verification.ladder` con `defaultLevel: L1`, mutación ligera bajo `verification.mutation` en modo
`warn`). Ocho cambios son defectuosos (el defecto `path.endswith` de las sesiones de Haiku con su
criterio en L1 y en L3, un guardado que pierde el historial mientras ninguna prueba nombra el
criterio de ida y vuelta, un `total_value` que devuelve un float con una prueba que no lo ejercita,
una prueba sin aserciones y una prueba que pasa sobre la línea base, una línea de comandos con salida
incorrecta en L1 y en L3) y ocho son correctos (en L1, L2 y L3, cuatro de ellos con evidencia que no
alcanza el peldaño declarado).

La sonda de línea de comandos `cli_value` (criterio `ac_e_value` en L3) corre dos variantes, el
archivo de inventario y el archivo vacío, con cada aserción de valor acotada a su variante. Antes de
la ola 9 solo `differs` respetaba las `variants` de una aserción (#74), así que los primeros
resultados usaron solo el archivo de inventario; desde entonces se sondea también la parte del
criterio con el archivo vacío.

Verdad por caso (`cases.yaml`): `defective` (verificado de nuevo por las pruebas ocultas de la parte
del caso sobre una copia aparte con el cambio aplicado: `oracle`, `defectiveObserved`,
`truthConsistent`) y `evidenceAdequate` (las pruebas o sondas realmente alcanzan el peldaño
declarado). Señales por caso: `certification` (la corrida queda `NOT_CERTIFIED` o `PARTIAL`, o un
hallazgo `certification.*`), `mutation` (`tests.change-not-exercised`, `tests.weak`,
`tests.broken`), `testQuality` (otros hallazgos `tests.*`), `gateNotPassed` (el gate no es `PASSED`,
incluida una corrida que se detuvo antes de DECISION) y `any`. El resumen da la sensibilidad y la
especificidad de cada señal frente a ambas verdades. No se toma ninguna decisión; las esperas antes
de DECISION las responde la persona simulada del README del directorio (aquí: un preflight
`UNAVAILABLE` se continuaría sin certificar).

Comando (código del worktree):

```bash
SCRATCH=<scratch>/scratchpad
$SCRATCH/venvs/eval-w9/bin/python evaluation/deterministic/ladder_corpus.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/ladder --work $SCRATCH/work/ladder-w9
```

Archivos: `ladder-corpus.jsonl` (un registro por caso: estado y fase de la corrida, gate,
certificación por criterio con su evidencia, hallazgos, oráculo), `ladder-summary.json` (tabla y
tasas), `environment.json`.
