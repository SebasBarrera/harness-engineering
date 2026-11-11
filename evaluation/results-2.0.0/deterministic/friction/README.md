# N03-a: fricción de los cambios pequeños con el proveedor fixture

Dos cambios pequeños (S) sobre el proyecto de ejemplo (el descuento en el umbral y por encima de él,
con una prueba que nombra el requisito), cada uno también con un factor de riesgo inyectado: una
credencial literal en el código, una llamada a `eval` o un cambio del README fuera de las rutas de
las que la tarea es dueña (`ownedPaths` del archivo de tarea, `--owned` de `harness do`). Tres
condiciones, cada una sobre un proyecto nuevo con la configuración que escribe `harness init`:

- `full`: sin la sección `friction` (el flujo completo): `task create`, `run start`,
  `gate decide`;
- `fast-lane`: `harness do TEXT --criterion ... --owned ...`, luego `gate decide`;
- `fast-lane+pre-approval`: `harness do ... --pre-approve` (luego `gate decide` solo si la corrida
  todavía espera a una persona).

En DECISION la persona que mide escribe `gate decide --decision APPROVE` sea cual sea el gate (una
aprobación rechazada sale con 5 y el cambio no se entrega). El proveedor fixture (copiado de
`scripts/measure_friction.py`, sin modificar) escribe el cambio y responde los tipos de solo lectura
con resultados vacíos; reporta como uso una **estimación** de sus tokens (caracteres de la solicitud
/ 4) que el harness registra, y anota cada llamada (tipo, tokens estimados, sus propios segundos de
reloj) en `agent-calls.jsonl` dentro del directorio de la corrida, junto a `runner-clock.json` (los
comandos escritos y sus segundos): N04 usa ambos como la medición externa.

Medidas por corrida: comandos escritos, interacciones humanas que registró el harness
(`human.interactions`), llamadas al agente por tipo (registro del fixture) y llamadas y tokens
registrados (`budget show`), segundos de los comandos escritos en esta máquina (sin tiempo humano),
el carril (`lane.classified`) y su escalamiento (`lane.escalated`), la aprobación por adelantado, el
gate, si el cambio se entregó y si se entregó sin una persona (un falso carril rápido para una tarea
con riesgo).

Comando (código del worktree con la ola 9 fusionada). El 2026-10-06 corrió después de que terminara
la suite de sondas de fallas, sin ninguna otra suite corriendo (los primeros resultados compartieron
la máquina con las sondas):

```bash
SCRATCH=<scratch>/scratchpad
$SCRATCH/venvs/eval-w9/bin/python evaluation/deterministic/friction.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/friction --work $SCRATCH/work/friction-w9
```

Archivos: `friction.jsonl` (24 registros), `friction-summary.json` (por condición: medianas sin
riesgo; con un factor de riesgo: carril escalado o completo, falsos carriles rápidos, bloqueados),
`environment.json`.
