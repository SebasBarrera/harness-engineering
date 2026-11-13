# Comandos por fase

Los 80 comandos de la [referencia de la CLI](../reference/cli.md), con la fase normativa en que se usan. Las fases son INTENT, DISCOVERY, SPECIFICATION, PLANNING, IMPLEMENTATION, VERIFICATION, INDEPENDENT_REVIEW, DECISION y CLOSURE ([las nueve fases](../architecture/phases.md)). «Configuración» marca los comandos que se usan antes de la primera ejecución o para ajustar el proyecto; «Cualquiera» marca los que consultan o administran sin pertenecer a una fase.

La fase sale de lo que el comando hace según su descripción en la referencia generada y de la tabla de fases. `do`, `run start` y `run continue` recorren todas las fases hasta una condición de parada. Ningún comando de la CLI corresponde por sí solo a IMPLEMENTATION ni a INDEPENDENT_REVIEW: las ejecuta el harness dentro de una ejecución.

| Comando | Fase en que se usa | Para qué |
|---|---|---|
| `harness init` | Configuración | Crea `.harness/project.yaml` |
| `harness inspect` | Configuración | Detecta los perfiles tecnológicos (solo lectura) |
| `harness doctor` | Configuración | Revisa el entorno local |
| `harness project show` | Configuración | Qué detecta el harness del proyecto |
| `harness config validate` | Configuración | Valida la configuración |
| `harness config lint` | Configuración | Contradicciones entre la configuración y los archivos de instrucciones del agente |
| `harness standards show` | Configuración | Paquetes de estándares por lenguaje |
| `harness agent-contract schema` | Configuración | Esquema JSON de los contratos de agente |
| `harness agent-contract validate` | Configuración | Valida un JSON contra un contrato |
| `harness agent-contract scripts` | Configuración | Scripts que los agentes pueden llamar como cajas negras |
| `harness plugins list` | Configuración | Extensiones incorporadas y protocolo de plugins |
| `harness pr forge` | Configuración | Forja que resuelve el espacio de trabajo |
| `harness review rules show` | Configuración | Catálogo efectivo de reglas de revisión |
| `harness review rules sync` | Configuración | Escribe el bloque de reglas de los revisores |
| `harness review hook install` | Configuración | Instala el hook pre-push de revisión |
| `harness task create` | INTENT | Valida y persiste el archivo de tarea |
| `harness task list` | INTENT | Lista las tareas |
| `harness task show` | INTENT | Muestra una tarea |
| `harness task questions` | INTENT | Preguntas de aclaración que INTENT hizo |
| `harness task clarify` | INTENT | Responde las preguntas y guarda la revisión |
| `harness task confirm` | INTENT | Confirma el contrato operativo de la tarea |
| `harness architecture decide` | INTENT (proyecto nuevo) / DISCOVERY (existente) | Decide la arquitectura: elegir una opción o aprobar las reglas inferidas |
| `harness architecture show` | DISCOVERY | Arquitectura del proyecto y estudio en caché |
| `harness architecture refresh` | DISCOVERY | Marca obsoleto el estudio para volver a hacerlo |
| `harness acceptance show` | SPECIFICATION | Pruebas de aceptación propuestas |
| `harness acceptance decide` | SPECIFICATION | Aprueba o rechaza las pruebas de aceptación |
| `harness plan show` | PLANNING | Descomposición propuesta |
| `harness plan decide` | PLANNING | Aprueba o rechaza la descomposición |
| `harness verification show` | PLANNING | Plan de verificación y preflight |
| `harness verification decide` | PLANNING | Decide continuar sin certificar lo que el entorno no puede verificar |
| `harness check` | VERIFICATION | Corre los validadores del gate sin registrar nada |
| `harness acceptance dispute` | VERIFICATION | Resuelve la disputa de pruebas de aceptación congeladas |
| `harness review` | DECISION | Informe de decisión de la ejecución |
| `harness gate decide` | DECISION | Registra la decisión humana ligada al digest |
| `harness pr publish` | CLOSURE | Publica el informe de decisión en el pull request |
| `harness pr create` | CLOSURE | Abre el pull request desde la rama de cierre |
| `harness pr status` | CLOSURE | Fija el estado de commit `governed-harness` |
| `harness run cleanup` | CLOSURE | Quita el worktree de una ejecución aislada terminada |
| `harness retrospect` | CLOSURE | Observaciones y recomendaciones de una ejecución cerrada |
| `harness recommendation list` | CLOSURE | Recomendaciones retrospectivas |
| `harness recommendation decide` | CLOSURE | Acepta, edita o rechaza una recomendación |
| `harness do` | Cualquiera (recorre todas las fases) | Crea la tarea y la ejecuta con un solo comando |
| `harness run start` | Cualquiera (recorre todas las fases) | Crea y ejecuta una ejecución hasta una condición de parada |
| `harness run continue` | Cualquiera (reanuda en la fase detenida) | Reanuda una ejecución |
| `harness run cancel` | Cualquiera | Cancela una ejecución |
| `harness run quarantine` | Cualquiera | Guarda los cambios de una ejecución sin aprobar y restaura la línea base |
| `harness run list` | Cualquiera | Lista las ejecuciones |
| `harness status` | Cualquiera | Estado completo de una ejecución |
| `harness inbox` | Cualquiera | Ejecuciones que esperan a una persona |
| `harness budget show` | Cualquiera | Consumo frente al presupuesto |
| `harness budget raise` | Cualquiera | Sube un límite de presupuesto excedido |
| `harness evidence list` | Cualquiera | Evidencias de una ejecución |
| `harness evidence attach` | Cualquiera | Adjunta evidencia (informe diferido, adjunto manual o contexto de tarea) |
| `harness findings list` | Cualquiera | Hallazgos de una ejecución |
| `harness artifact show` | Cualquiera | Contenido de un artefacto con su digest verificado |
| `harness trace` | Cualquiera | Exporta la traza (Markdown, JSON, JSONL, SARIF, Code Quality) |
| `harness verify` | Cualquiera | Verifica el registro de una ejecución |
| `harness export` | Cualquiera | Paquete de evidencia portable |
| `harness verify-approval` | Cualquiera | Comprueba en CI que un rango es un ChangeSet aprobado |
| `harness gc` | Cualquiera | Aplica la retención a ejecuciones terminadas |
| `harness registry` | Cualquiera | Proyectos del registro de ejecuciones |
| `harness metrics` | Cualquiera | Métricas locales, sin llamadas a modelos |
| `harness review-code` | Cualquiera (fuera de una ejecución) | Revisión de un cambio con el panel de revisores |
| `harness review verify` | Cualquiera | Verifica la evidencia de revisión de un commit |
| `harness review variance` | Cualquiera | Variabilidad de la revisión |
| `harness memory add` | Cualquiera | Registra una entrada de memoria |
| `harness memory list` | Cualquiera | Lista la memoria con su estado |
| `harness memory manifest` | Cualquiera | Contexto aplicado a una ejecución |
| `harness memory approve` | Cualquiera | Aprueba un registro propuesto |
| `harness memory invalidate` | Cualquiera | Invalida un registro sin borrarlo |
| `harness outcome record` | Cualquiera | Liga un incidente o reversión posterior a una ejecución |
| `harness outcome list` | Cualquiera | Lista los resultados posteriores |
| `harness exceptions list` | Cualquiera | Excepciones del proyecto |
| `harness rules health` | Cualquiera | Comportamiento de cada regla y validador |
| `harness routing calibrate` | Cualquiera | Costo por tarea aprobada de cada decisión de ruteo |
| `harness agent-contract calls` | Cualquiera | Llamadas de agente de una ejecución |
| `harness mcp serve` | Cualquiera | Sirve el harness a una sesión de agente por MCP |
| `harness api serve` | Cualquiera | API local y tablero web |
| `harness benchmark run` | Cualquiera | Microbenchmarks sintéticos |
| `harness benchmark scenarios` | Cualquiera | Camino directo frente al gobernado |

`plan decide`, `acceptance decide` y `verification decide` detienen la ejecución en la fase indicada hasta que una persona decide; después se continúa con `harness run continue`.
