# Estructura generada

Esta página lista cada archivo y carpeta que el harness crea, para qué sirve y si conviene confirmarlo en Git. La lista sale de `harness init` y de una ejecución completa en un proyecto de prueba, contrastadas con el código. Lo que no se vio en esa ejecución se marca como «según el código».

En resumen: el harness separa lo que escribes tú (la configuración y las tareas), lo que es estado de trabajo local (se regenera y no se versiona) y el registro de las ejecuciones (se guarda fuera de tu repositorio).

## Qué crea `harness init`

- `.harness/project.yaml`: la configuración del proyecto.
- `.harness/task.example.yaml`: una tarea de ejemplo para copiar y editar (se omite con `--no-example-task`).
- Una línea `.harness/` en `.git/info/exclude`, para que Git ignore la carpeta sin tocar tu `.gitignore` (con `--ignore-file gitignore` va en el `.gitignore`; con `--no-gitignore` no se escribe).
- Solo con `--agent-skills`: `.claude/skills/harness/SKILL.md` y `.codex/skills/harness/SKILL.md`.

Todo lo demás aparece al usar el harness.

## En tu repositorio

| Ruta | Para qué sirve | ¿Se versiona? |
|---|---|---|
| `.harness/project.yaml` | Configuración: perfiles, validadores, capacidades, políticas, presupuesto, aislamiento, entrega. Es lo que gobierna cada ejecución; su digest queda registrado en cada una. | Tu decisión. Por defecto queda fuera de Git (la entrada de `.git/info/exclude`). Si el equipo quiere compartir las mismas reglas, quítale la exclusión o agrégalo con `git add -f`. Edítalo con el harness detenido: durante una ejecución el guardia de rutas vigila `.harness/` (según el código, `governance.protectExcludedPaths`). |
| `.harness/task.example.yaml` | Plantilla de tarea. | No. Cópiala a un archivo de tareas propio. |
| `.harness/architecture.md` y `.harness/architecture.json` | Estudio de la arquitectura del proyecto que hace el agente la primera vez y que las siguientes ejecuciones reutilizan sin otra consulta. `harness architecture refresh` lo vuelve a generar. | No, es una caché. Para fijar la arquitectura elegida usa el ADR. |
| `.harness/adr/ADR-0001-architecture.md` | Registro de la decisión de arquitectura de un proyecto nuevo, cuando la persona elige una opción con `harness architecture decide`. Según el código; no se generó en la prueba. | Sí conviene: es documentación de una decisión del equipo. |
| `.harness/cache/snapshot-cache.json` | Huellas de archivos para no volver a calcularlas en cada ejecución (`workspace.snapshotCache`). | No. |
| `.harness/check/<run-id>.json` | Estado que el harness escribe antes de llamar al agente para que `harness check` pueda ejecutar las mismas validaciones sin escribir en `.harness/`. | No. |
| `.harness/coverage/<run-id>.diffcov` y `.diffcov.json` | Datos de cobertura de las líneas modificadas, que alimentan el validador de cobertura del diff. | No. |
| `.harness/tmp/` | Copias temporales de trabajo. Normalmente vacía. | No. |
| `.harness/lease.json` | Marca de «hay un proceso del harness usando este directorio» (`governance.workspaceLease`). Existe solo mientras un comando ejecuta fases o registra una decisión; al terminar se borra. Contiene el pid, el equipo, el comando y un latido. Ver [Trabajo simultáneo](trabajo-simultaneo.md). | No. |
| `.harness/worktrees/<run-id>/` | Un worktree de Git por ejecución aislada (`run start --isolate worktree`), con su propio `.harness/` y un `isolation.json` que apunta al registro de origen. Se quita con `harness run cleanup`. | No. La rama de cada ejecución sí es Git. |
| `.harness/metrics/metrics.json`, `metrics.md`, `metrics.html` | Reporte de `harness metrics`: se escribe al ejecutar el comando. El HTML es autocontenido y se abre en cualquier navegador. Ver [Reporte de métricas](reporte-de-metricas.md). | No, se regenera. |
| `.harness/review/rules/`, `review/agents/`, `review/cache/` | Reglas de revisión del equipo (`DOMAIN.md`), revisores propios (`ID.md`) y la caché de respuestas del panel de revisión. Según el código; solo existen si los creas o usas el panel. | Reglas y revisores, sí; la caché, no. |
| `.harness/standards/` | Ruta por defecto donde el proyecto puede sobrescribir los estándares que trae el harness (`standards.path`). Según el código; opcional. | Sí, si la usas. |
| `.harness/audit/api-decisions.jsonl` | Un registro por intento de decisión hecha desde el tablero web. Según el código; solo con la API. | No. |
| `.claude/skills/harness/SKILL.md`, `.codex/skills/harness/SKILL.md` | Instrucciones que enseñan a un agente de Claude Code o Codex el flujo gobernado. Solo con `--agent-skills`. Son instrucciones: el harness sigue aplicando las compuertas. | Sí, si usas esos agentes en el equipo. |
| `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `__pycache__/` | No son del harness: los dejan los validadores de Python al correr. | Ignóralos como siempre. |

El cierre de una ejecución aprobada crea además una rama `harness/...` (en la prueba, `harness/run_<id>`); eso es Git, no un archivo del harness.

## Fuera de tu repositorio: el directorio de estado

Con `runtime.stateDir: auto` (lo que escribe `harness init`), el registro de las ejecuciones no vive en tu repositorio. Así un agente con acceso de escritura al proyecto no puede alcanzarlo, una limpieza del directorio no borra el historial y un solo tablero ve varios repositorios.

Dónde está, en orden de prioridad:

1. La variable de entorno `HARNESS_STATE_DIR`.
2. `$XDG_DATA_HOME/governed-harness/state`.
3. El directorio de datos de la plataforma: `~/Library/Application Support/governed-harness/state` en macOS, `%LOCALAPPDATA%\governed-harness\state` en Windows y `~/.local/share/governed-harness/state` en el resto.

`runtime.stateDir` también acepta una ruta absoluta o que empiece con `~/`. `harness doctor` muestra la ruta efectiva en la línea `state dir`, y `harness registry` lista los proyectos registrados.

Cada proyecto tiene una carpeta propia, `<id-del-proyecto>-<huella del repositorio>`, con:

| Ruta | Para qué sirve |
|---|---|
| `state.db` | Base SQLite con las ejecuciones, las tareas, los eventos encadenados, las evidencias y las decisiones. Es el registro auditable. |
| `artifacts/` | Los bytes de cada artefacto (diffs, salidas de herramientas, el ChangeSet), guardados por su digest, en `blobs/` y `metadata/`. Se redactan antes de guardarse. |
| `registry.json` | El id del proyecto y la ruta de su espacio de trabajo, para que el tablero y `harness registry` lo listen. |

Todos los worktrees de un mismo repositorio comparten esta carpeta (la huella sale del directorio común de Git).

No se versiona nada de esto. Sí conviene respaldarla si necesitas conservar el historial de decisiones: `harness export --bundle archivo.tar.gz` produce un paquete de evidencia portátil de una ejecución y `harness verify --bundle archivo.tar.gz` lo verifica sin el espacio de trabajo.

### El ancla de la cadena

Con `governance.chainAnchor: file` (lo que escribe `init`), después de cada comando que agrega eventos a una ejecución el harness copia la cabeza de la cadena de eventos a otro lugar: un archivo JSON por espacio de trabajo en `$HARNESS_ANCHOR_DIR`, o bien, sin esa variable, en la carpeta `anchors/` del directorio de datos de la plataforma (`~/Library/Application Support/governed-harness/anchors` en macOS, que fue donde apareció en la prueba aunque `HARNESS_STATE_DIR` apuntaba a otra carpeta). `harness verify` compara ambas copias. Sirve para detectar que alguien truncó el historial; no es a prueba de manipulación para quien tenga los permisos de tu usuario.

### Retención

`retention.artifactDays: 30` y `retention.eventDays: 365` (lo que escribe `init`) indican cuánto se conserva de las ejecuciones terminadas. Nada se borra solo: `harness gc` muestra el informe y `harness gc --apply` aplica la retención. Las ejecuciones abiertas nunca se tocan.

## Sin `runtime.stateDir`

Un `project.yaml` sin esa clave (por ejemplo, uno de la versión 1.0.0) conserva el comportamiento anterior: `state.db` y `artifacts/` viven dentro de `.harness/`.

## Qué sí hay que confirmar en Git

- El código que produce cada ejecución. El harness nunca hace commit en tu rama actual. Al cerrar crea una rama `harness/<run-id>` (configurable) con un commit del cambio aprobado y deja esos archivos en el índice; tú decides cuándo integrarlos.
- Opcionalmente: `project.yaml`, el ADR, las reglas y revisores propios y las instrucciones de agentes.
