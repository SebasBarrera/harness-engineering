# Guion del facilitador

Se lee tal cual. Lo que va en cursiva son acciones del facilitador, no se lee. Ver
`PROTOCOLO.md` para el procedimiento y las medidas.

## Antes de la sesión

- *Confirmar que el preregistro está versionado y anotar su SHA en la hoja de registro.*
- *Tener abiertos, en el mismo editor y con la misma fuente, los tres briefs preparados
  (`brief-A.txt`, `brief-B.txt`, `brief-C.txt`) en el orden que corresponde al código de la
  persona (tabla de la sección 4.1 del protocolo). Sin resaltado del diff.*
- *La clave de qué brief tiene el defecto no está en la máquina de la sesión.*
- *Cronómetro listo, hoja de registro abierta con el código de la persona, `SUS.md` impreso o en
  un formulario sin datos personales.*

## 1. Bienvenida y consentimiento (4 min)

> Gracias por venir. Estoy evaluando materiales para mi tesis sobre cómo se revisan cambios de
> código. No te estoy evaluando a ti: no hay respuestas correctas ni incorrectas, y lo que me
> sirve es lo que harías de verdad.
>
> Antes de empezar, te pido que leas este consentimiento. Puedes preguntarme lo que quieras.
> Algunas preguntas sobre el estudio solo te las podré responder al final, para no influir.

*Esperar la firma. Asignar el código y escribirlo en la hoja. No escribir el nombre en la hoja.*

> En los registros vas a aparecer como «P[n]». No grabo audio ni video; solo tomo notas y mido
> el tiempo.

## 2. Orientación (3 min)

> Te voy a mostrar tres resúmenes de decisión, uno por uno. Cada uno describe un cambio de
> código hecho para una tarea: qué se pidió, qué cambió, qué riesgos se encontraron, qué se
> verificó y qué no, y el diff. Están en inglés.
>
> Imagina que eres la persona que tiene que decidir si ese cambio se acepta. Para cada uno, dime
> una de estas decisiones:
>
> - **APPROVE**: lo aceptas tal como está.
> - **REQUEST_CHANGES**: pides cambios antes de aceptarlo.
> - **REJECT**: lo rechazas.
> - **APPROVE_EXCEPTION**: lo aceptas aunque algo no cumple, con una excepción justificada.
>
> Lee lo que necesites, a tu ritmo. Puedes moverte por todo el archivo. No puedes ejecutar
> código. Cuando hayas decidido, dime la decisión en voz alta; después te pediré que me
> expliques por qué y qué tan seguro o segura estás.
>
> ¿Alguna pregunta antes de empezar?

*Responder solo dudas sobre la mecánica. Si pregunta por el contenido o el objetivo: «Te lo
cuento al final».*

## 3 a 6. Los tres briefs (hasta 8 min cada uno)

*Para cada brief, en el orden asignado:*

> Este es el primero [segundo / tercero].

*Mostrar el archivo y empezar el cronómetro en ese momento. No hablar mientras lee.*

*Si a los 7 minutos no ha decidido:*

> Tenemos que avanzar: con lo que viste hasta ahora, ¿qué decisión tomarías?

*Detener el cronómetro cuando diga la decisión. Anotar `tiempo_s`, `decision` y `tope_alcanzado`.*

> ¿Por qué esa decisión?

*Anotar la justificación lo más fiel posible, sin interpretar. Si se queda corto, se puede
repetir una sola vez:* «¿Algo más?»

> Del 1 al 5, donde 1 es nada seguro y 5 es totalmente seguro, ¿qué tan seguro o segura estás de
> esa decisión?

*Anotar `confianza_1a5`. Cerrar el archivo antes de mostrar el siguiente.*

## 7. Pregunta posterior (2 min)

*Solo después de los tres briefs. Para cada uno, en el mismo orden, mostrar de nuevo su primera
pantalla:*

> Sobre este cambio: ¿hubo algo que te preocupara?

*Anotar la respuesta en `defecto_detectado_tras_pregunta` (se codifica después con el criterio
preregistrado) y en `notas_facilitador`. No hacer preguntas de seguimiento sobre el contenido.*

## 8. Cuestionario SUS (3 min)

> Por último, un cuestionario de diez frases. Cuando dice «sistema», piensa en los resúmenes de
> decisión que acabas de usar. Marca qué tan de acuerdo estás con cada una; responde rápido, con
> tu primera impresión, y no dejes ninguna en blanco.

*Entregar `SUS.md`. No comentar las frases.*

## 9. Cierre (1 min)

> Eso es todo, gracias. Te pido que no comentes los materiales con otras personas que puedan
> participar, hasta que termine el estudio. Ahora sí, si quieres, te cuento de qué se trata.

*Si pregunta, explicar el objetivo general. No decir cuál brief tenía el defecto hasta que todas
las personas hayan participado; ofrecer contárselo después.*

## Lo que no se dice (para no sesgar)

Durante la sesión de la parte A, el facilitador **no**:

- dice que uno de los briefs tiene un defecto, cuántos tienen problemas, ni que «hay algo que
  encontrar»;
- usa las palabras «harness», «agente», «IA», «modelo», «campaña», «direct», «control» ni el
  nombre de la herramienta, antes del cierre;
- dice que los cambios los hizo un agente de IA (si la persona lo pregunta: «Te lo cuento al
  final»);
- reacciona a las decisiones (ni «bien», ni «interesante», ni gestos, ni silencios largos
  distintos entre briefs);
- explica una sección del brief, un hallazgo o una línea del diff; si pregunta qué significa
  algo: «Decide con lo que ves en el resumen»;
- sugiere una decisión ni comenta cuál es la más común;
- apura o deja más tiempo según el brief: el tope es el mismo para los tres;
- menciona la tesis, la hipótesis o resultados de otras personas;
- vuelve atrás para que la persona cambie una decisión ya dicha. Si la persona cambia de opinión
  por su cuenta, se anota la primera y la nueva en `notas_facilitador`; la primera es la que
  cuenta.

## Parte B (lectura ciega): instrucciones para cada lectora

> Te voy a mostrar diez pares de cambios. En cada par, los dos cambios resuelven la misma tarea,
> que está escrita arriba. Califica cada cambio con esta rúbrica de cinco ítems, del 1 al 5. Luego
> dime cuál de los dos prefieres, o si te dan igual. Por último, uno de los dos se hizo con más
> controles de proceso que el otro: dime cuál crees que es y qué tan seguro o segura estás, del 1
> al 5. No pasa nada si no lo sabes; responde lo que te parezca.

*Entregar `RUBRICA.md`. No decir qué controles ni cómo se hicieron los cambios. No comentar los
pares. Cada lectora trabaja sola.*
