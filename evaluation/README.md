# Evaluación controlada

Código, fixtures, pruebas ocultas y lanzadores de la evaluación controlada del Governed Agent Harness
(capítulo 7 de la tesis). La evaluación reporta solo la versión 2.0.0; la campaña está en curso (plan,
catálogo y scripts en [`campaign/`](campaign/)). Los resultados de versiones anteriores no forman parte
de este análisis: están en el historial de git y en el tag `v0.8.0`. El mismo agente (Claude Code,
modo no interactivo) implementa las mismas tareas con y sin el harness, y cada corrida se mide
después de la misma forma.

## Diseño

| Factor | Niveles |
|---|---|
| Escenario | `greenfield` (biblioteca de costos de envío a partir de `SPEC.md`), `brownfield` (decodificación estricta de base64 en `pallets/itsdangerous` 2.2.0), `security` (cliente de alertas cuya especificación menciona una contraseña de desarrollo) |
| Condición | ver [Condiciones](#condiciones-conditionspy) |
| Modelo | `claude-haiku-4-5-20251001`, `claude-sonnet-5-5`, `claude-opus-5-5` |

Todas las condiciones usan el mismo prompt (`agentlib.build_prompt`), el mismo conjunto de
herramientas y modo de permisos, el mismo límite de tiempo y un entorno mínimo. Las condiciones
gobernadas aplican una regla de decisión declarada antes de las corridas (`run_eval.py`): aprobar
cuando el gate pasa, pedir cambios con las razones del gate y los hallazgos como retroalimentación
cuando no pasa (a lo sumo dos ciclos de corrección) y rechazar en otro caso; una corrida que se
detiene antes de `DECISION` no se entrega.

`fault_probes.py` complementa la comparación: un proveedor determinista (`fault_provider.py`)
aplica la solución de referencia de la tarea brownfield más una falla conocida, y la sonda registra
qué control reacciona.

Otros bloques reutilizan los mismos escenarios y medidas: niveles de instrucción (`--prompt poor`,
`--prompt casual`), un segundo agente (`--agent codex`) y desarrollo iterativo (`longitudinal/`).

## Medidas (`measure.py`)

- Pruebas de aceptación ocultas (`hidden/`), que nunca se muestran a quien implementa.
- La suite de pruebas del propio repositorio y, en el escenario brownfield, las pruebas originales
  del proyecto, corridas sobre el código final (regresiones).
- Tamaño y alcance del cambio, cobertura de las líneas de código cambiadas, hallazgos de Ruff y
  Bandit introducidos en los archivos cambiados, y si la contraseña de desarrollo aparece en el
  cambio.
- Uso del agente reportado por el CLI (tokens, costo, turnos, duración) y, en las condiciones
  gobernadas, el historial del gate, las decisiones, los eventos, la validez de la cadena de eventos
  y la completitud de la traza.

## Resultados

`results-2.0.0/<block>/` guarda los registros de la evaluación de la 2.0.0; las suites deterministas
están en `results-2.0.0/deterministic/`. No se registran rutas locales ni identificadores de corrida.
En el árbol no se guarda ningún resultado de una versión anterior.

## La evaluación de la 2.0.0

Todo se mide sobre el harness bajo prueba. Los resultados van a `results-2.0.0/<block>/` (registros JSONL, resúmenes,
`environment.json`); los directorios crudos de las corridas quedan en el directorio de trabajo del
lanzador, fuera del repositorio, y los registros no llevan ninguna ruta local ni id de corrida.

### Condiciones (`conditions.py`)

| Condición | Configuración (`.harness/project.yaml`, guardada en cada directorio de corrida y en el registro como `projectConfig`) |
|---|---|
| `direct` | Ninguna: Claude Code solo (`baseline` en registros anteriores). |
| `harness-core` | El archivo que escribía `harness init` en la 1.0.0, clave por clave: ninguna de las claves agregadas después de la 1.0.0 (`coreCheck.extraKeys` está vacío). Con `--core-reference-src <1.0.0 src>` la corrida comprueba que el resolvedor de la 1.0.0 calcula para él el mismo digest de configuración (`coreCheck.digestEqual`). |
| `harness` | Lo que escribe `harness init` de la versión bajo prueba (todos los ajustes de la 2.0.0 activos), con `agentRouting.mode: fixed`: cada llamada usa el modelo de la celda. |
| `harness-anchored` | Lo que escribe `harness init`, con el enrutamiento tal como lo escribe init desde la ola 9 (`anchored`, #85) y `agentRouting.anchorModel` fijado en el modelo de la celda: el enrutador elige el modelo y el esfuerzo de cada llamada a partir de las tablas por defecto, con el modelo que invoca como techo (`agent.routing.decided` con su `anchor`). El factor es el modelo que invoca: Haiku, Sonnet u Opus. Reemplaza a `harness-tiered` del primer piloto, en el que las tablas por defecto ignoraban el modelo que invoca. |
| `embedded` | `run_embedded.py`: Claude Code, como sesión anfitriona, conduce el harness a través de `harness mcp serve` y la skill (`harness init --agent-skills`), con la configuración `harness`; el factor es el modelo anfitrión. |

La evaluación cambia solo el proveedor del agente (el adaptador descrito abajo, o el proveedor
`simulated` del harness en una corrida en seco), el permiso de exactamente ese comando de proveedor,
`runtime.commandTimeoutSeconds` (1800 s) y, donde el sandbox de escritura está activo, una entrada
extra de `runtime.sandboxWritePaths`: el directorio `agent-calls` de la corrida, donde el adaptador
escribe sus registros de uso. Desde la ola 9 (#87) un comando de proveedor debe estar permitido por
un permiso `process.execute` bajo `governance.phaseCapabilities`; la evaluación otorga la línea de
comandos completa del adaptador (alcance `python <code>/claude_provider.py --model
<model> ...`, nunca `python` solo) en `capabilities.extend` y, en `harness-core`, que no tiene la
clave `extend`, en `capabilities.grants` (la clave de la 1.0.0, agregada allí a los perfiles). Cada
registro gobernado guarda `grantCheck` (las reglas escritas y las advertencias de proveedor de
`harness config validate`, vacío cuando el permiso se sostiene). El registro de cada corrida
(`runtime.stateDir: auto`) se guarda en el directorio de la corrida (`HARNESS_STATE_DIR`).

Desde la enmienda del 2026-11-11 al pre-registro (autoverificación del agente y caché de prompts),
las llamadas implement de todas las condiciones permiten, además de pytest, `ls` y `cd`, los
validadores del proyecto (`python -m ruff check`, `python -m ruff format --check`, `python -m mypy`,
sus formas `python3` y sin `python -m`) y `harness check` (`agentlib.ALLOWED_TOOLS`); ningún otro
comando de shell. El prompt base (`agentlib.build_prompt`) dice que el agente ya está en el
repositorio (sin `cd` ni rutas absolutas) y que verifique su cambio con
`agentlib.VALIDATOR_COMMANDS`; en las condiciones con contrato del gate (`harness`,
`harness-anchored`) la evaluación otorga `harness check` (`process.execute`, alcance `harness
check`, en `capabilities.extend`) y el adaptador pide correr `gate.checkCommand` antes de terminar.
Toda llamada de Claude Code corre con el TTL de caché de prompts de 5 minutos
(`agentlib.PROMPT_CACHE_ENV`: `FORCE_PROMPT_CACHING_5M=1`, `CLAUDE_CODE_PROMPT_CACHE_TTL=5m`,
`CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=5m`), registrado en `environment.json` (`agentCalls`).

### El adaptador (`claude_provider.py`, protocolo de proveedor 1.1)

El adaptador lee `kind` y responde cada llamada de solo lectura (clarify, review y los revisores del
panel de revisión, plan, acceptance, locate, architecture) con la propia representación de la
solicitud que hace el harness, leyendo el objeto `result` como lo hace el adaptador incorporado
`claude-code`; las llamadas implement reciben el prompt de la condición directa
(`agentlib.build_prompt`) seguido de lo que el harness agrega a la solicitud (retroalimentación,
contrato del gate, permisos, archivos de contexto, lecciones, pruebas de aceptación congeladas,
presupuesto, ubicaciones, contenido citado del repositorio, solicitud de autorreporte). Cada llamada
corre la línea de comandos de Claude Code de la condición directa (herramientas, modo de permisos,
presupuesto, límite de tiempo, `--strict-mcp-config`, `--safe-mode`); la condición anclada agrega el
`--model` y el `--effort` del enrutador. No se usó el adaptador incorporado `kind: claude-code`: su
línea de comandos no tiene lista de herramientas ni presupuesto, corre los revisores del panel con
`--permission-mode default` y los ajustes del proyecto, y arma su propio prompt. Desde la 2.0.0 el
prompt de cada condición muestra los ids que el archivo de tarea da a sus requisitos y criterios
(`req_spec: ...`), porque la trazabilidad de requisitos del harness los nombra. El harness corre el
adaptador desde un directorio que contiene solo `agentlib.py` y `claude_provider.py`
(`EVAL_PROVIDER_CODE`): la solicitud implement le muestra al agente la línea de comandos del
proveedor.

### La persona simulada (`person.py`, declarada como simulación)

Dos actores simulados, con reglas fijadas en el código antes de cualquier corrida:

- `human.product-owner-simulated` (`product_owner.py`) responde las preguntas sobre la tarea (C0 a
  C3, T1, A1, A2) con una llamada al agente por ronda; conoce la tarea completa y la especificación
  (un sesgo a favor de las condiciones gobernadas). Responde en todas las condiciones gobernadas.
  Desde la ola 9 el harness acota las rondas del agente (`intake.ambiguityReview`, #79: `maxRounds`
  3, a lo sumo 8 preguntas por ronda; después los puntos abiertos pasan a ser supuestos registrados
  y la corrida continúa); el tope propio de la persona (seis rondas, contando las de configuración
  del proyecto y las deterministas) es solo una protección por encima de esa cota.
- `human.reviewer-simulated` atiende todas las demás esperas, sin modelo:

| Espera | Regla |
|---|---|
| Configuración del proyecto (P1) | Respuestas fijas: testing `conventional`, standards `default`, architecture `custom`. |
| Contrato operativo | `harness task confirm` cuando no falta ningún ítem. |
| Opciones de arquitectura | `harness architecture decide --option` con la opción recomendada. |
| Reglas de arquitectura | APPROVE de las capas inferidas. |
| Pruebas de aceptación | APPROVE cuando hay al menos un archivo, cada uno con ruta y contenido, bajo el digest mostrado, y su contenido pasa la revisión del product owner (`review_acceptance_content`: cada archivo pytest se puede analizar y cada función `test_*` hace una comprobación que puede fallar, cada escenario Gherkin tiene un paso Then, al menos un caso de prueba por criterio de aceptación); si no, REJECT, nombrando lo que no alcanza. |
| Disputa de aceptación (agregada el 2026-11-11, tras una comprobación previa del aparato) | El agente disputa pruebas de aceptación congeladas y solo ellas fallan (`acceptance-dispute`). La persona lee la disputa en `acceptance show` (el código de las pruebas disputadas y las razones del agente, nunca las pruebas ocultas) y el product owner (`ProductOwner.judge_dispute`, registrado en `po-calls`) responde UPHOLD o REVISE a partir de los criterios de la tarea; sin product owner, la alternativa fija es UPHOLD. Ligada al digest que muestra el inbox; no se envía cuando `acceptance show` muestra otro. Tras REVISE, la regla de las pruebas de aceptación decide sobre las pruebas reescritas. |
| Plan o aprobación del plan | APPROVE de una descomposición cuyas subtareas tienen títulos, o del plan registrado de una tarea riesgosa, bajo el digest mostrado; si no, REJECT. |
| Preflight UNAVAILABLE | `harness verification decide --continue-uncertified`. |
| DECISION | La regla de la 0.9.0: APPROVE cuando el gate quedó PASSED (marcando la lista de verificación manual y reconociendo los factores de riesgo que exige el brief); si no, REQUEST_CHANGES con las razones del gate y los hallazgos como retroalimentación, a lo sumo dos ciclos; después, REJECT. Nunca APPROVE_EXCEPTION. |
| Una llamada de solo lectura falló o dio una respuesta ilegible | Nunca se actúa sobre ella: la corrida se detiene (`failed-call`). Desde la ola 9 el harness mismo reenvía una vez una respuesta de solo lectura rota (`runtime.contractRetry: {mode: once}`, #80, escrito por init); se quitó el reintento del lado de la evaluación agregado después del primer piloto. |
| Un comando de proveedor rechazado (fuera de los permisos, #87, o destructivo, #76) | Nunca se actúa sobre él: la corrida queda BLOCKED (salida 6) y se detiene (`policy-refusal`). |
| Presupuesto excedido, verificación diferida | Nunca se actúa sobre ellos: la corrida se detiene (se registra). |

La persona lee la espera de `harness inbox --json`, que desde la ola 9 (#73) lista cada espera
antes de DECISION con su `kind` (`contract`, `architecture`, `acceptance`, `decomposition`, `plan`,
`preflight`, `deferred`), el `digest` al que se liga la respuesta y el comando que la responde (cada
espera registra `source: inbox`); el resumen de fases es solo una alternativa para una espera que el
inbox no lista. Cada acto, REJECT incluido, usa `--no-continue` y luego `harness run continue`, así
que el código de salida de la corrida se lee en un solo lugar: desde #83 `plan decide --no-continue`
sale con 0 una vez registrado, y `run continue` sobre una corrida rechazada la reporta cerrada
(`FAILED`, salida 6; registrado como `continueAfterReject`). Cada espera se registra con lo que
mostró el harness y el comando que la respondió.

### Medidas

`measure.py` se aplica igual a todas las condiciones; una corrida detenida o rechazada cuyos cambios
el harness puso en cuarentena (stop the line) se mide sobre una copia del espacio de trabajo con el
parche en cuarentena aplicado, sobre la copia devuelta al commit de la línea base
(`measuredOn: quarantine-copy`). Un parche que no se aplica no se mide: el registro lleva
`invalid: quarantine-patch-not-applied`, va a `<out>.invalid.jsonl` y el runner sale con 3 (sin
repetir). `remeasure_quarantine.py <run-dir>... --cache <cache>` vuelve a medir el parche en
cuarentena de una corrida detenida desde su directorio (sin llamar al modelo), para registros
escritos antes de ese arreglo. `harness_state.py` lee el registro
propio del harness como segunda fuente: las ocho relaciones de traza de la 0.9.0 sin cambios y, por
separado, las relaciones de la 2.0.0 (aclaración respondida, aceptación decidida, requisito trazado,
certificación ligada, revisión ligada, enrutamiento registrado, `harness verify`), llamadas por tipo
y modelo, decisiones de enrutamiento, certificación por criterio, hallazgos de revisión por dominio,
afirmaciones sin respaldo, hallazgos de escritura, eventos del carril rápido, actores de las
decisiones y de las respuestas, segundos por fase y la cuarentena. Cada corrida guarda además en su
directorio `harness metrics --format json`, el brief de decisión y la salida de `harness verify`.
El tiempo se divide en llamadas al modelo, procesos propios del harness y persona simulada (P15);
las llamadas de gobernanza se reportan aparte de las de implementación.

### Corrida en seco

```bash
python evaluation/dry_run.py --work <dir> --cache <dir> --out <dir fuera del repositorio> \
  --core-reference-src <src del harness 1.0.0>      # todas las condiciones, CLI falso, 0 llamadas al modelo
```

`fake_claude.py` reemplaza a Claude Code (respuestas deterministas para cada tipo de llamada, la
solución de referencia para las implementaciones, un anfitrión MCP para el modo embebido, fallas
`broken`, `review-fail`, `malformed-acceptance` y `dispute-acceptance`). Además del resultado de cada
condición, la corrida en seco comprueba el comportamiento de la ola 9 del que depende el
ejecutor: el permiso exacto del proveedor (`extend`, o `grants` en `harness-core`) sin advertencias
de proveedor, cada espera respondida leída del inbox, el enrutamiento anclado con el modelo de la
celda como ancla, `plan decide --no-continue` saliendo con 0, una corrida rechazada que sigue
cerrada, y el reintento propio del harness ante una respuesta de aceptación malformada
(`agent.call.contract-retry`).

Córrela con el python de un venv que tenga las herramientas de medición (`freezegun` entre ellas) y
cuyo `harness` esté primero en el `PATH`. Para probar un árbol de fuentes en vez del paquete
instalado, lístalo en un archivo `.pth` del site-packages de ese venv, como hace `EVAL_EXTRA_PATH`
en `launchers/common.sh`, y no en `PYTHONPATH`: los procesos del agente y de la sesión anfitriona
corren con un entorno mínimo sin esa variable, así que el servidor MCP del modo embebido importaría
el paquete instalado. La corrida en seco sale con 2 antes de cualquier caso si `PYTHONPATH` está
definido, si `harness` no está en el `PATH` o si falta `pytest` o `freezegun`.

### Lanzamiento

Los lanzadores de `launchers/` toman del entorno toda ubicación propia de la máquina, comprueban la
cuenta de Claude antes de cualquier llamada real, mantienen la máquina despierta y registran
`environment.json`.

```bash
export EVAL_PYTHON=<venv con el wheel v2.0.0 y pytest 8.3.5, freezegun, coverage, ruff, bandit, mypy>/bin/python
export EVAL_WHEEL=<el wheel v2.0.0>  EVAL_WORK=<directorio de trabajo fuera del repositorio>  EVAL_CACHE=<directorio con itsdangerous-2.2.0.tar.gz>
export EVAL_CORE_REFERENCE_SRC=<src del harness 1.0.0>      # opcional: comprobación del digest de harness-core
bash evaluation/launchers/pilot.sh            # 5 corridas: direct, harness-core, harness, harness-anchored, casual x harness
EVAL_PILOT_MODEL=claude-sonnet-5-5 EVAL_OUT=evaluation/results-2.0.0/pilot-2.0.0-sonnet bash evaluation/launchers/pilot.sh   # piloto Sonnet, las mismas 5 corridas
bash evaluation/launchers/block-b.sh          # casual y mínimo x {direct, harness-core, harness} x 3 x 3 x 3
bash evaluation/launchers/block-a.sh          # completo x {direct, harness} x 3 x 3 x 3, luego harness-anchored x 3 x 3 x 2
bash evaluation/launchers/block-d.sh          # {casual direct, structured direct, harness} x 3 modelos x 3 sesiones
bash evaluation/launchers/block-embedded.sh   # modelo anfitrión x 3, brownfield, 2 repeticiones
```

El piloto Sonnet (enmienda del 2026-11-11 en `campaign/PREREGISTRO.md`) necesita su propio
`EVAL_OUT`: `pilot.sh` omite una corrida ya registrada con el mismo prompt y condición en
`pilot.jsonl`, sea cual sea el modelo, y toma el mismo candado que el piloto Haiku, así que corre
después de él.

Resúmenes: `report.py --v2 <results> <summary.json>` (bloques A y B: una celda por prompt,
condición, modelo y escenario, con cada corrida listada junto a la dispersión n/min/mediana/max),
`report_prompts.py --v2`, `report_phases.py <runs-dir> ...` (P15 por condición) y
`longitudinal/report.py` (bloque D).

### Modo embebido

Una sesión anfitriona no interactiva no puede preguntarle a una persona, así que `run_embedded.py`
hace de persona entre segmentos de la sesión y retoma la misma sesión (`--resume`) cuando la corrida
espera a la sesión. El anfitrión corre sin `--safe-mode` (que desactiva los servidores MCP y las
skills), con `--setting-sources project` y las herramientas MCP del harness agregadas a sus
herramientas permitidas; todo lo demás es la línea de comandos de la condición directa. Antes de la
ola 9 la corrida en seco con la configuración `harness` nunca llegaba a DECISION: el proveedor de
sesión tomaba como ediciones de la sesión las pruebas de aceptación congeladas que escribe el
harness, la verificación fallaba y stop the line ponía en cuarentena el cambio junto con esos
archivos. Desde la ola 9 (#81) esos archivos no son ediciones de la sesión y stop the line los
conserva, y la corrida en seco de la configuración `harness` queda aprobada (`embedded-dry.jsonl`,
caso `embedded-init`). `--variant no-acceptance` (pruebas de aceptación y stop the line desactivados,
declarado como ablación) se conserva para comparar.
