# Trabajo simultáneo sobre una misma funcionalidad

Pregunta: ¿pueden dos personas trabajar a la vez en la misma funcionalidad usando el harness? Esta página responde con lo que el código hace y con experimentos que se ejecutaron con el harness 1.0.0 y el proveedor `simulated`. Lo que no se probó se marca.

## Respuesta corta

- **Sí, con límites: ejecuciones paralelas y aisladas en una misma máquina.** Con `harness run start --isolate worktree`, cada ejecución corre en su propio worktree de Git y su propia rama. Se probaron tres ejecuciones al mismo tiempo sobre el mismo repositorio y dos personas aprobando, cada una, una ejecución distinta. Las ejecuciones no se afectan entre sí mientras corren.
- **No hay coordinación de contenido.** El harness no fusiona el trabajo de dos ejecuciones ni avisa que tocan los mismos archivos. Si dos ejecuciones modifican las mismas líneas, el conflicto aparece al integrar las ramas con Git, como cualquier otro conflicto (se probó).
- **No hay coordinación entre máquinas.** Dos clones del repositorio, o dos personas con cuentas distintas, no comparten ejecuciones, bloqueos ni decisiones. Lo único que comparten es Git (se probó con dos clones).
- **En un mismo directorio de trabajo, sin aislamiento, no es seguro.** Solo hay un candado que cubre el momento en que un proceso del harness está ejecutando; entre comandos, una ejecución que espera decisión no protege el árbol (se probó).

## Cómo hacerlo (misma máquina, mismo usuario del sistema)

Ana y Beto trabajan sobre el mismo repositorio, cada uno con su tarea o con una parte de la funcionalidad.

1. Parten del mismo punto. Un repositorio de Git con al menos un commit y el harness inicializado (`harness init`). El aislamiento necesita eso; sin commits la ejecución se rechaza como error de configuración.
2. Cada persona crea su tarea con un `taskId` propio.

   ```bash
   harness task create --file tarea-ana.yaml
   harness task create --file tarea-beto.yaml
   ```

3. Cada una lanza su ejecución aislada, sin esperar a la otra.

   ```bash
   harness run start --task tarea_ana --isolate worktree
   harness run start --task tarea_beto --isolate worktree
   ```

   Para que sea el modo por defecto, pon `workspace.isolation.mode: worktree` en `.harness/project.yaml`. `harness init` escribe `mode: none`.

4. Ambas terminan con código 4 (esperan decisión). Cada persona revisa y decide su ejecución, con su identificador:

   ```bash
   harness review --run <run-id-de-ana>
   harness gate decide --run <run-id-de-ana> --decision APPROVE \
     --change-set-digest sha256:<digest> --actor human.ana --rationale "..."
   ```

   `harness inbox` lista todo lo que espera decisión en el proyecto, de cualquier persona.

5. Cada aprobación crea una rama `harness/<taskId>-<runId>` con un commit del cambio aprobado. El harness no integra esas ramas: se integran con Git (merge, rebase o pull request).
6. Se integra primero una rama. **La ejecución de la segunda persona se hizo sobre el estado anterior.** Para que el harness verifique el resultado combinado, la segunda persona lanza una ejecución nueva después del merge: el worktree parte de la rama base actual (y, si hay un remoto, de la copia actualizada del remoto). Se comprobó que un worktree creado después del merge incluye el cambio ya integrado.
7. Cuando una ejecución cerró, `harness run cleanup --run <run-id>` quita su worktree. Solo lo hace si el worktree lo creó el harness y no tiene cambios sin confirmar; nunca borra la rama.

### Qué evita que una ejecución afecte a la otra

| Mecanismo | Qué hace | Evidencia |
|---|---|---|
| Worktree y rama por ejecución | Cada ejecución modifica archivos en `.harness/worktrees/<run-id>/`, no en tu directorio de trabajo. Tu árbol no recibe sus archivos. | Tres ejecuciones simultáneas terminaron en `DECISION`; el archivo `tests/test_par_a.py` de una de ellas existía solo en su worktree y no en el árbol principal; `harness verify` válido para todas. |
| Candado por directorio (`governance.workspaceLease`) | `.harness/lease.json` se crea de forma exclusiva mientras un comando ejecuta fases o registra una decisión. En un worktree el candado está en su propio `.harness/`, así que no choca con el de otro. | El código toma el candado del worktree, no el del directorio principal. |
| Registro común | El worktree lleva un `isolation.json` que apunta al registro de ejecuciones de origen, así `status`, `review` y `gate decide` funcionan desde cualquier directorio y todos ven las mismas ejecuciones. | `harness run list` desde el árbol principal mostró las ejecuciones de los tres worktrees. |
| Colisiones | Si la rama o el directorio ya existen, la ejecución queda `BLOCKED` con `destructive-collision`; el harness nunca reinicia, borra ni reutiliza lo que no creó. `run continue` reintenta cuando resolviste la colisión. | Según el código; la rama lleva el identificador de ejecución, así que en el uso normal no colisiona. |
| Tarea fijada (`governance.pinTaskRevision`) | Una tarea con una ejecución abierta no se puede reemplazar. | Ver abajo. |
| Decisión ligada al digest | La aprobación vale para el ChangeSet exacto de esa ejecución. Si el contenido cambia después, queda obsoleta. | Ver abajo. |

**Tarea fijada.** Con `task create` sobre un identificador que ya tiene una ejecución abierta, el comando sale con código 5 y dice qué ejecución la usa:

```text
Error: task task_lease has open run(s) run_dede5f63c93748fbad7d9eeaf60437d6; its task cannot be replaced while they run. Create the task under a new id, cancel the run, or revise it with harness task clarify during INTENT
```

**Decisión ligada al digest.** Después de editar un archivo del ChangeSet mientras la ejecución esperaba, `gate decide` con el digest anterior se rechazó:

```text
Error: decision digest does not match the current ChangeSet; prior approval is stale
Hint: The current ChangeSet digest is sha256:b291...081d. No decision has been recorded on the current digest.
```

## Restricciones

- **Misma máquina y mismo usuario del sistema.** El registro de ejecuciones es una base SQLite en el directorio de estado del usuario (ver [Estructura generada](estructura-generada.md)). Dos personas en el mismo equipo con usuarios distintos tendrían registros distintos. Apuntar `runtime.stateDir` o `HARNESS_STATE_DIR` a una carpeta compartida no se probó y no se recomienda: SQLite no está pensado para uso concurrente sobre carpetas de red.
- **Requiere Git** con al menos un commit. Cada worktree es un checkout completo: ocupa espacio, y las dependencias que vivan dentro del repositorio (`node_modules`, un `.venv`) no están en el worktree. En las pruebas los validadores de Python corrieron con las herramientas del `PATH`; un proyecto que dependa de un entorno dentro del árbol necesita instalarlo en cada worktree (no se probó).
- **La configuración se copia al crear el worktree.** Un cambio posterior en `.harness/project.yaml` no llega a los worktrees que ya existen.
- **La identidad es declarada.** `--actor` se registra pero no se autentica; por defecto es el usuario de Git. El tablero web sí exige un token con rol (ver [Seguimiento](seguimiento.md)).
- **La integración es de Git.** El harness no resuelve conflictos ni reordena ramas.
- **Cada ejecución se aprueba por separado**, con el digest de su propio ChangeSet. Aprobar la ejecución de Ana no aprueba la de Beto.

## Lo que no está soportado, por qué y qué riesgo tiene

### Dos ejecuciones en el mismo directorio, sin aislamiento

El candado solo existe mientras un proceso del harness ejecuta fases o registra una decisión. Si dos comandos `run start` coinciden, el segundo se rechaza con código 5. Se probó con un candado de otro proceso vivo:

```text
Error: the workspace is in use by run run_other (run start, pid 95926 on CO000JDY4XLL2FL, since 2026-10-09T00:00:00+00:00); wait for it to finish or cancel it (harness run cancel). A holder that no longer runs is taken over after its heartbeat is 60 s old
```

Un candado cuyo proceso ya no existe en la misma máquina se toma de inmediato; si el titular está en otro equipo o no se puede comprobar, se toma cuando su latido tiene más de 60 segundos. Los comandos de solo lectura (`run list`, `status`) no se bloquean.

Pero el candado se libera al terminar el comando. Una ejecución que espera tu decisión ya no lo tiene. En una prueba, una segunda ejecución sobre el mismo directorio:

- Arrancó sobre un árbol con los cambios sin confirmar de la primera (hallazgo `environment.dirty-tree`: «The run starts on 2 uncommitted change(s)»).
- Falló en `VERIFICATION`: su parche reemplazó la misma función y rompió la prueba que la primera había agregado (`test_req_discount_at_threshold`).
- Y si una persona edita a mano un archivo del ChangeSet de una ejecución en `DECISION`, esa aprobación queda obsoleta (arriba).

El riesgo es mezclar el trabajo de dos ejecuciones en un solo árbol: resultados que no corresponden a ninguna de las dos. Por eso, con más de una ejecución abierta, usa siempre `--isolate worktree`.

### Dos personas en máquinas distintas, o en clones distintos

No hay un servidor, un registro compartido ni un candado de red. Cada clon tiene su propio directorio de estado y su propio candado. Se probó con dos clones del mismo repositorio y el mismo `project.yaml`:

- El clon B aceptó una tarea con el mismo identificador que una ejecución abierta del clon A y la ejecutó sin ninguna advertencia.
- Cada clon vio solo sus ejecuciones en `harness run list`.
- Las aprobaciones de uno no existen para el otro.

Riesgos: el trabajo duplicado, dos aprobaciones del mismo cambio que no se enteran una de la otra y conflictos que solo se descubren al integrar. Lo único compartido es Git. Si necesitan colaborar entre equipos, coordinen por fuera del harness: repartan las tareas, trabajen en ramas distintas (el aislamiento nombra las ramas con el identificador de ejecución, que es único), y revisen y fusionen mediante pull requests (`harness pr create` y `harness pr publish` existen para eso; ver la [referencia de la CLI](../reference/cli.md)). En CI, `harness verify-approval` comprueba que un rango de commits es un ChangeSet que una persona aprobó, a partir de un paquete de evidencia (`harness export`).

### Dos personas en una misma ejecución

La decisión final la toma una persona, con una sola ejecución y un solo digest. No existe un flujo de «dos revisores» ni de edición compartida de una ejecución. Si Ana pide cambios (`REQUEST_CHANGES`), la ejecución se retoma con `harness run continue`; no hay un modo en que Beto trabaje en paralelo sobre esa misma ejecución. La bandeja (`harness inbox`) permite decidir varias ejecuciones distintas, cada una con su propio digest.

### Partir una funcionalidad entre dos personas con el harness

El harness puede descomponer una tarea grande en sub-tareas gobernadas (`planning.decomposition`, `harness plan show`), pero ese plan no está pensado para repartirse entre personas y repartirlo no se probó. La división en partes independientes se hace antes, al crear las tareas; el orden de integración y el cuidado de que no se pisen los mismos archivos es de las personas. Si dos tareas modifican los mismos archivos, lo recomendable es que se ejecuten una tras otra: integra la primera y lanza la segunda después.
