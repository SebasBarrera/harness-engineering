# N04: validez de `harness metrics` frente a una medición externa

`evaluation/deterministic/metrics_validity.py` compara, para cada directorio de corrida, el documento
de `harness metrics --format json` con lo que se midió fuera del harness:

- **directorios de corrida medidos**: las 24 corridas de la suite de fricción (`../friction`), cuyo
  proveedor fixture anotó cada llamada que recibió con los tokens que reportó y sus propios segundos
  de reloj (`agent-calls.jsonl`), y cuyo ejecutor cronometró los comandos escritos
  (`runner-clock.json`). Para las corridas con agente de los otros evaluadores el mismo script lee
  los registros de `agentlib` (`agent-calls/call-*.json`: tokens de entrada, de salida, de lectura de
  caché y de creación de caché, costo, segundos de reloj).
- **proyectos de demostración**: cada proyecto que deja `scripts/demo_flows.py all --workdir` en el
  bloque 0 (sin lado externo: solo se comprueban la consistencia interna, las etiquetas de costo y
  los indicadores por persona).

Por métrica (llamadas al agente, tokens de entrada, de salida y de caché, costo reportado, segundos
de las llamadas al agente, segundos de reloj de la corrida frente al reloj del ejecutor): valor del
harness, valor externo, diferencia absoluta y relativa, si son iguales o no; cobertura (corridas en
las que había un valor disponible en ambos lados). Comprobaciones sobre cada documento: las llamadas
por tipo suman el total, las llamadas y los tokens por corrida suman los totales, los costos
estimados se mantienen aparte de los reportados, y no hay ningún indicador por persona (ninguna
clave que agrupe por actor, ningún id de actor humano de los eventos de la corrida en el documento,
RD-16).

Nota sobre el tiempo: el harness cronometra una llamada al agente desde el lanzamiento de su proceso;
el fixture se cronometra a sí mismo desde su propio inicio, así que los segundos de las llamadas al
agente difieren en el arranque del intérprete. El reloj del ejecutor incluye el arranque del proceso
propio del harness y el manejo de la salida de los comandos.

Comando (código del worktree, después de la suite de fricción y del bloque 0):

```bash
SCRATCH=<scratch>/scratchpad
.venv/bin/python evaluation/deterministic/metrics_validity.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/metrics-validity \
  --runs-glob "$SCRATCH/work/friction-w9/friction-*" \
  --demo-workdir $SCRATCH/work/block0-w9/demo --demo-state $SCRATCH/work/block0-w9/demo-state
```

Archivos: `metrics-validity.jsonl` (un registro por directorio de corrida o proyecto de
demostración), `metrics-validity-summary.json`, `environment.json`.
