# Arquitecturas para proyectos nuevos

Esta guía explica cómo el harness ayuda a elegir y aplicar una arquitectura cuando el proyecto
se crea desde cero (greenfield): qué estilos ofrece, cómo recomienda uno, cómo decides tú y qué
hace después un experto en el estilo elegido.

## Cómo activarlo

La función está detrás de la clave `architecture.catalogue: experts` de `.harness/project.yaml`.
`harness init` la escribe junto con `architecture.mode: agent`:

```yaml
architecture:
  mode: agent
  catalogue: experts
```

Un `project.yaml` sin la clave conserva el comportamiento anterior: el harness no ofrece el
catálogo, no escribe el reporte ni llama al experto, y el digest de la configuración no cambia.
`harness config validate` muestra el valor efectivo en `engineering.architecture.catalogue`.

## El catálogo de diez estilos

Los estilos son los ocho que el harness ya reconocía más dos que faltaban. Se escogieron por ser
los más usados en proyectos de software de propósito general y de interfaces, y porque cada uno
se puede describir con reglas de dependencia que una herramienta puede revisar (o, cuando no hay
capas, con reglas de módulos o contratos).

| Estilo | Para qué sirve | Reglas de capas incluidas |
|---|---|---|
| `layered` | Separar presentación, servicios y datos; cada capa usa solo la de abajo | presentation, services, data |
| `hexagonal` | Un núcleo de negocio aislado que habla con el exterior mediante puertos y adaptadores | domain, application, adapters |
| `clean` | Anillos concéntricos con dependencias solo hacia adentro | entities, usecases, adapters, frameworks |
| `ddd` | Dominios complejos con lenguaje ubicuo y contextos acotados | domain, application, infrastructure, interfaces |
| `modular-monolith` | Una sola aplicación con módulos de interfaz pública explícita | no tiene plantilla: los módulos son propios de cada proyecto |
| `microservices` | Servicios que se despliegan y escalan por separado | no tiene plantilla: los servicios se comunican por contratos |
| `mvvm` | Pantallas con un modelo de vista que expone estado observable | model, viewmodel, view |
| `mvi` | Pantallas con un único estado inmutable y un ciclo unidireccional | model, intent, view |
| `event-driven` | Componentes que reaccionan a eventos publicados por otros | no tiene plantilla: productores y manejadores dependen del contrato de eventos |
| `cqrs` | Modelos separados para escribir (comandos) y leer (consultas) | domain, commands, queries |

Cada estilo viene empaquetado con el harness (carpeta `resources/architecture` del paquete,
incluida en el wheel) en un archivo con: propósito, cuándo encaja, beneficios y costos, estructura
de carpetas, mejores prácticas, reglas de dependencia y el prompt del experto. Al elegir un
estilo, el documento del estilo (sin el prompt) se copia a `.harness/architecture-style.md`
dentro del proyecto.

Las plantillas de capas usan rutas genéricas (por ejemplo `**/domain/**`). Si tu proyecto usa
otros nombres, ajusta `architecture.layers` en `project.yaml`: las capas configuradas tienen
prioridad.

## El flujo, paso a paso

1. Creas la tarea y ejecutas `harness run start --task <id>`. En INTENT, si el proyecto es nuevo,
   el agente padre analiza la tarea y la estructura inicial y responde con dos a cuatro opciones
   del catálogo, marcando exactamente una como recomendada.
2. La ejecución queda en `BLOCKED`. El mensaje indica la opción recomendada y el reporte.
3. Lees el reporte en `.harness/architecture-options.md` (también con `harness architecture show`,
   campo `recommendation`). Dice qué arquitectura se recomienda, por qué y qué características
   del proyecto la respaldan, y lista las demás opciones con sus beneficios y costos.
4. Decides, siempre ligado al digest de las opciones:

```bash
# Aceptar la recomendada
harness architecture decide --run <run> --option <id-recomendada> --digest <digest> \
  --rationale "Encaja con el proyecto"

# Elegir otra de las opciones propuestas
harness architecture decide --run <run> --option <otro-id> --digest <digest> \
  --rationale "Preferimos esta"

# Elegir cualquier estilo del catálogo, aunque no se haya propuesto
harness architecture decide --run <run> --style cqrs --digest <digest> \
  --rationale "Leemos y escribimos de forma muy distinta"
```

   Si eliges con `--style` un estilo que el agente sí propuso, se usan las capas que propuso. Si no
   lo propuso, se usan las capas de la plantilla del catálogo (o ninguna, para los estilos sin
   plantilla). Una decisión de un actor que no es una persona, un digest desactualizado, un estilo
   fuera del catálogo o usar `--option` y `--style` a la vez se rechazan (código de salida 5).
5. El harness registra la decisión como un ADR (`.harness/adr/ADR-0001-architecture.md`) con las
   reglas de capas, y el estilo queda vigente.
6. Al reanudar, interviene el experto del estilo elegido: una llamada de solo lectura que recibe
   su prompt especializado y planifica cómo organizar el proyecto (carpetas y módulos con su
   propósito, y las mejores prácticas que aplican). El plan se guarda en
   `.harness/architecture-organization.md` y en el estado; no se vuelve a pedir en las
   ejecuciones siguientes.
7. En cada llamada posterior al agente (implementación, revisión), el bloque `architecture` de la
   solicitud lleva el prompt del experto, el resumen del plan, el estilo y el camino del
   documento del estilo. Las instrucciones piden al agente actuar como ese experto y mantener la
   implementación alineada con el estilo.

## Qué hace y qué no hace el experto

- La llamada del experto es de solo lectura, como el resto de las llamadas de arquitectura: propone
  la organización, no crea carpetas ni archivos. Quien escribe el código es el agente de
  implementación, que recibe el prompt del experto y el plan.
- La alineación se verifica con las reglas de capas del estilo en VERIFICATION
  (`architecture.layer-violation`). En los estilos sin plantilla de capas no hay verificación
  automática de dependencias; si quieres una, configura `architecture.layers` y `allow`.

## Cómo comprobar que funcionó

- Reporte: `.harness/architecture-options.md`.
- Eventos: `harness trace --run <run> --format jsonl` muestra `architecture.options.proposed` (con la opción
  recomendada), `architecture.decided` (con `style` y `acceptedRecommendation`) y
  `architecture.organization.proposed` (con el digest del plan).
- Evidencia: las opciones y el plan se guardan como evidencia de la ejecución.
- Solicitud al agente: el artefacto de la solicitud de implementación contiene
  `architecture.expert.prompt`.

## Archivos que genera

| Archivo | Para qué sirve |
|---|---|
| `.harness/architecture.json` | Estado: opciones, decisión, reglas y plan del experto |
| `.harness/architecture-options.md` | Reporte de la recomendación |
| `.harness/adr/ADR-0001-architecture.md` | Registro de la decisión |
| `.harness/architecture-style.md` | Documentación del estilo elegido |
| `.harness/architecture-organization.md` | Plan de organización del experto |

## Proyectos existentes

Para un proyecto que ya tiene código, el harness no ofrece el catálogo: infiere la arquitectura
existente. Consulta la guía de [ingeniería inversa](ingenieria-inversa.md).
