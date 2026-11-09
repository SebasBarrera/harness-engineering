# Ingeniería inversa de proyectos existentes

Cuando usas el harness en un proyecto que ya tiene código (brownfield), el harness necesita
entender cómo está construido antes de modificarlo. Esta guía explica cómo hace ese análisis una
sola vez, qué documentos deja dentro del proyecto, cómo los usan los agentes después y cómo
comprobar que el análisis no se repite sin necesidad.

## Cómo activarlo

La función está detrás de la clave `architecture.documentation: project` de
`.harness/project.yaml`. `harness init` la escribe junto con `architecture.mode: agent`:

```yaml
architecture:
  mode: agent
  documentation: project
```

Sin la clave, el análisis de arquitectura funciona como antes (estilo, capas y resumen en
`.harness/architecture.md`) y no se escriben los documentos nuevos. `harness config validate`
muestra el valor efectivo en `engineering.architecture.documentation`.

Si ya tenías un análisis hecho y activas la clave después, el siguiente run lo repite una vez
para escribir los documentos, porque faltan.

## Cómo se ejecuta

No hay un comando aparte: ocurre en DISCOVERY del primer run sobre el proyecto, con una sola
llamada de solo lectura al agente. Si el análisis infiere reglas de capas, la ejecución queda
bloqueada hasta que una persona las aprueba o rechaza:

```bash
harness run start --task <id>
harness architecture decide --run <run> --decision APPROVE --digest <digest> \
  --rationale "Coincide con el código"
```

La aprobación de las reglas es independiente de los documentos: los documentos se escriben
cuando el análisis termina, antes de la aprobación.

## Qué información analiza

- La disposición de directorios con código fuente (la lista la calcula el harness, sin agente).
- Una muestra de archivos representativos (el agente no lee todo el código).

Con eso el agente describe: el estilo y las capas, la organización de archivos y carpetas, los
componentes principales, las convenciones que muestra el código y cómo deben actuar los agentes
sobre el proyecto. Una respuesta incompleta (sin organización, sin componentes o sin guía de
actuación) se rechaza como mal formada y la fase queda bloqueada con el hallazgo
`architecture.malformed`; no se escriben documentos parciales.

## Qué documentos genera y dónde

Todo queda dentro del proyecto, en `.harness/reverse-engineering/`:

| Archivo | Contenido |
|---|---|
| `README.md` | Índice, y cómo actualizar los documentos |
| `structure.md` | Directorios con código (calculados por el harness), organización descrita por el agente y resumen de la arquitectura |
| `components.md` | Componentes principales: nombre, ruta y responsabilidad |
| `conventions.md` | Convenciones observadas: nombres, pruebas, errores, configuración |
| `working-agreement.md` | Cómo debe actuar el harness sobre el proyecto desde ahora |

Además siguen existiendo `.harness/architecture.md` (resumen) y `.harness/architecture.json`
(estado: estilo, reglas, digests de los documentos). Cada documento lleva el digest del análisis
que lo generó; no se editan a mano, porque un refresco los reescribe.

## Cómo los usa el harness después

En cada llamada al agente, el bloque `architecture` de la solicitud incluye
`reverseEngineering`: la carpeta, la lista de archivos, el digest del conjunto y el acuerdo de
trabajo (recortado). Las instrucciones piden leer los documentos antes de buscar en el código y
no analizar de nuevo todo el proyecto. Los agentes consultan los documentos bajo demanda, no se
envían completos en la solicitud.

## Cómo se demuestra que el análisis no se repite

- Mientras la disposición de directorios no cambie y los documentos existan, los runs siguientes
  no llaman al agente de arquitectura.
- Cada reutilización queda registrada: el evento `architecture.survey.reused` incluye
  `agentCalls: 0` y `documentation` (el digest de los documentos), el mismo que registró
  `architecture.survey.completed` al generarlos. Lo ves con `harness trace --run <run> --format jsonl`.
- `harness architecture show` lista los documentos con su digest y si cada archivo sigue en el
  proyecto (`reverseEngineering.files`).
- Hay pruebas automáticas (`tests/integration/test_reverse_engineering.py`) que ejecutan dos
  runs y comprueban una sola llamada de arquitectura, el evento de reutilización y que los
  archivos no cambian byte a byte.

## Cómo refrescar el análisis

- A mano, cuando el proyecto cambió de forma importante: `harness architecture refresh`. Marca el
  análisis como obsoleto; el siguiente run lo repite (una llamada) y reescribe los documentos. Las
  reglas aprobadas siguen vigentes hasta que se decida el nuevo análisis.
- Automático, con `architecture.refresh: auto`: se repite cuando menos del 80 % de los directorios
  con código es común a la disposición registrada.
- Si borras los documentos, el siguiente run repite el análisis una vez para escribirlos de nuevo.
  Esto vuelve a pedir la aprobación de las reglas de capas, como cualquier análisis nuevo.

## Límites

- Con `architecture.layers` configuradas a mano no hay análisis: son las reglas tal como las
  escribiste.
- Si el proyecto se detecta como nuevo (sin código), no hay ingeniería inversa; usa la guía de
  [arquitecturas](arquitecturas.md).
- El análisis lo hace un agente sobre una muestra del código: revisa los documentos antes de
  apoyarte en ellos para decisiones importantes.
