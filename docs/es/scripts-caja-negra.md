# Scripts como cajas negras

Los scripts y comandos que el harness deja usar a un agente son cajas negras: el agente solo
necesita saber cómo invocarlos, qué entradas reciben, qué salidas producen y qué errores pueden
devolver. No carga su código en el contexto ni gasta tokens interpretando cómo funcionan por
dentro: los invoca, espera el resultado y usa solo la respuesta.

## Cómo funciona

1. Cada script tiene un **contrato** con cuatro partes: invocación, entradas, salidas y errores.
   El esquema es `schemas/v1/script-contract.schema.json` (`harness agent-contract schema script`).
2. Los contratos viven en un **registro** que se arma con lo que el proyecto realmente permite:
   - `harness check`, el comando que el agente puede correr antes de terminar;
   - los comandos de los validadores del gate (`validator.<id>`), uno por validador con comando;
   - las herramientas del servidor MCP del modo embebido (`mcp.<herramienta>`), derivadas de su
     esquema de entrada.
3. La solicitud de `implement` lleva en `scriptContracts` el resumen de los que el agente puede
   ejecutar (`harness.check`, o los validadores permitidos si sus permisos no incluyen
   `harness check`) y en `agentContract.resources.scripts` sus ids.
4. Las instrucciones de `implement` añaden: "Scripts and commands are black boxes: use only the
   contract listed in scriptContracts (invocation, inputs, outputs, errors). Do not read
   their source code, do not work out how they work inside, run them and use only the answer they
   give." Solo `implement` ejecuta comandos, por eso solo ahí se añade; las llamadas de solo
   lectura no ejecutan scripts.

Esto es una instrucción y un contrato, no un bloqueo: el harness no impide leer archivos (los
archivos de contexto son una guía). Lo que sí garantiza es que el agente tiene, sin leer código,
todo lo que necesita para usar cada script.

## Consultar los contratos

```bash
harness agent-contract scripts                  # lista de scripts del proyecto
harness agent-contract scripts harness.check    # contrato completo de uno
```

## Contrato de `harness check`

| Parte | Contenido |
|---|---|
| Invocación | `harness check --path <workspace> --run <run id>` |
| Entradas | `--path` (directorio del proyecto, opcional) y `--run` (ejecución cuya tarea y línea base usan las revisiones del diff, opcional). |
| Salidas | Por la salida estándar, un JSON con `status`, `validators[]` (id, mandatory, status, summary, problems), `checks[]` (id, policy, issues) y `blockingIssues`. El código de salida es `0` si pasa. No registra nada. |
| Errores | `6`: un validador obligatorio falló o una revisión obligatoria encontró un problema bloqueante; el agente corrige lo que listan `validators[].problems` y `checks[].issues` y lo vuelve a correr. `2`: no hay configuración válida; el agente se detiene e informa. `3`: la ejecución de `--run` no tiene estado de revisión; el agente lo corre sin `--run`. |

## Contrato de un validador

El comando del validador tal como está en la configuración (por ejemplo `ruff check .`). Entrada:
el directorio del proyecto, sin otras entradas. Salidas: el código de salida (`0` si pasa) y lo que
la herramienta imprime, de lo cual el harness extrae los problemas con ubicación. Error: un código
distinto de cero significa que hay un problema o que no pudo correr; el agente lee los problemas
y corrige la causa, sin editar el validador ni debilitar su configuración.

## Contrato de una herramienta MCP

Invocación: el nombre de la herramienta (`harness_check`, `harness_status`...). Entradas: las
propiedades de su esquema de entrada, con las obligatorias marcadas. Salida: el contenido de texto
del resultado de la herramienta. Error: el resultado con `isError: true` y el motivo en texto; el
agente cambia los argumentos y no repite la llamada igual.

## Qué hace falta para añadir un script nuevo

El registro (`src/governed_harness/agents/script_contracts.py`) es la única lista. Un script nuevo
que los agentes puedan llamar se añade ahí con su contrato; el esquema impide un contrato sin
salidas o sin errores. Un script sin contrato no aparece en la solicitud y por tanto no se ofrece a
los agentes.
