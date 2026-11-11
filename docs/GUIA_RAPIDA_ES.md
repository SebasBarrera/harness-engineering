# Guía rápida en español

## Qué hace

El harness gobierna una tarea de desarrollo desde la intención hasta el cierre. No reemplaza al agente, las pruebas, Git ni los analizadores: controla cuándo pueden actuar, qué evidencia deben producir y bajo qué condiciones puede avanzar la ejecución.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,api]'
```

## Preparar un repositorio

```bash
harness init --path ./mi-proyecto
harness inspect --path ./mi-proyecto
harness config validate --path ./mi-proyecto
```

El detector inicial soporta proyectos Python y Node.js. La configuración queda en `.harness/project.yaml`.

## Definir una tarea

Una tarea mínima:

```yaml
title: Corregir precedencia de configuración
intent: Los valores de tarea deben tener mayor prioridad que los del repositorio.
acceptanceCriteria:
  - La tarea tiene la mayor prioridad.
  - Los tests existentes siguen pasando.
implementation:
  mode: patch
  patches:
    - path: src/config.py
      operation: replace
      content: |
        # contenido esperado
```

Registrar la tarea:

```bash
harness task create --path ./mi-proyecto --file task.yaml
```

## Ejecutar

```bash
harness run start --path ./mi-proyecto --task <task-id>
```

Cuando todo lo automático pasa, el comando termina con código `4`: no es un error; significa que la ejecución quedó bloqueada esperando una decisión humana en `DECISION`.

Consultar:

```bash
harness status --path ./mi-proyecto --run <run-id>
harness findings list --path ./mi-proyecto --run <run-id>
harness evidence list --path ./mi-proyecto --run <run-id>
```

## Aprobar el ChangeSet exacto

Copiar el `changeSetDigest` actual y ejecutar:

```bash
harness gate decide \
  --path ./mi-proyecto \
  --run <run-id> \
  --decision APPROVE \
  --change-set-digest sha256:<digest> \
  --actor human.reviewer \
  --rationale 'Revisé criterios, diff, tests y findings'
```

Después de cualquier cambio adicional, la aprobación anterior deja de ser válida automáticamente.

Cuando existen findings bloqueantes, una aprobación ordinaria no es válida. Una excepción debe utilizar `APPROVE_EXCEPTION`, incluir una justificación específica y queda auditada por separado.

## Continuar después de una corrección

Cuando un test o validador falla, corregir los archivos y ejecutar:

```bash
harness run continue --path ./mi-proyecto --run <run-id>
```

El sistema recalcula el ChangeSet y repite las validaciones necesarias.

## Trazas y retrospectiva

```bash
harness trace --path ./mi-proyecto --run <run-id> --format markdown --output trace.md
harness trace --path ./mi-proyecto --run <run-id> --format json --output trace.json
harness retrospect --path ./mi-proyecto --run <run-id>
```

La retrospectiva genera recomendaciones; nunca modifica automáticamente reglas, gates, permisos o memoria normativa.

## Web local

```bash
harness api serve --path ./mi-proyecto --host 127.0.0.1 --port 8765
```

La interfaz permite observar ejecuciones y registrar decisiones vinculadas al digest. Con la sección `api` que escribe `harness init`, cada ruta exige un token bearer: el comando lo toma de `HARNESS_API_TOKEN` o genera uno y lo imprime una sola vez en la salida de error; el tablero lo pide una vez y lo guarda solo en la pestaña. Sin la sección no hay autenticación. En ambos casos es una interfaz local; no debe exponerse públicamente.

## Límites importantes

El process runner local no es un sandbox fuerte. Controla autorización, argumentos, directorio, timeout, cancelación y salida, pero un proceso nativo hostil todavía puede usar los permisos del usuario del sistema operativo. Para código no confiable se requiere un adaptador basado en contenedores, VM o sandbox del sistema operativo.
