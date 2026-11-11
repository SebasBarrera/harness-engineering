# P24: equivalencia de INTENT sin agentes

Cada tarea que los experimentos enviaron (o enviarían) al harness, evaluada con las reglas
deterministas de INTENT del harness con el que corre el script (`assess_intent`: C0, C1, C2, C3, T1).
Un archivo de tarea se carga como lo carga `harness task create` con `intake.criteriaPolicy: enforce`
(`load_task_file`). Un prompt de texto libre (los prompts de una línea, los incrementos casual y las
correcciones casual de reportes de bugs) se evalúa dos veces: como arma la tarea `harness do TEXT`
(`task_from_text`: el texto es la intención y el criterio) y como un archivo de tarea cuyo único
contenido es el texto como intención (sin criterios: regla C0). No se aplican las reglas que
necesitan un agente (A1, A3) o el espacio de trabajo y las respuestas (A2, P1). No se llamó a ningún
modelo.

Fuentes: `evaluation/tasks` (3 full, 3 poor, 3 casual), los 5 incrementos de
`evaluation/longitudinal/increments.yaml` (tarea estructurada y prompt casual), los 16 reportes de
bugs de `evaluation/longitudinal/bug_reports.yaml` (la tarea de corrección de un solo reporte que
arma `run_session.py` y su prompt casual), y las tareas de proyecto completo y por paso de
`evaluation/rides` y `evaluation/large` (armadas por `whole_task` y `step_task` de sus ejecutores).

Comando (código del worktree, 2026-10-06):

```bash
.venv/bin/python evaluation/deterministic/intent_equivalence.py --harness "$PWD/.venv/bin/harness" \
  --out evaluation/results-2.0.0/deterministic/intent-equivalence
```

Archivos: `intent-equivalence.jsonl` (un registro por tarea e interpretación: criterios,
requisitos, preguntas y preguntas por regla), `intent-equivalence-summary.json` (por fuente),
`environment.json`.
