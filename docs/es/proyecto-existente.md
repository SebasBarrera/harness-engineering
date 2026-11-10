# Proyecto existente

El flujo es el mismo que en [Proyecto nuevo](proyecto-nuevo.md). Lo que cambia es el punto de partida: historia, convenciones, dependencias que no instalaste tú y, a veces, un estado base que ya está roto. La guía en inglés [Brownfield](../guides/brownfield.md) reproduce un caso real sobre `pallets/itsdangerous` 2.2.0.

## Pasos

1. Entra al repositorio y comprueba el estado del árbol.

   ```bash
   git status --short
   ```

   Lo mejor es un árbol limpio. Si hay cambios sin confirmar, forman parte del estado base y quedan fuera del ChangeSet, pero la ejecución registra un hallazgo `environment.dirty-tree` de severidad `LOW` que dice cuántos cambios había. (Con `environment.dirtyTree: block` en la configuración, la ejecución se detiene.)

2. Inicializa el harness.

   ```bash
   harness init
   ```

   En un repositorio de Git, `init` agrega `.harness/` a `.git/info/exclude`, un archivo que Git lee como un `.gitignore` pero que no forma parte del árbol. Tu árbol no queda sucio por el harness y no hay nada que confirmar por esto. Si prefieres que la entrada vaya en el `.gitignore` del proyecto, usa `harness init --ignore-file gitignore` (ese cambio sí hay que confirmarlo). Si `.harness/project.yaml` ya existe, `init` se niega a sobrescribirlo salvo con `--force`.

3. Revisa lo que detectó, sin riesgo: son comandos de solo lectura.

   ```bash
   harness inspect
   harness doctor --path .
   harness config validate
   harness project show
   ```

   `doctor` comprueba Python, Git, la configuración, los validadores que el proyecto ejecutará, el commit base y si `.harness/` está ignorado por Git. Cada punto que no pasa trae su remedio.

4. Instala las dependencias de prueba del proyecto en el entorno donde corre el harness. Los validadores ejecutan los comandos del proyecto (`python -m pytest`, `npm test`) con el intérprete o las herramientas del `PATH`. Con `toolchain.interpreter: auto` (lo que escribe `init`), los validadores de Python usan el `.venv` o `venv` del proyecto, `uv run --no-sync` o `poetry run`. Una dependencia que falta no se detecta de antemano: las pruebas fallan al recolectarse y la ejecución sale con código 6. Los proyectos con comandos distintos declaran sus propios validadores en `toolchain.validators` (ver la [referencia de configuración](../reference/configuration.md#project-toolchain)).

5. Crea la tarea y ejecútala, igual que en un proyecto nuevo. Para un cambio en código existente declara en `metadata.ownedPaths` los directorios que el agente puede modificar: el ChangeSet solo contiene esas rutas.

   ```bash
   harness task create --file task.yaml
   harness run start --task <taskId>
   ```

## Qué hace el harness con el código que ya existe

- Con `architecture.mode: agent` (lo que escribe `init`), la fase de descubrimiento hace una consulta de solo lectura al agente para estudiar la arquitectura del proyecto y la guarda en `.harness/architecture.md` y `.harness/architecture.json`. Las siguientes ejecuciones la reutilizan sin otra consulta. Con el proveedor `simulated` el archivo solo dice «Simulated survey: no layering was inferred»; con un agente real describe los patrones y las reglas de capas. Ver [Ingeniería inversa](ingenieria-inversa.md) y [Arquitecturas](arquitecturas.md).
- Los archivos que `.gitignore` excluye nunca se leen ni se guardan. Con `workspace.snapshot: git`, `baseline: manifest` y `snapshotCache: true` (lo que escribe `init`), repositorios de miles de archivos no se vuelven lentos en cada ejecución.
- La detección respeta tus convenciones: lee los archivos marcadores (`pyproject.toml`, `package.json`, ...) sin modificarlos. Si usas un gestor de paquetes distinto de npm en un proyecto Node.js, trata esos resultados con cuidado, porque el perfil siempre ejecuta `npm`.

## Si el estado base ya está roto

El harness todavía no distingue fallos que ya existían de los que introduce el cambio (el estado `PREEXISTING_ERROR` existe pero no se asigna, issue 7 del repositorio). La evidencia muestra qué archivos fallaron para que veas que están fuera del ChangeSet. Hay dos salidas:

- Corrige el entorno o el estado base y ejecuta `harness run continue --run <run-id>`. El gate cuenta el último intento de cada validador, así que puede quedar en `PASSED` y bastar un `APPROVE` normal.
- Cierra con `APPROVE_EXCEPTION` y una justificación escrita; queda auditada aparte.

## Trabajar sin tocar tu árbol

Si quieres seguir editando mientras el harness trabaja, o ejecutar varias tareas a la vez, usa el aislamiento por worktree: `harness run start --task <taskId> --isolate worktree`. Está explicado, con sus límites, en [Trabajo simultáneo](trabajo-simultaneo.md).
