# Seguimiento de una ejecución

Cómo saber en qué punto está una ejecución, qué la detuvo y dónde mirar los resultados. Todos los comandos son de solo lectura (no cambian la ejecución), salvo los que se indican, y ninguno llama a un modelo. Se probaron con el harness 1.0.0.

Sobre la salida: en una terminal el harness imprime texto legible; en un pipe o en CI imprime JSON. `harness --no-json <comando>` fuerza texto y `--json` fuerza JSON. En esta página los ejemplos están en texto.

## Lo primero: el código de salida

`run start`, `run continue` y `gate decide` devuelven un código que dice qué pasó, sin abrir nada más.

| Código | Qué significa | Qué hacer |
|---:|---|---|
| `0` | La ejecución llegó a `CLOSURE` (o el comando terminó bien). | Nada. |
| `4` | Las fases automáticas terminaron y espera una decisión humana. No es un error. | `harness review`, luego `harness gate decide`. |
| `6` | Algo la detuvo: una validación falló, una política bloqueó o una verificación de registros falló. | `harness status` y `harness findings list`. |
| `5` | Una política lo rechazó: aprobación obsoleta, ejecución que no está en `DECISION`, espacio de trabajo en uso por otro proceso, tarea con ejecución abierta. | Lee el mensaje; dice qué corregir. |
| `3` | No existe lo que pediste (ejecución, tarea...). | Revisa el identificador con `harness run list`. |
| `2` | Error de configuración o de entorno. | `harness doctor`, `harness config validate`. |
| `1` | Error del harness o inesperado. | Revisa el mensaje y reporta si persiste. |
| `130` | La ejecución quedó cancelada (`harness run cancel`). | Crea una ejecución nueva si aún necesitas el cambio. |
| `143` | El comando recibió `SIGTERM`; la fase quedó registrada como interrumpida. | `harness run continue` la recupera. |

La tabla completa está en la [referencia de códigos de salida](../reference/exit-codes.md).

## ¿Dónde está mi ejecución?

```bash
harness run list
```

Lista las ejecuciones del proyecto, la más reciente primero, con su tarea, fase, estado y última actualización:

```text
RUN                                   TASK                PHASE         STATUS     UPDATED
run_ed8a7268e6f94663aa31c3594a8444a9  task_neg            CLOSURE       PASSED     2026-10-09T00:40:26
run_a7cd5c4913884332815dc67373303e34  task_two            VERIFICATION  CANCELLED  2026-10-09T00:40:03
run_dede5f63c93748fbad7d9eeaf60437d6  task_lease          DECISION      BLOCKED    2026-10-09T00:41:36
```

Fíjate en que `BLOCKED` en la fase `DECISION` no es un problema: significa que la ejecución terminó todo lo automático y te espera. `BLOCKED` en cualquier otra fase sí es un bloqueo (por ejemplo, una capacidad denegada o un conflicto de rama en el aislamiento).

`--run` acepta el identificador completo, un prefijo único (`run_1a2b`) o `latest` en `status`, `trace`, `review`, `findings list`, `evidence list` y `budget show`. `harness verify --run` no acepta `latest`: usa el identificador.

## ¿Qué esperan de mí?

```bash
harness inbox
```

Lista, de la más antigua a la más nueva, las ejecuciones del proyecto que esperan a una persona: una decisión en `DECISION` (con el estado de la compuerta, el digest y los hallazgos bloqueantes), respuestas a preguntas de aclaración en `INTENT` y las demás esperas anteriores a `DECISION` (plan, descomposición, pruebas de aceptación, arquitectura...), cada una con el siguiente comando.

```text
WAITS FOR  RUN                       TASK                  STATE                                WAITING  NEXT
decision   run_dede5f63c93748fb...   Apply a percentage... gate PASSED, 0 blocking finding(s)   0.0 h    harness review --run run_dede5f63...
```

## ¿Cómo va y qué pasó?

```bash
harness status --run latest
```

Muestra la fase actual, el estado, el digest del ChangeSet, la compuerta, el número de validaciones y hallazgos por severidad, si la cadena de eventos es válida y una tabla con cada fase, su intento, su estado y un resumen. Es la vista para saber hasta dónde llegó y en qué fase se detuvo.

```text
Run run_826700ae99524300b39c7824f310bc2f: FAILED in VERIFICATION.
gate: not evaluated yet
validations: 1 (FAILED 1)
findings: 3 (LOW 1, HIGH 2)
event chain valid: yes

  PHASE           ATTEMPT  STATUS  SUMMARY
  INTENT          1        PASSED  Intent is structured and identifiable
  ...
  VERIFICATION    1        FAILED  The affected tests did not pass; the full suite runs on the…
```

Para monitorear una ejecución larga, repite `harness status --run latest` (por ejemplo con `watch -n 10`), o abre el tablero web (más abajo).

## ¿Por qué se bloqueó o falló?

1. `harness findings list --run <run-id>`: los hallazgos, con severidad, regla, lugar y mensaje. Es la respuesta más directa.

   ```text
   SEVERITY  RULE                                LOCATION                 MESSAGE
   LOW       environment.dirty-tree              -                        The run starts on 2 uncommitted change(s): src/pricing/__in…
   HIGH      harness.affected-tests.failed       -                        harness.affected-tests failed with exit code 1
   HIGH      harness.affected-tests.test-failed  tests/test_pricing.py:9  tests/test_pricing.py::test_req_discount_at_threshold faile…
   ```

2. `harness review --run <run-id>`: el resumen de decisión. Dice qué se pidió, qué cambió, los riesgos con archivo y línea, qué se verificó y sobre qué digest, qué no se verificó, los reintentos y correcciones, y cuál es el siguiente comando.
3. `harness evidence list --run <run-id>`: la evidencia guardada (salida de cada herramienta, capacidades concedidas por fase, resultados de validadores), cada pieza con su digest, su fase y quién la produjo.
4. `harness trace --run <run-id>`: la traza completa en Markdown (por defecto), JSON, JSONL, SARIF o GitLab Code Quality. Con `--output archivo.md` la escribe a un archivo. Incluye fases, resultados de validación, hallazgos, la compuerta y la decisión.

Cuando el problema está resuelto: `harness run continue --run <run-id>` retoma la ejecución, recalcula el ChangeSet y repite las validaciones necesarias. `harness run cancel --run <run-id>` la cancela.

## Límites alcanzados

Cada ejecución tiene un presupuesto (`budget` en `project.yaml`: por llamada, por tarea y por ejecución, en costo, tokens y tiempo de reloj). Para ver el uso y lo que queda:

```bash
harness budget show --run <run-id>
```

Muestra el uso de la ejecución y de su tarea, los límites, lo que una persona haya subido (`raised`), lo que queda (`remaining`) y, si hay, los límites superados (`exceeded`). Con la configuración de `init` los límites son amplios: 25 USD por llamada, 100 USD y 500 millones de tokens por ejecución, y un aviso al 80 %.

Qué ocurre al llegar a un límite, según el código (el proveedor `simulated` informa cero tokens y tiempos de milisegundos, así que no pude provocarlo en la prueba):

- Al cruzar el aviso queda un hallazgo `budget.warning` de severidad `LOW` y un evento `budget.warning`.
- Al superar el límite queda un hallazgo `budget.exceeded` de severidad `HIGH` y un evento `budget.exceeded`, y se bloquea la siguiente llamada.
- Una persona sube el límite con `harness budget raise --run <run-id> --scope run --metric costUsd --to <valor> --rationale "motivo"` (queda registrado con su identificador y el motivo) y retoma con `harness run continue`.

Otros límites que puedes encontrar: el tiempo máximo de cada comando (`runtime.commandTimeoutSeconds`), que deja el validador en `TIMED_OUT`; el límite de correcciones de verificación (`runtime.verificationCorrections`) y los reintentos y escalamientos de agente. El tope único de iteraciones por agente y la forma de reconocer que se alcanzó están en [Contrato de agentes y límites](contrato-de-agentes.md).

## ¿Se puede confiar en el registro?

```bash
harness verify --run <run-id>
```

Comprueba la cadena de eventos, su cabeza contra el ancla fuera de `.harness`, cada registro contra su evento y cada artefacto contra su digest. Sin `--run` verifica todas las ejecuciones. Código 0 si todo coincide, 6 si algo falla. Nunca repara nada. `harness status` ya muestra si la cadena es válida.

## Tablero web local

```bash
pip install "governed-agent-harness[api]"
export HARNESS_API_TOKEN=<un texto de al menos 16 caracteres>
harness api serve
```

Sirve en `http://127.0.0.1:8765` el tablero con las ejecuciones, los resúmenes de decisión, las trazas, la evidencia, los hallazgos y la bandeja. Con la sección `api` que escribe `init`, cada petición exige `Authorization: Bearer <token>`; sin token la respuesta es 401, y con el token es 200 (probado). Si no defines `HARNESS_API_TOKEN`, el comando genera uno y lo imprime una sola vez en la salida de error. Es una interfaz local: no la expongas en una red. Más en la [referencia de la API](../reference/api.md).

## Reportes, métricas y resultados

- `harness metrics` escribe `metrics.json`, `metrics.md` y `metrics.html` en `.harness/metrics/` (tokens, costo, líneas, tiempos, esperas humanas, calidad y fricción), sin llamar a ningún modelo. `--open` abre el HTML; `--since 7d`, `--task` y `--model` filtran; `--all-repos` suma todos los repositorios del registro. Cómo leerlo: [Reporte de métricas](reporte-de-metricas.md).
- `harness trace --run <run-id> --output traza.md` guarda la traza de una ejecución.
- `harness export --run <run-id> --bundle evidencia.tar.gz` produce un paquete de evidencia portátil que `harness verify --bundle evidencia.tar.gz` comprueba sin el espacio de trabajo.
- `harness retrospect --run <run-id>` deriva observaciones y recomendaciones de una ejecución cerrada. No aplica nada automáticamente.
- `harness registry` lista los proyectos cuyo registro vive en el directorio de estado, con sus últimas ejecuciones.
- El cambio aprobado queda en la rama `harness/<run-id>`; el resultado final es ese commit más el registro de la ejecución. Dónde están los archivos: [Estructura generada](estructura-generada.md).
