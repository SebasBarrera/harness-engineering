# Límites de iteraciones, tokens y tiempo

Cada agente que llama el harness tiene tres límites: un tope de iteraciones, un presupuesto de
tokens y un límite de tiempo. Se activan con `agentContract` (ver
[Contrato entre agentes](contrato-de-agentes.md)). Esta página explica qué significa cada uno,
por qué tienen esos valores y qué pasa cuando se alcanzan.

## Tope de iteraciones

**Qué es una iteración.** Una llamada al agente con su propio resultado. Cuentan todas estas:

- el primer intento;
- cada corrección (`runtime.verificationCorrections`, la corrección automática de la revisión);
- cada reintento por contrato (`runtime.contractRetry`);
- cada escalamiento a un modelo más fuerte (`agentRouting.maxEscalations`);
- cada llamada cortada por un límite de tokens o de tiempo.

No cuenta como iteración el reintento de infraestructura de un proveedor
(`runtime.providerRetries`): es la misma llamada enviada otra vez porque falló el proceso o la red,
sin un resultado del agente.

**Qué es "un agente".** El conteo es por ejecución y por tipo de llamada: el agente que implementa,
el que aclara, el que planifica, el que escribe las pruebas de aceptación, el que ubica el cambio,
el que propone la arquitectura y el que revisa. El panel de revisión cuenta una iteración por
ronda, no una por revisor, porque los revisores de una ronda trabajan a la vez. Una ejecución
nueva empieza de cero.

**Cuál es el tope.** 5 por defecto (`agentContract.maxIterations`). La configuración no acepta más
de 6: un valor mayor se rechaza al validar el archivo.

**Por qué 5.** Los contadores que ya existían se quedan como sublímites dentro del tope. Con los
valores que escribe `harness init` (`verificationCorrections: 2`, corrección automática de la
revisión con `maxAttempts: 2`) el agente que implementa puede necesitar legítimamente
1 + 2 + 2 = 5 llamadas. Un tope de 5 no corta ningún flujo que la configuración permita y sí
detiene todo lo demás. Es una derivación de los valores por defecto, no una medición con agentes
reales. Un tope menor (3 o 4) cortaría flujos que esos mismos valores permiten; 6 es el máximo
admitido para proyectos que suban sus sublímites. Si un agente no converge en 5 llamadas, una
sexta con la misma entrada rara vez cambia el resultado: lo más probable es que el problema esté
en la entrada, no en el intento.

**Qué pasa al alcanzarlo.** El harness no vuelve a llamar al agente. La fase queda `BLOCKED` con un
resultado como este:

```text
agent.iterations.exhausted: the acceptance agent used 5 of 5 iterations in this run without
finishing. The input may be badly structured (a badly structured prompt, wrong or insufficient
context, excessive context or a badly delimited task). Do not run the task again unchanged.
Restructure the input first: rewrite the task in one clear objective, name the files it owns and
the criteria that prove it, split it when it asks for more than one thing, then start a new run.
```

Queda un hallazgo `HIGH` con la regla `agent.iterations.exhausted`, el evento
`agent.iterations.exhausted` y el artefacto `agent-iteration-cap`, que lista cada iteración con su
estado, las cuatro causas posibles y la recomendación:

- un prompt mal estructurado (la tarea es vaga, contradictoria o demasiado larga);
- contexto incorrecto o insuficiente (al agente le faltan archivos o datos);
- contexto excesivo (se envía tanto que el agente pierde el hilo);
- una tarea mal delimitada (pide más de una cosa o no deja claros sus límites).

Reestructure la tarea y empiece una ejecución nueva. Continuar la misma ejecución volvería a
bloquear, porque el conteo es de la ejecución. `harness agent-contract calls --run RUN` muestra
cuántas iteraciones usó cada agente.

## Tamaño de la tarea

El tamaño es el que ya calcula el router (`agentRouting`): S, M o L según tres señales (requisitos,
archivos que la tarea posee o nombra, y sus líneas). Gana la clase más grande entre las señales.
Los umbrales por defecto que escribe `harness init` son:

| Señal | S hasta | M hasta | L por encima de |
|---|---|---|---|
| Requisitos | 5 | 15 | 15 |
| Archivos | 5 | 20 | 20 |
| Líneas | 2000 | 10000 | 10000 |

Una bandera de riesgo (interfaz pública o palabras de seguridad) sube S a M, y una tarea que la
planificación ya descompuso por grande es L. Se puede calibrar con `harness routing calibrate`.
No se creó una segunda clasificación: se reutiliza la del router.

## Presupuesto de tokens y límite de tiempo

Cada llamada recibe un presupuesto según el tamaño de su tarea, anotado en el contrato
(`tokenBudget`, `timeLimitSeconds`, `sizeClass`):

| Tamaño | Tokens por llamada | Tiempo por llamada |
|---|---|---|
| S | 50 000 | 600 s (10 min) |
| M | 200 000 | 1800 s (30 min) |
| L | 800 000 | 5400 s (90 min) |

Son valores de partida con una razón 1:4:16 entre tamaños, que sigue el crecimiento de la tarea;
no son un óptimo medido. Conviene calibrarlos con el uso registrado y ajustarlos por proyecto:

```yaml
agentContract:
  budgets:
    M:
      tokens: 300000
      wallSeconds: 2400
```

Tokens son entrada, salida y razonamiento. Los límites de `budget` (por llamada, por tarea y por
ejecución) siguen funcionando como antes y se suman a estos.

## Qué pasa al alcanzar un límite

- **Tiempo.** El tiempo límite acorta el tiempo máximo del proceso (el menor entre
  `runtime.commandTimeoutSeconds` y el del tamaño). Al vencerse, el harness detiene el proceso del
  agente.
- **Tokens.** El harness no puede cortar a media llamada un CLI que no lo permite: compara el
  consumo que el proveedor reportó al terminar con el presupuesto. Si lo supera, la respuesta se
  descarta. Si un proveedor no reporta consumo, el presupuesto de tokens no se puede comprobar;
  el límite de tiempo sí se aplica siempre.
- En ambos casos la llamada termina en forma controlada, cuenta como una iteración consumida y el
  padre recibe una respuesta estructurada con `finalStatus: LIMIT_REACHED` y `limitReached`
  (`tokens` o `time`). La fase queda `BLOCKED`, con un hallazgo `HIGH` `agent.limit.reached`.
- No hay reintento automático de una llamada cortada por un límite: sería gastar de nuevo el mismo
  presupuesto. La salida completa del proveedor, la solicitud y el registro de la llamada
  (`agent-call-record`) quedan como evidencia para diagnosticar; se consultan con
  `harness agent-contract calls --run RUN --kind TIPO`.

Para continuar, acote la tarea o suba el presupuesto de su tamaño y empiece una ejecución nueva.
