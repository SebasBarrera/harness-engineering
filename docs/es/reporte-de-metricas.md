# Reporte local de métricas

`harness metrics` calcula, solo con los registros de las ejecuciones y sin llamar a ningún modelo,
cuánto costaron, qué entregaron y cuánta intervención humana exigieron las ejecuciones gobernadas.
Esta guía explica cómo generar el reporte HTML, dónde queda, cómo abrirlo, qué contiene y cómo leer
cada sección. La guía de referencia de todos los formatos (en inglés) es
[local-metrics.md](../guides/local-metrics.md).

## Cómo generarlo

```bash
harness metrics                                   # escribe los tres archivos en .harness/metrics/
harness metrics --format html --output reporte.html --open
harness metrics --since 7d                        # solo las ejecuciones de los últimos 7 días
harness metrics --all-repos                       # todos los repositorios del registro de ejecuciones
```

Sin `--format`, el comando escribe `metrics.json`, `metrics.md` y `metrics.html` con los mismos
números. Los filtros `--since`, `--task`, `--model` y `--agent` acotan el período. El comando no
necesita ninguna clave de configuración y no llama a un modelo.

## Dónde queda almacenado

La ubicación es `.harness/metrics/` dentro del proyecto. Se eligió porque ya era la carpeta que usa
el comando (no se creó una convención nueva) y porque `.harness/` es el directorio donde el
harness guarda todo su estado local.

- `.harness/` está ignorado por Git: el reporte **es local** y no se versiona ni se sube por
  accidente. Contiene nombres de tareas, rutas de archivos con hallazgos y costos, que no
  necesariamente deben salir del equipo.
- Cada ejecución de `harness metrics` reemplaza los tres archivos con el estado actual de los
  registros; para conservar una foto de un período, expórtala con `--output`.
- Para llevarlo a otro lugar (por ejemplo, adjuntarlo a un informe), usa `--output RUTA` con
  `--format html`, o `--output-dir CARPETA` para los tres archivos.

## Cómo abrirlo

Es un único archivo HTML autocontenido: CSS y gráficos SVG en línea, sin JavaScript, sin fuentes,
sin imágenes y sin recursos remotos. Se abre sin conexión con doble clic, desde la terminal con
`open .harness/metrics/metrics.html` (macOS) o `xdg-open .harness/metrics/metrics.html` (Linux),
o con `harness metrics --open`. Los enlaces a issues y pull requests son anclas simples: la página
no carga nada por sí sola. Admite tema claro y oscuro. El mismo reporte se ve en la pestaña
Métricas de `harness api serve`.

## Cómo se organizó (investigación sobre Sonar)

Se tomó como referencia el informe de proyecto de SonarQube y SonarCloud. Lo siguiente proviene
de conocimiento previo del producto, **no se verificó en línea durante este trabajo**.

Organización de Sonar: una vista general con el estado de la *quality gate* (aprobada o fallida,
con las condiciones que fallaron); medidas en tarjetas con una calificación por dimensión
(fiabilidad, seguridad, mantenibilidad, cobertura, duplicación); separación entre *código nuevo* y
*código total*; listas de detalle (incidencias por severidad, regla y archivo) y una pestaña de
actividad con el histórico.

| Elemento de Sonar | Qué se tomó | Por qué |
|---|---|---|
| Estado de la quality gate con sus condiciones | Un **estado del período** en el encabezado, con las condiciones visibles y su conteo | Responde primero la pregunta "¿hay algo que mirar?" y muestra por qué |
| Tarjetas por dimensión | Tarjetas agrupadas en costo y tokens, tiempo, fricción, calidad y entrega | Es la lectura de arriba hacia abajo: resumen y luego detalle |
| Navegación entre secciones | Barra de anclas y los títulos de cada grupo de tarjetas enlazan a su sección | Un reporte largo necesita saltar sin JavaScript |
| Incidencias por severidad, regla y archivo | Hallazgos por severidad, por regla y por archivo | Es el recorrido de lo general a lo concreto |
| Actividad e histórico | Tendencias por día y por semana | El harness ya calcula esas series |
| Calificaciones A a E | **No se tomaron** | Una letra suena a certificación y requeriría umbrales que nadie configuró; sería inventar un criterio |
| Código nuevo contra total | **No se tomó** | El harness no mide calidad del código, sino del proceso; la separación equivalente es el filtro `--since` |
| Cobertura y duplicación | **No se tomaron** | El harness no las calcula; las producen otras herramientas |
| Deuda técnica en tiempo | **No se tomó** | No hay un modelo de esfuerzo en los datos |

## Qué contiene y cómo leerlo

El orden de arriba hacia abajo es: encabezado y navegación, estado del período, resumen por
dimensión y los grupos de detalle. Los textos del HTML están en inglés, como el resto de la salida
del CLI; esta guía los nombra en español.

### Estado del período

Es un resumen derivado **solo de números que el propio reporte ya tiene**. La regla es explícita y
determinista (código en `src/governed_harness/metrics/summary.py`):

- `NO_DATA` ("No data"): no hay ejecuciones en el período.
- `ATTENTION` ("Needs attention"): al menos una de estas cuatro condiciones tiene un conteo mayor
  que cero.
- `CLEAR` ("Clear"): las cuatro condiciones valen cero.

Las condiciones son: ejecuciones detenidas (`BLOCKED` fuera de la decisión humana), ejecuciones
rechazadas por una persona, hallazgos `HIGH` o `CRITICAL` en ejecuciones que no cerraron, y
ejecuciones que no cerraron cuya última compuerta fue fallida (`FAILED`, `ERROR`, `TIMED_OUT` o
`INCONCLUSIVE`). Las ejecuciones que esperan una decisión no cuentan como problema; se informan
aparte.

**No es una certificación de calidad.** `CLEAR` solo significa que ninguna ejecución del período
muestra esas cuatro señales; no dice nada sobre el código ni sobre lo que el reporte no mide. Un
hallazgo `HIGH` de una ejecución que luego cerró no cuenta, porque la persona ya decidió con él a
la vista. El estado se muestra con texto y símbolo, nunca solo con color.

### Resumen por dimensión

Cinco grupos de tarjetas. Todos los números del reporte anterior se conservan; los grupos solo los
reordenan.

- Costo y tokens: llamadas de agente, tokens, costo reportado, costo estimado y costo por
  funcionalidad entregada.
- Tiempo: espera humana (minutos) y mediana y percentil 90 de las llamadas de agente.
- Fricción: ejecuciones, tareas, interacciones humanas, tareas por encima de su objetivo y
  correcciones.
- Calidad: tasa de aprobación a la primera, ejecuciones detenidas y rechazadas, hallazgos
  `HIGH` o `CRITICAL` abiertos y límites alcanzados.
- Entrega: funcionalidades entregadas, issues resueltos y líneas añadidas y eliminadas.

### Costo y tokens

Tokens por modelo, agente, tarea y fase (gráfico de barras y tabla con los mismos números), costo
por agente y modelos usados. Sirve para ver qué agente o qué tarea concentra el consumo. Mira la
columna "Unpriced calls": son llamadas con tokens pero sin costo reportado ni precio configurado.

### Tiempo

Tiempo por fase (intentos, total y media) y por llamada de agente (media, mediana y percentil 90).
Una media muy superior a la mediana indica pocas llamadas muy largas.

### Fricción

Por tarea: tamaño, carril, ejecuciones, interacciones, aprobaciones, correcciones, minutos y
minutos de espera. Un valor por encima del objetivo de su tamaño se marca "over".

### Calidad

Medidas de las ejecuciones, hallazgos por severidad, por regla y por archivo (los 20 con más
hallazgos), límites alcanzados, resultado de la última compuerta de cada ejecución y las
ejecuciones detenidas con el motivo que registraron. Empieza por los archivos con más hallazgos
`HIGH` y por los motivos de las detenciones.

### Entrega

Funcionalidades entregadas (ejecuciones cerradas) con sus issues y pull request, y las líneas
añadidas y eliminadas atribuidas por procedencia a un agente, a una persona o sin atribuir.

### Tendencias

Ejecuciones y tokens por día, y tablas diarias y semanales. Son agregados por período, no por
persona.

### Calidad de los datos

Cada número tiene un origen y el reporte lo declara:

- **Observado**: sale de las marcas de tiempo y de los registros que el harness escribió (tiempos,
  interacciones, hallazgos, compuertas, límites).
- **Reportado**: lo devolvió el proveedor del agente (tokens y costo reportado).
- **Estimado**: se calculó con la tabla de precios que tú configuraste, para llamadas con tokens
  pero sin costo; se muestra aparte, nunca se cobra ni cuenta contra un presupuesto.

El reporte no contiene ningún indicador por persona (requisito RD-16 de la tesis).

## Información que se añadió y para qué sirve

Solo se añadió lo que los registros ya contienen. Cada línea indica la decisión que ayuda a tomar.

- Estado del período: decidir si hay que abrir el detalle o no.
- Hallazgos por severidad y por archivo: decidir dónde empezar a corregir.
- Hallazgos `HIGH` o `CRITICAL` abiertos: saber si queda algo bloqueante en ejecuciones sin cerrar.
- Resultado de la última compuerta por ejecución: ver si las ejecuciones sin cerrar fallan por la
  compuerta o por otra causa.
- Límites alcanzados (presupuesto, intentos de fase, ciclos de corrección): decidir qué límite
  subir o qué tarea dividir.
- Ejecuciones detenidas con su motivo: decidir cómo desbloquearlas sin abrir cada una.
- Correcciones por tarea: detectar tareas mal especificadas que necesitan varias iteraciones.
- Costo por funcionalidad entregada: comparar períodos y proyectar el presupuesto de lo que viene.
  Si hay llamadas sin precio, el valor es un mínimo.

Se evaluaron y **no** se añadieron:

- Aciertos de la caché de revisión: el harness no registra un evento por acierto, así que no hay
  dato real que mostrar.
- Espera por una persona: ya estaba (espera humana en tarjetas, fricción y tiempo).

Los campos nuevos están en el JSON bajo `summary`, `quality` (`findingsBySeverity`,
`findingsByFile`, `openBlockingFindings`, `gateResults`, `limitsReached`, `blockedRunsDetail`),
`totals.costPerFeatureUsd` y `friction.perTask[].corrections`. Los demás campos y formatos (JSON,
Markdown, CSV y Prometheus) conservan los que ya tenían.
