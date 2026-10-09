# Primeros pasos

Esta página te lleva de cero a tu primera ejecución gobernada. No necesitas conocer cómo funciona el harness por dentro. Los comandos de ejecución y consulta de esta guía se probaron con el harness 1.0.0 y el proveedor `simulated`, que no llama a ningún modelo. Los de instalación desde PyPI y Docker no se pudieron ejecutar sin red y se marcan como tales en las guías de instalación.

## Qué problema resuelve

Un agente de IA puede escribir código, pero nada garantiza por sí solo que haya probado lo que dice haber probado, que no haya tocado archivos ajenos a la tarea o que una persona haya aprobado exactamente el cambio que quedó en el repositorio. Si le pides «ejecuta las pruebas» en un prompt, la respuesta depende de que el agente lo haga.

## Cómo lo resuelve

El harness no es un agente ni un modelo: es el entorno que rodea al agente y hace cumplir las reglas.

- Recorre cada tarea por nueve fases fijas (intención, descubrimiento, especificación, planificación, implementación, verificación, revisión independiente, decisión y cierre). Ninguna se puede quitar.
- Ejecuta él mismo los validadores (pruebas, linters, tipos) y guarda su salida como evidencia. «Las pruebas pasaron» deja de ser una afirmación del agente.
- Calcula el conjunto exacto de archivos que cambió la ejecución frente al estado inicial (el ChangeSet) y le asigna un digest, una huella SHA-256.
- Evalúa una compuerta (gate) que falla cerrado: si algo no pasó, no se puede aprobar de forma normal.
- Exige una decisión humana ligada a ese digest. Si el código cambia después de aprobar, la aprobación deja de valer.

El harness nunca hace commit en tu rama actual. Revisa el cambio y confirma tú.

## Qué necesitas

- Python 3.12 o superior.
- Git: los puntos de partida y los ChangeSets salen del repositorio.
- Para proyectos Node.js, Node.js LTS y npm.

El harness es un paquete de Python: no se instala con npm ni con npx.

## Instalarlo

La forma más corta, con un entorno virtual:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install governed-agent-harness
harness --version
```

La instalación desde PyPI depende de que el mantenedor la haya habilitado; mientras tanto instala el wheel del release. Todas las opciones (wheel, Docker, pipx, versión concreta, verificación) están en [Instalación](instalacion.md) y [Instalación avanzada](instalacion-avanzada.md).

Para el tablero web local instala también el extra `api`: `pip install "governed-agent-harness[api]"`.

## Ejecutarlo localmente

No hay servidores que levantar: el harness es una herramienta de línea de comandos que guarda su estado en archivos locales. Todo se hace desde el directorio del proyecto (o con `--path`).

Antes de usarlo en un proyecto, comprueba el entorno:

```bash
harness doctor --path .
```

Cada comprobación que no esté en `PASSED` indica cómo arreglarla. Un `WARNING` (por ejemplo, Git sin `user.name`) no es un fallo. Un fallo obligatorio sale con código 2.

## Tu primera ejecución gobernada

Necesitas un repositorio de Git con al menos un commit. Si no tienes uno a mano, consulta [Proyecto nuevo](proyecto-nuevo.md) para crear uno mínimo.

1. Inicializa el harness en el repositorio.

   ```bash
   harness init
   ```

   Escribe `.harness/project.yaml`, una tarea de ejemplo en `.harness/task.example.yaml` y agrega `.harness/` a `.git/info/exclude` para que tu árbol siga limpio. Imprime los perfiles detectados y los siguientes comandos.

2. Escribe una tarea. Es un archivo YAML con la intención, los requisitos y los criterios de aceptación. Parte de la tarea de ejemplo o de [docs/guides/task.yaml](https://github.com/SebasBarrera/harness-engineering/blob/develop/docs/guides/task.yaml); el formato completo está en la [referencia del archivo de tarea](../reference/task-file.md).

   ```bash
   harness task create --file task.yaml
   ```

3. Ejecútala.

   ```bash
   harness run start --task task_discount_rule
   ```

   Con el proveedor `simulated` la implementación sale de los parches que declara la tarea (`implementation.mode: patch`). Con un agente real, la tarea no lleva parches: consulta [Agentes externos](../guides/external-agents.md).

4. Lee el código de salida.

   - `0`: la ejecución llegó a `CLOSURE`.
   - `4`: no es un error. Las fases automáticas terminaron y la ejecución espera tu decisión en `DECISION`.
   - `6`: algo la detuvo (una validación falló, una política bloqueó). Mira [Seguimiento](seguimiento.md).

5. Revisa y decide. `harness review` muestra el resumen de decisión: qué se pidió, qué cambió, los riesgos, qué se verificó y qué no, y el comando exacto para decidir.

   ```bash
   harness review --run latest
   harness gate decide --run <run-id> --decision APPROVE \
     --change-set-digest sha256:<digest que muestra review> \
     --rationale "Revisé el diff y las pruebas"
   ```

   Si omites `--change-set-digest` en una terminal, el comando te pide confirmar el digest de forma interactiva. Tu identificador de decisor sale de tu usuario de Git (`governance.deciderIdentity: git`); `--actor` lo reemplaza. No se aceptan identificadores de agentes, validadores ni del harness (`agent.*`, `validator.*`, `harness.*`).

6. Al aprobar, la ejecución se cierra. Con la configuración que escribe `harness init`, el cierre crea la rama `harness/<run-id>` con un commit del cambio aprobado y deja esos archivos en el índice (staged) de tu árbol. Tu rama actual no recibe ningún commit.

## Qué sigue

- Cómo ver el estado, los bloqueos y los reportes: [Seguimiento](seguimiento.md).
- Qué archivos crea el harness y cuáles conviene versionar: [Estructura generada](estructura-generada.md).
- Empezar un proyecto desde cero: [Proyecto nuevo](proyecto-nuevo.md). Usarlo en uno que ya existe: [Proyecto existente](proyecto-existente.md).
- Dos personas en la misma funcionalidad: [Trabajo simultáneo](trabajo-simultaneo.md).
- Métricas: [Reporte de métricas](reporte-de-metricas.md). Límites y contrato de los agentes: [Contrato de agentes](contrato-de-agentes.md).
