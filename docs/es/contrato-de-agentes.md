# Contrato entre agentes

Cuando el harness llama a un agente (aclarar la tarea, planificar, escribir pruebas de aceptación,
ubicar el cambio, proponer arquitectura, revisar o implementar) lo hace bajo un contrato JSON
versionado. El contrato dice qué recibe el agente, qué puede hacer, cuánto puede gastar y qué
puede devolver. Esta página explica el contrato, cómo se valida, qué contexto recibe cada llamada
y qué le llega al agente padre.

## Cuándo se activa

El contrato se activa con la sección `agentContract` de `.harness/project.yaml`. `harness init` la
escribe:

```yaml
agentContract:
  mode: enforce
  maxIterations: 5
```

Un `project.yaml` sin la sección se comporta, y calcula su huella de configuración, exactamente
como antes: las solicitudes no cambian y no se cuentan iteraciones. Las demás claves
(`budgets`, `limits`) son opcionales y están descritas en la
[referencia de configuración](../reference/configuration.md#agent-contract).

## Contrato de solicitud

El padre (el harness) envía el contrato en la clave `agentContract` de cada solicitud. Su esquema
está en `schemas/v1/agent-call-request.schema.json` y se imprime con
`harness agent-contract schema request`. Todos los campos son obligatorios salvo los marcados.

| Campo | Para qué sirve |
|---|---|
| `contractVersion` | Versión del contrato (`1.0`). |
| `callKind` | Tipo de llamada: `implement`, `clarify`, `plan`, `acceptance`, `locate`, `architecture` o `review`. |
| `task` | La tarea asignada: `taskId`, `callKind`, `objective` (máximo 500 caracteres) y `fields`, los campos de la tarea que sí se enviaron. |
| `allowedContext` | El contexto permitido: todas las claves de primer nivel de la solicitud. Lo que no está aquí no se envió. |
| `resources` | Archivos y recursos autorizados: `readPaths`, `writePaths`, `guidancePaths` (opcional, los archivos de contexto), `denyPaths` (opcional), `commands` (opcional) y `scripts` (opcional, ids del registro de scripts). |
| `constraints` | Restricciones de ejecución: `readOnly`, `workspace`, `scriptsAreBlackBoxes` y `contextFilesAreGuidance`. |
| `tokenBudget` | Presupuesto de tokens de la llamada. |
| `timeLimitSeconds` | Límite de tiempo de la llamada, en segundos. |
| `sizeClass` | Tamaño de la tarea (`S`, `M` o `L`) del que salen los dos límites anteriores. |
| `iteration` | Número de iteración (`number`) y tope del agente (`max`, entre 1 y 6). |
| `expectedResponse` | La respuesta esperada: protocolo `1.0`, las claves que puede tener `result` (`resultKeys`), los límites por campo (`limits`) y si se permite devolver contenido de archivos (`fileContentsAllowed`). |
| `returnToParent` | El resultado mínimo que vuelve al padre (`fields`) y lo que nunca vuelve (`never`). |

Los archivos de contexto son una guía: `guidancePaths` los nombra, pero el harness no bloquea que
el agente lea otros archivos del proyecto. Lo que sí está confinado son las escrituras, como en el
sandbox del agente.

## Contrato de respuesta

Lo que el padre recibe de una llamada es una respuesta con este contrato
(`schemas/v1/agent-call-response.schema.json`, `harness agent-contract schema response`).

| Campo | Para qué sirve |
|---|---|
| `contractVersion`, `callKind`, `iteration` | Versión, tipo de llamada y número de iteración (obligatorios). |
| `finalStatus` | Estado final de la ejecución (obligatorio): `PASSED`, `FAILED`, `BLOCKED`, `ERROR`, `CANCELLED`, `LIMIT_REACHED` (se alcanzó un límite), `CONTRACT_VIOLATION` (la respuesta rompió el contrato) o `ITERATIONS_EXHAUSTED`. |
| `summary` | Resumen acotado (obligatorio). |
| `status` | Estado que reportó el agente (`PASSED`, `FAILED` o `BLOCKED`), si reportó alguno. |
| `result` | El resultado mínimo. Solo existe si la llamada terminó en `PASSED`. |
| `usage` | Tokens que reportó el proveedor, si los reportó. |
| `limitReached` | `tokens` o `time`. Obligatorio cuando `finalStatus` es `LIMIT_REACHED`. |
| `violations` | Lista de violaciones. Obligatoria cuando `finalStatus` es `CONTRACT_VIOLATION`. |
| `invocationId` | Identificador de la invocación, para buscarla en la evidencia. |

Lo que el agente imprime sigue siendo el objeto JSON del protocolo 1.0
(`{"status": "PASSED", "summary": "...", "result": {...}}`, con `usage` opcional). El harness lo
lee, lo valida contra los límites de `expectedResponse` y entrega al padre la respuesta de la
tabla anterior.

## Cómo se valida

- Antes de enviar, el harness construye el contrato con los mismos modelos de los que salen los
  esquemas JSON. Un contrato inválido (por ejemplo una iteración por encima del tope) no se envía.
- Después de la llamada revisa la respuesta contra los límites: claves de `result` que el tipo de
  llamada no define, tamaño del `result` en bytes, tamaño de cada campo de texto, y contenido de
  archivos donde solo se pide una conclusión (solo `acceptance` devuelve archivos, las pruebas que
  escribe).
- Un documento cualquiera se valida a mano con `harness agent-contract validate`:

```bash
harness agent-contract validate request solicitud.json
harness agent-contract validate response respuesta.json
harness agent-contract validate output salida-del-agente.json --kind plan
```

Sale con `0` si cumple el contrato y con `6` si no, imprimiendo la violación. `output` es el JSON
que imprime el agente, comparado con los límites del tipo de llamada indicado en `--kind`.

## Ejemplos válidos

Una solicitud de `plan` que cumple el contrato (la salida de `validate` fue `"valid": true`, salida
`0`):

```json
{
  "contractVersion": "1.0",
  "callKind": "plan",
  "task": {
    "taskId": "task_1",
    "callKind": "plan",
    "objective": "Descuento. Aplicarlo desde el umbral.",
    "fields": ["task_id", "title", "intent", "requirements", "acceptance_criteria"]
  },
  "allowedContext": ["agentContract", "instructions", "kind", "readOnly", "schemaVersion", "task", "workspace"],
  "resources": {"readPaths": ["/proyecto"], "writePaths": [], "denyPaths": [".git", ".harness"]},
  "constraints": {"readOnly": true, "workspace": "/proyecto"},
  "tokenBudget": 50000,
  "timeLimitSeconds": 600,
  "sizeClass": "S",
  "iteration": {"number": 1, "max": 5},
  "expectedResponse": {
    "resultKeys": ["subtasks"],
    "limits": {"summaryChars": 600, "resultBytes": 32768, "fieldChars": 4000},
    "fileContentsAllowed": false
  },
  "returnToParent": {}
}
```

Una respuesta válida de esa llamada:

```json
{
  "contractVersion": "1.0",
  "callKind": "plan",
  "iteration": 1,
  "status": "PASSED",
  "finalStatus": "PASSED",
  "summary": "Dos subtareas.",
  "result": {"subtasks": []}
}
```

Una respuesta válida de una llamada que alcanzó un límite:

```json
{
  "contractVersion": "1.0",
  "callKind": "acceptance",
  "iteration": 1,
  "finalStatus": "LIMIT_REACHED",
  "limitReached": "tokens",
  "summary": "agent.limit.reached: the call used 5010 tokens of a budget of 1000"
}
```

## Ejemplos inválidos

| Documento | Por qué es inválido | El mensaje contiene |
|---|---|---|
| La solicitud anterior con `"max": 7` | El tope de iteraciones no puede superar 6. | `iteration.max: Input should be less than or equal to 6` (salida `6`) |
| La solicitud sin `tokenBudget` | Falta un campo obligatorio. | `tokenBudget: Field required` |
| Una solicitud con `iteration` `{"number": 6, "max": 5}` | La iteración está por encima del tope. | `iteration 6 is above the cap of 5` |
| Una solicitud de solo lectura con `writePaths` | Una llamada de solo lectura no puede escribir. | `a read-only call cannot be authorized to write` |
| Una respuesta `LIMIT_REACHED` sin `limitReached` | El estado exige nombrar el límite. | `finalStatus LIMIT_REACHED needs limitReached` |
| Una respuesta `BLOCKED` con `result` | Solo una llamada que pasó devuelve resultado. | `only a passed call returns a result` |
| La salida de un agente de `plan` con `"result": {"subtasks": [], "razonamiento": "..."}` | El `result` trae una clave fuera del contrato. | `result carries keys outside the plan contract: razonamiento` (salida `6`) |

## Qué ocurre cuando el contrato no se cumple

- Una respuesta del agente que rompe los límites se descarta: el padre no recibe su `result`. La
  llamada termina con `finalStatus: CONTRACT_VIOLATION`, queda un hallazgo `MEDIUM`
  `agent.contract.violation` con las violaciones y la salida completa del proveedor se conserva
  como evidencia.
- Si el proyecto tiene `runtime.contractRetry`, la llamada se envía una vez más (otra iteración,
  que cuenta contra el tope). Sin esa clave, la primera respuesta rota bloquea la fase, como antes.
- El resumen demasiado largo no es una violación: se corta al límite `limits.summaryChars` y el
  registro de la llamada indica `summaryTruncated`.
- Un contrato de solicitud inválido falla cerrado: una llamada de solo lectura se bloquea con
  `agent.contract.invalid` y no se envía.

## Contexto mínimo para cada llamada

**Cómo se selecciona.** Cada tipo de llamada recibe solo los campos de la tarea que necesita:

| Tipo | Campos de la tarea que recibe |
|---|---|
| `clarify` | `task_id`, `title`, `intent`, `constraints`, `requirements`, `acceptance_criteria`, `contract` |
| `plan` | `task_id`, `title`, `intent`, `constraints`, `requirements`, `acceptance_criteria` |
| `acceptance` | `task_id`, `title`, `intent`, `requirements`, `acceptance_criteria` |
| `locate` | `task_id`, `title`, `intent`, `requirements`, `acceptance_criteria` |
| `architecture` | `task_id`, `title`, `intent`, `constraints`, `requirements` |
| `review` | `task_id`, `title`, `constraints`, `requirements`, `acceptance_criteria` |
| `implement` | La tarea completa y el plan: es la tarea que el agente implementa. |

No se envían a las llamadas de solo lectura los campos internos de la tarea (`metadata`,
`implementation`, `probes`, `checklist`, `created_at`, `project_id`). Lo demás que lleva la
solicitud (instrucciones, directorio de trabajo y los datos propios de cada tipo) ya existía antes
del contrato; el contrato no añade historial.

**Cómo se limita.** Tres mecanismos, todos ya existentes o descritos aquí: los campos de la tabla,
el manifiesto de contexto de `implement` (`context.maxFiles` y `context.maxBytes`, que limita los
archivos que se listan) y las lecciones filtradas.

**Cómo se demuestra que no recibió más.** El contrato de cada solicitud lista en `allowedContext`
todas las claves que la solicitud lleva y en `task.fields` los campos de la tarea. El harness
guarda la solicitud completa como evidencia y su huella (`digest`) coincide con la huella del
prompt de la invocación. El comando que lo muestra:

```bash
harness agent-contract calls --run latest --kind acceptance --iteration 1
```

Sin `--kind`, el comando lista las llamadas de la ejecución y las iteraciones usadas por cada
agente. Con `--kind`, muestra para esa llamada la solicitud exacta que se envió (`request`), lo que
recibió el padre (`parentReceived`), el registro (`record`) y una `verification`:

- `digestMatches`: la solicitud guardada tiene la huella registrada.
- `keysMatchContract`: las claves de la solicitud guardada son exactamente `allowedContext`.
- `extraKeys`: claves enviadas que el contrato no listaba. Debe estar vacío.

**Qué evidencia permite verificarlo.** El artefacto `agent-<tipo>-request` (la solicitud), el
artefacto `agent-call-record` (lo enviado y lo recibido, con tamaños en bytes) y el evento
`agent.call.recorded` de la cadena de eventos de la ejecución.

Límite que conviene conocer: la solicitud de `implement` la termina de armar el adaptador del
proveedor, que puede añadir `context` (memoria gobernada), `feedback` (correcciones) y
`selfReport`. Para `implement` el harness guarda el contrato, la lista de claves que añade y la
huella del prompt de la invocación, pero no la solicitud completa, así que `verification` indica
`requestStored: false`.

## Respuesta mínima al agente padre

**Cómo se limita la respuesta.** Con los límites de `expectedResponse.limits`, configurables en
`agentContract.limits`:

- `summaryChars` (600 por defecto): el resumen se corta.
- `resultBytes`: tamaño máximo del `result` serializado, por tipo de llamada (4096 `implement`,
  32768 `clarify`, `plan` y `locate`, 65536 `review` y `architecture`, 262144 `acceptance`).
- `fieldChars` (4000): tamaño máximo de cada campo de texto dentro del `result`.
- Solo las claves definidas para el tipo de llamada (`resultKeys`).
- Sin contenido de archivos (`content`, `contents`, `source`, `body`...) en ningún tipo salvo
  `acceptance`, que devuelve las pruebas que escribió.

**Qué información puede regresar.** Estado final, resumen, el `result` del tipo de llamada, los
tokens que reportó el proveedor y, si aplica, el límite alcanzado o las violaciones. No regresan el
contexto recibido, razonamientos internos ni información que el padre no necesite para decidir lo
siguiente.

**Cómo se demuestra lo que recibió el padre.** `parentReceived` del comando anterior es la
respuesta que se entregó, y `receivedBytes` del registro es su tamaño. La salida completa del
proveedor se guarda aparte (`raw.outputRef`) solo para diagnóstico: el padre no la recibe. Los
límites revisan el `result` y el resumen; no pueden demostrar que un agente no "razonó de más"
dentro de un texto permitido, solo acotan cuánto y en qué claves puede devolver.
