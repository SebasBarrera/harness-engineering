# Tareas en español

En `INTENT` el harness revisa la tarea con reglas deterministas, sin modelo de lenguaje: `C0`
(sin criterios de aceptación), `C1` (un criterio que no dice qué se puede observar), `C2` (una
cualidad sin medida), `C3` (un criterio repetido) y `T1` (una intención corta sin desglose). La
referencia completa está en [la política de criterios de aceptación](../reference/configuration.md#acceptance-criteria-policy).

Estas reglas leen palabras: palabras vagas ("funciona", "correctamente"), verbos de resultado
("devuelve", "rechaza"), palabras de cualidad ("rápido", "seguro") y números. La clave
`intake.language` decide en qué idioma las leen y en qué idioma hacen las preguntas.

```yaml
intake:
  criteriaPolicy: enforce
  language: auto   # auto, en o es
```

`harness init` escribe `language: auto`. Un `project.yaml` sin la clave conserva las reglas en
inglés y su digest de configuración.

| Valor | Qué hace |
|---|---|
| sin la clave | Las reglas anteriores: palabras ASCII (`contraseñas` se lee como `contrase` y `as`), vocabularios y preguntas en inglés. |
| `en` | Palabras Unicode comparadas sin tildes; vocabularios y preguntas en inglés. Para texto ASCII las preguntas son las mismas que sin la clave. |
| `es` | Palabras Unicode; los vocabularios en español se suman a los ingleses (una tarea en español conserva palabras de código en inglés) y las preguntas, y el siguiente paso de un hallazgo `warn`, salen en español. |
| `auto` | `es` o `en` según cada tarea. |

Los identificadores no cambian con el idioma: las preguntas siguen siendo `Q-1`, `Q-2`, ... con
las reglas `C0` a `T1` y los mismos objetivos, así que un archivo de respuestas sirve igual.

## Cómo se detecta el idioma

Con `auto` el harness cuenta señales en el título, la intención, los criterios y sus pistas de
verificación:

- cuenta para el español cada palabra funcional española ("el", "de", "que", "para"...), cada
  palabra común de pedidos cortos ("agrega", "implementa", "pruebas"...) y cada palabra de los
  vocabularios españoles, y cada `ñ`, `¿`, `¡` o vocal con tilde;
- cuenta para el inglés cada palabra funcional inglesa ("the", "of", "should"...);
- las palabras que existen en los dos idiomas ("a", "general", "once") no cuentan.

La tarea es española si el español suma más; un empate, incluida una tarea sin ninguna señal, es
inglés. El conteo es fijo: la misma tarea da siempre el mismo idioma y las mismas preguntas.

## Ejemplo

```yaml
taskId: task_descuento
title: Descuento
intent: Agrega un descuento.
acceptanceCriteria:
  - criterionId: AC-1
    text: Funciona.
  - criterionId: AC-2
    text: Debe ser rápido.
```

Con `language: auto` el harness pregunta lo mismo que con la tarea equivalente en inglés ("It
works.", "It must be fast."), con las mismas reglas y los mismos ids:

- `Q-1` (`C1`, `AC-1`): El criterio AC-1 ('Funciona.') no dice qué se puede observar. ¿Para qué
  entrada o acción, y qué resultado exacto muestra que se cumple?
- `Q-2` (`C2`, `AC-2`): El criterio AC-2 ('Debe ser rápido.') pide una cualidad ('rápido') sin
  una medida. ¿Qué número o umbral decide si se cumple (por ejemplo un tiempo, un tamaño o una
  tasa)?

Sin la clave, el mismo archivo recibe solo la pregunta `C1`, en inglés: la palabra "rápido" se
parte en "r" y "pido" y el harness no reconoce la cualidad.

## Lo que todavía sale en inglés

`intake.language` cubre las reglas deterministas `C0` a `C3` y `T1`. Las preguntas del contrato
operativo (`intake.operationalContract`), las de la configuración del proyecto (`P1`), los
resúmenes de fase (por ejemplo `Intent needs clarification: N question(s)`, que el modo
embebido reconoce) y los comandos sugeridos siguen en inglés. Las preguntas de la revisión del
agente (`A1`) las escribe el agente.
