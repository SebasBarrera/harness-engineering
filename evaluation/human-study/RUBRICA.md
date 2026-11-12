# Rúbrica de la lectura ciega de diffs (T3, parte B)

Cada lectora califica **por separado** los dos cambios de cada par (Cambio 1 y Cambio 2) con los
cinco ítems siguientes, del 1 al 5. Solo se juzga lo que se ve en el enunciado de la tarea y en el
diff; no se ejecuta código. Si no puede juzgar un ítem, escriba `nd` (no determinable) en vez de
un número.

## Ítems y anclas

| Ítem | Pregunta | 1 | 3 | 5 |
|---|---|---|---|---|
| **Corrección aparente** | Leyendo el diff, ¿parece que el cambio hace lo que pide la tarea, sin errores visibles? | Hay errores visibles o no resuelve la tarea | Resuelve la tarea pero hay dudas o casos sin cubrir | Parece correcto y completo para lo pedido |
| **Claridad** | ¿Se entiende el cambio: nombres, estructura, tamaño de funciones, comentarios donde hacen falta? | Difícil de seguir | Se entiende con esfuerzo | Se entiende en una lectura |
| **Pruebas** | ¿Las pruebas que agrega o cambia cubren el comportamiento pedido, incluidos los casos de error? | Sin pruebas, o pruebas que no verifican nada relevante | Prueban el caso principal, faltan casos de error o bordes | Cubren el caso principal, errores y bordes relevantes |
| **Alcance** | ¿El cambio se limita a lo que pide la tarea? | Toca muchas cosas no pedidas | Algún cambio no pedido menor | Solo lo pedido |
| **Riesgo** | ¿Qué tan seguro sería integrar este cambio (efectos colaterales, interfaces públicas, manejo de errores, secretos, dependencias)? | Riesgo alto: rompe o puede romper algo | Riesgo moderado: algo a revisar | Riesgo bajo |

Los valores 2 y 4 son intermedios entre las anclas. En **Riesgo**, 5 es lo mejor (riesgo bajo),
igual que en los demás ítems: en todos, más es mejor.

## Además, por cada par

- **Preferencia:** ¿qué cambio integraría? `1`, `2` o `igual`.
- **Adivinanza:** uno de los dos se hizo con más controles de proceso. ¿Cuál cree que es? `1` o
  `2`.
- **Confianza en la adivinanza:** del 1 (al azar) al 5 (segura).
- **Comentario** (opcional, una línea).

## Hoja por par (copiar una por par)

Código de lectora: `P__`   Par: `__`

| Ítem | Cambio 1 | Cambio 2 |
|---|---|---|
| Corrección aparente | | |
| Claridad | | |
| Pruebas | | |
| Alcance | | |
| Riesgo | | |

Preferencia: ____   Adivinanza: ____   Confianza en la adivinanza: ____

Comentario: ________________________________________________

En la hoja de registro (`PLANTILLA-REGISTRO.csv`) cada par genera dos filas por lectora
(`parte = RUBRICA`, `rubrica_lado = 1` y `2`); preferencia, adivinanza y confianza se repiten en
las dos filas del par. La columna `condicion_oculta` se completa solo cuando las dos lectoras
terminaron.
