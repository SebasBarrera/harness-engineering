# Modelo de confianza

Qué se le puede confiar al harness, frente a qué tipo de agente, y qué no garantiza. Versión en
inglés: [Trust model](../architecture/trust-model.md). Lo que sigue describe la 2.0.1 con la
configuración que escribe `harness init`; un `project.yaml` escrito por una versión anterior
conserva el comportamiento anterior en cada clave que no tenga.

## Capas de confianza

El contenido del repositorio es dato, no política. Un archivo puede traer inyección de prompts o
scripts maliciosos. Por eso el harness separa:

- los invariantes del núcleo, inmutables;
- la política organizacional bloqueada;
- la configuración validada del proyecto;
- el contenido del espacio de trabajo, no confiable;
- las herramientas y extensiones externas;
- la salida probabilística del modelo.

Una capa de más confianza puede restringir a una de menos confianza. Una capa de menos confianza no
puede relajar una restricción de una de más confianza.

## Tres tipos de agente

El harness corre en la máquina de quien desarrolla, con su usuario del sistema operativo. Lo que
resiste depende de lo que el agente intente.

| Agente | Qué hace | Qué da el harness |
|---|---|---|
| **Honesto** | Sigue la tarea y reporta lo que hizo, pero puede equivocarse. | Las nueve fases siempre corren; los validadores corren sin shell y su código de salida es la evidencia, no el resumen del agente; un estado no exitoso nunca se vuelve éxito; la persona decide sobre el digest exacto del ChangeSet. |
| **Descuidado** | Edita fuera de la tarea, borra o debilita pruebas, afirma verificaciones que no corrió, sigue editando después del gate. | Solo las rutas propias entran al ChangeSet y las ediciones en rutas excluidas son hallazgos; el detector de controles debilitados (una heurística sobre el diff) señala pruebas borradas, aserciones eliminadas y linters silenciados; una afirmación sin evidencia es un hallazgo `agent.unsupported-claim`; cualquier edición después del gate cambia el digest, así que una aprobación anterior se rechaza por obsoleta. |
| **Adversario** | Intenta que un cambio no aprobado parezca aprobado: falsificar una decisión, reescribir el registro, escapar del confinamiento. | Protección parcial. Se rechaza un id de actor en el espacio `agent.`, `validator.` o `harness.`; con la sección `api`, la API local liga la decisión al usuario del token autenticado; en macOS y Linux el sandbox del agente deja `.harness/`, `.git` y el registro de ejecuciones en solo lectura para el proceso del agente y los procesos que lanza; `harness verify` detecta una cadena editada, reordenada o truncada mientras no se haya reescrito también el ancla. Todo lo de [Qué no garantiza](#que-no-garantiza) queda abierto a este agente. |

El objetivo de diseño son el agente honesto y el descuidado: el harness convierte sus errores en
hallazgos registrados que bloquean. Frente a un proceso adversario que corre con los permisos del
usuario, sube el costo del ataque y deja rastro, pero no es una frontera de seguridad.

## Qué no garantiza

### La identidad de quien decide

El id del actor de una decisión humana es **declarado, no autenticado**
(`src/governed_harness/domain/actors.py`, `require_human_actor`). En la CLI sale de `--actor`, de
la identidad de Git con `governance.deciderIdentity: git` (lo que escribe `harness init`) o del
valor por defecto configurado. Cualquier proceso que pueda ejecutar `harness gate decide` puede
pasar cualquier id fuera de los espacios reservados, y cualquiera puede cambiar
`git config user.email`. La comprobación del espacio de nombres impide que un agente decida con su
propio id reconocible; no prueba quién escribió el comando.

La excepción es la API local: con la sección `api` que escribe `harness init`, todas las rutas
exigen un token bearer, una decisión requiere el rol `reviewer` o `admin`, y un `actor_id` que no
es el usuario del token se rechaza con 403 (`src/governed_harness/api/app.py`). Los tokens salen
de variables de entorno del usuario que inicia el servidor, así que un proceso con ese entorno
también puede leerlos. Sin la sección `api`, la API no autentica nada.

### La barrera en modo embebido

En modo embebido la sesión del agente conduce la ejecución con `harness mcp serve`. El servidor MCP
no expone ninguna herramienta de decisión, y sus instrucciones le dicen a la sesión que nunca
decida por una persona (`src/governed_harness/embedded/mcp_server.py`, `HUMAN_ONLY` e
`INSTRUCTIONS`). Es una instrucción de texto más la ausencia de una herramienta: una sesión que
además tenga shell puede ejecutar `harness gate decide` por su cuenta, con cualquier id de aspecto
humano. La sesión no es un proceso que el harness lance, así que `runtime.agentSandbox` tampoco la
confina.

### Integridad frente a un proceso con los permisos del usuario

Los eventos se encadenan con digests SHA-256 sin clave
(`src/governed_harness/events/sqlite_store.py`). La cadena detecta un evento editado o reordenado
y el ancla (`governance.chainAnchor`, que `harness init` pone en `file`) detecta una cadena
truncada, pero un proceso con los permisos del usuario puede reescribir el almacén SQLite,
recalcular todos los digests y reescribir el archivo del ancla o la nota de Git: ninguno de los dos
es a prueba de manipulación (`src/governed_harness/events/anchor.py`). La evidencia no lleva firma
ni sello de tiempo externos. El registro detecta accidentes y ediciones parciales, y reconstruye lo
que pasó mientras nadie lo reescriba.

### Aislamiento en Windows, y de los validadores en todas partes

El confinamiento de escritura de los proveedores de agente existe solo en macOS (`sandbox-exec`) y
en Linux con bubblewrap (`bwrap`) (`src/governed_harness/runtime/sandbox.py`). En Windows, o en
Linux sin `bwrap`, `runtime.agentSandbox: enforce` bloquea `IMPLEMENTATION` antes de lanzar el
proveedor (un hallazgo `HIGH` `sandbox.unavailable`); la única forma de correr el agente ahí es
`agentSandbox: off`, que lo ejecuta con los permisos del usuario y sin las protecciones que
dependen del sandbox (estado en solo lectura, red negada). Los validadores y las extensiones no se
confinan en ninguna plataforma: un comando autorizado conserva los permisos de sistema de archivos,
red, CPU y memoria del usuario. Ver
[Esto no es un sandbox](https://github.com/SebasBarrera/harness-engineering/blob/develop/README.es.md#esto-no-es-un-sandbox)
y [SECURITY.md](https://github.com/SebasBarrera/harness-engineering/blob/develop/SECURITY.md).

## Atribución declarada, no no repudio

Como la identidad de quien decide no está autenticada y el registro no lleva una firma con clave,
el harness ofrece **atribución declarada**: cada decisión registra quién dice haber decidido, sobre
qué digest, con qué justificación y de dónde salió la identidad, en una cadena que detecta
ediciones posteriores accidentales o parciales. **No** ofrece no repudio: una persona puede negar
una decisión de la CLI registrada con su id, y el registro por sí solo no puede probar lo
contrario. Con la autenticación por token de la API, la atribución es a quien tiene el token, que
es más fuerte pero sigue siendo local a la máquina.

El catálogo y el preregistro de la evaluación, escritos antes de esta página, usaban «no repudio»
para la prueba de identidad de quien decide (H16); el catálogo ahora dice atribución declarada, y el
preregistro conserva sus criterios con una nota fechada. H16 mide qué intentos de decisión se
rechazan, no si una decisión se puede repudiar.
