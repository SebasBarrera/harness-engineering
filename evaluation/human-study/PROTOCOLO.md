# Protocolo del estudio con personas (T7) y de la lectura ciega de diffs (T3)

Estado: listo para usar, **pendiente de preregistro**. Este protocolo no se aplica a nadie hasta
que la sección correspondiente de `evaluation/campaign/PREREGISTRO.md` (la escribe el plan A0)
esté versionada en un commit cuyo SHA se anota en cada hoja de registro. Ver la sección 8.

Archivos del estudio (todos en esta carpeta):

| Archivo | Para qué |
|---|---|
| `PROTOCOLO.md` | Este documento: objetivo, participantes, materiales, procedimiento, medidas y análisis. |
| `CONSENTIMIENTO.md` | Plantilla del consentimiento informado. |
| `GUION.md` | Guion literal del facilitador y lista de lo que no se dice. |
| `SUS.md` | Cuestionario SUS en español (10 ítems, escala estándar de 1 a 5) y su cálculo. |
| `RUBRICA.md` | Rúbrica de 5 ítems para la lectura ciega de pares de diffs. |
| `PLANTILLA-REGISTRO.csv` | Hoja de registro: solo encabezados, sin datos. |

## 1. Objetivo y preguntas

El harness pide a una persona que decida sobre un ChangeSet a partir del *decision brief* que
imprime `harness review`: qué se pidió, qué cambió, los riesgos con archivo y línea, qué se
verificó sobre qué digest y qué no, excepciones, reintentos y correcciones. La tesis sostiene que
ese brief basta para decidir con evidencia. Este estudio lo pone a prueba con personas reales, a
pequeña escala y de forma descriptiva.

Preguntas:

- **P1 (detección).** Con el decision brief, ¿una persona detecta un defecto sembrado en el
  cambio y decide en consecuencia (no aprueba)?
- **P2 (decisión con evidencia).** ¿La justificación de cada decisión cita evidencia que está en
  el brief (un hallazgo, una prueba, un archivo, una línea, algo no verificado)?
- **P3 (tiempo).** ¿Cuánto tarda una persona en decidir sobre un brief?
- **P4 (usabilidad).** ¿Qué tan usable le parece el brief (puntaje SUS) y con cuánta confianza
  decide (1 a 5)?
- **P5 (lectura ciega, T3).** Sin saber qué condición produjo cada diff, ¿cómo califican dos
  personas los diffs de `direct` y de `harness` en corrección aparente, claridad, pruebas, alcance
  y riesgo?

Lo que este estudio **no** puede decir: que el harness mejore la revisión humana en general, que
el puntaje SUS represente a una población, ni comparar con otra herramienta. Con 3 a 5 personas
el resultado es un estudio de casos; se reporta como tal (sección 7).

## 2. Participantes

### 2.1 Número

Entre 3 y 5 personas para la parte A (briefs). Dos de ellas (u otras dos que cumplan los mismos
criterios) hacen además la parte B (lectura ciega). Antes de la primera sesión válida se hace
**una sesión piloto** con una persona que no cuenta en el análisis; sus datos se descartan y se
declara qué se cambió del protocolo por ella (si se cambió algo, se enmienda el preregistro antes
de seguir).

### 2.2 Criterios de inclusión

- Mayor de edad.
- Al menos un año de experiencia programando en Python (profesional o académica).
- Ha revisado cambios de otras personas (pull requests, merge requests o equivalente) al menos
  algunas veces.
- Lee inglés técnico con soltura: el decision brief y los diffs están en inglés.
- Acepta el consentimiento informado.

### 2.3 Criterios de exclusión

- Haber contribuido al Governed Agent Harness o haber leído su código, su documentación o la
  tesis.
- Haber visto resultados de la campaña de evaluación.
- Tener una relación de dependencia con el autor de la tesis que dificulte negarse (por ejemplo,
  ser estudiante a su cargo). Se declara cualquier relación que exista igualmente.

### 2.4 Reclutamiento

Invitación directa por mensaje a personas de la red profesional o académica del autor que cumplan
los criterios, sin explicar la hipótesis. Texto sugerido:

> Estoy haciendo un estudio corto (unos 30 minutos) para mi tesis de maestría sobre cómo se
> revisan cambios de código. Te mostraría tres resúmenes de cambios y te pediría que decidas qué
> harías con cada uno, y al final un cuestionario de 10 preguntas. No se evalúa a la persona sino
> los materiales. Es voluntario, anónimo y puedes retirarte cuando quieras. ¿Te interesaría?

Se anota cuántas personas se invitó, cuántas aceptaron y cuántas se excluyeron y por qué (solo el
recuento, sin nombres). No hay pago; si se diera alguna compensación, se declara en el
preregistro y es igual para todos.

### 2.5 Consentimiento informado

Antes de empezar, cada persona lee y firma (o acepta por escrito) el consentimiento de
`CONSENTIMIENTO.md`, adaptado a los requisitos del comité de ética o de la institución que
corresponda. Si la institución exige aprobación de un comité, el estudio no empieza sin ella.

### 2.6 Anonimización y resguardo

- Cada persona recibe un código `P1` … `P5` en orden de sesión (el piloto es `P0`). El código es
  lo único que se escribe en las hojas de registro.
- **Nunca** entran al repositorio: nombres, correos, empresa, cargo, edad exacta, la tabla que une
  código y persona, los consentimientos firmados, notas a mano ni grabaciones.
- No se graba audio ni video. Se toman notas escritas y se cronometra.
- La tabla código → persona y los consentimientos firmados se guardan fuera del repositorio, en un
  almacenamiento privado del autor (papel bajo llave o archivo cifrado), y se destruyen al
  terminar el proceso de la tesis o en el plazo que fije la institución.
- En el repositorio solo puede quedar la hoja de registro con códigos, una vez revisada. Las
  justificaciones se transcriben resumidas y se revisan antes de versionarlas para quitar
  cualquier dato que identifique a la persona o a su empleador (por ejemplo «en mi empresa X
  hacemos…» se escribe «en su trabajo hacen…»).
- Los datos demográficos, si se recogen, son solo por rangos (años de experiencia: 1–3, 4–7, 8 o
  más) y se reportan agregados.
- Si una persona se retira, sus datos se borran y se reporta solo que hubo un retiro.

## 3. Materiales

### 3.1 Tres decision briefs reales (parte A)

Los tres briefs son la salida textual de `harness review` sobre corridas `harness` de la campaña:

```bash
harness review --path <workspace-de-la-corrida> --run <run-id> --diff --no-json > brief-original.txt
```

(`<workspace-de-la-corrida>` es el directorio `ws` de la corrida dentro de `--work`; ver
`evaluation/run_eval.py`). No se escribe ni se edita texto del brief salvo lo que indica la
sección 3.2.

Selección (la regla exacta, las corridas elegidas y sus digests se fijan en el preregistro):

- **Dos briefs sin defecto conocido:** corridas `harness` que llegaron a DECISION con el gate en
  verde y que pasan todas sus pruebas ocultas.
- **Un brief con defecto sembrado**, con este orden de preferencia:
  1. una corrida de las tentaciones (T2, `evaluation/temptations/`) en la que el detector
     determinista confirma que el defecto llegó a DECISION *sin* hallazgo: el defecto solo se ve
     en el diff (caso difícil y realista);
  2. si no hay ninguna, una corrida de `evaluation/fault_probes.py` (proveedor determinista que
     aplica la solución de referencia más un defecto conocido, por ejemplo `out-of-scope` o
     `regression`) que llega a DECISION: el brief muestra el hallazgo (caso fácil).
  El preregistro dice cuál de los dos se usó, porque cambia lo que significa detectar.
- Los tres briefs deben ser de tamaño parecido (líneas del diff dentro de ±50 % del mediano) y, si
  es posible, de escenarios distintos para que no se comparen entre sí línea a línea.
- Para cada brief se define **antes** de la primera sesión el criterio de detección: qué tiene que
  nombrar la persona para contar «defecto detectado» (por ejemplo, «menciona que se modificó un
  archivo fuera del alcance pedido» o «menciona la prueba debilitada en `tests/...`»). Ese texto
  queda en el preregistro.

### 3.2 Cómo preparar los briefs sin revelar la condición

El brief no debe delatar cuál tiene el defecto, que viene de una campaña, ni que la decisión ya se
tomó. Para cada uno de los tres se aplica exactamente la misma preparación:

1. Guardar `brief-original.txt` fuera del repositorio, junto con el run id y el ChangeSet digest.
2. Copiarlo a `brief-<letra>.txt` (`A`, `B`, `C`, asignadas al azar con una semilla declarada en
   el preregistro; la letra no dice nada del contenido).
3. En la copia, reemplazar:
   - el run id por `run_<letra>`;
   - rutas locales absolutas por rutas relativas al repositorio;
   - el comando del proveedor y su nombre (`fault_provider`, `fake_claude`, el modelo) por
     `<agente>`;
   - el actor de decisiones simuladas (`human.reviewer-simulated` u otro) por `<persona>`.
4. Quitar las secciones que muestran una decisión ya tomada (historial de decisiones, «qué cambió
   desde la última decisión») y la sección de comandos siguientes, **en los tres briefs por
   igual**, aunque alguno no las tenga.
5. Buscar en la copia y confirmar que no aparece ninguna de estas palabras (sin distinguir
   mayúsculas): `fault`, `probe`, `seed`, `sembr`, `temptation`, `tentaci`, `direct`, `baseline`,
   `campaign`, `campaña`, `eval`, `simulated`, `pilot`. Si aparece una en el código del diff y
   es parte real del cambio, se deja y se anota.
6. Una segunda persona (no participante) revisa los tres archivos y confirma que no puede saber
   cuál es el defectuoso por la forma (largo, secciones, marcas). Si puede, se ajusta y se repite.
7. Anotar en una tabla privada qué se quitó y qué se reemplazó en cada brief. Esa tabla (sin datos
   de personas) se publica junto con los resultados.

Los tres briefs se muestran en pantalla como archivos de texto en el mismo editor, con la misma
fuente, sin resaltado del diff ni herramientas para ejecutar código. La clave (qué letra tiene el
defecto) no está en la máquina de la sesión.

### 3.3 Diez pares de diffs sin etiqueta (parte B, lectura ciega de T3)

- **Selección:** 10 pares `direct`/`harness` de la misma celda (escenario, prompt, modelo,
  repetición), elegidos con una semilla declarada en el preregistro entre las celdas en que las dos
  corridas son válidas. Las corridas rechazadas o inválidas no entran.
- **Diff:** el de los archivos del proyecto contra la base, como lo calcula
  `evaluation/analysis/compare_implementations.py` (`worktree_diff`), sin el directorio `.harness/`
  ni otros archivos de registro del harness. Las pruebas que cada condición agregó **sí** se
  incluyen, porque son parte del cambio que se juzga.
- **Ciego:** en cada par, cuál condición va como «Cambio 1» y cuál como «Cambio 2» se sortea con la
  semilla; se quitan mensajes de commit, trailers y nombres de rama; cada par lleva solo el
  enunciado de la tarea (el mismo para los dos lados) y los dos diffs.
- **Riesgo de desciego:** la forma de las pruebas que agrega el harness puede delatar la condición.
  No se edita el contenido para ocultarlo; en cambio se mide: al final de cada par la persona
  adivina qué lado es «el que usó más controles» y con qué confianza (sección 5.2).

## 4. Procedimiento

### 4.1 Parte A: decision briefs (unos 30 minutos por persona)

Sesión individual, presencial o por videollamada con pantalla compartida por el facilitador. El
guion literal está en `GUION.md`.

| Paso | Qué pasa | Tiempo |
|---|---|---|
| 1 | Bienvenida, propósito general (sin hipótesis), consentimiento y código asignado | 4 min |
| 2 | Orientación: qué es un decision brief y qué decisiones existen (APPROVE, REQUEST_CHANGES, REJECT; APPROVE_EXCEPTION se explica como «aprobar con una excepción justificada») | 3 min |
| 3 | Brief 1: lectura y decisión (tope 7 min) | ≤ 7 min |
| 4 | Justificación oral del brief 1 y confianza 1 a 5 | 1 min |
| 5 | Brief 2: igual que 3 y 4 | ≤ 8 min |
| 6 | Brief 3: igual que 3 y 4 | ≤ 8 min |
| 7 | Pregunta posterior por brief («¿hubo algo en este cambio que te preocupara?») | 2 min |
| 8 | Cuestionario SUS (`SUS.md`) | 3 min |
| 9 | Cierre: agradecer, pedir que no comente el estudio con otros posibles participantes | 1 min |

Total previsto: 30 a 37 minutos.

Orden de los briefs (contrabalanceo fijo, para que el brief con defecto no caiga siempre en la
misma posición):

| Participante | Orden |
|---|---|
| P1 | A, B, C |
| P2 | B, C, A |
| P3 | C, A, B |
| P4 | A, C, B |
| P5 | C, B, A |

Cronometraje: el tiempo de un brief empieza cuando el archivo aparece en pantalla y termina cuando
la persona dice su decisión. Si llega al tope de 7 minutos, el facilitador pide la decisión con la
frase del guion y se marca `tope_alcanzado = si`.

### 4.2 Parte B: lectura ciega (unos 45 minutos, dos personas)

Sesión aparte (puede ser otro día). Si una lectora también hizo la parte A, la parte B va
después, para que la mención de «controles» de sus instrucciones no influya en la parte A. Cada
persona recibe los 10 pares en un orden aleatorio propio
(semilla declarada) y, para cada par, califica los dos cambios con la rúbrica de `RUBRICA.md`,
indica cuál prefiere y adivina la condición. Las dos personas trabajan por separado y no hablan
del material entre sí hasta terminar las dos. Tiempo previsto: unos 4 minutos por par más 5 de
instrucciones.

### 4.3 Facilitador

El facilitador es el autor de la tesis. Es una fuente de sesgo y se declara. Para reducirlo:
guion literal, lista de lo que no se dice (`GUION.md`), ninguna reacción a las respuestas, y la
clave de qué brief tiene el defecto fuera de la sesión hasta que se codifica la detección.

## 5. Medidas

Todas se anotan en `PLANTILLA-REGISTRO.csv` (una fila por brief, por respuesta SUS o por par).

### 5.1 Parte A

| Medida | Cómo se registra |
|---|---|
| Tiempo por brief | Segundos, de la aparición del brief a la decisión dicha (`tiempo_s`). |
| Decisión | `APPROVE`, `REQUEST_CHANGES`, `REJECT` o `APPROVE_EXCEPTION` (`decision`). |
| Justificación | Resumen fiel de lo que dijo, revisado para quitar datos identificables (`justificacion`). |
| Evidencia citada | `si` si la justificación nombra algo concreto del brief: un hallazgo, una prueba, un archivo o línea, algo no verificado; `no` si no (`cita_evidencia`). |
| Defecto detectado (primaria) | `si` si la justificación espontánea cumple el criterio de detección preregistrado; `no` si no (`defecto_detectado_espontaneo`). Solo para el brief con defecto; en los otros, `na`. |
| Defecto detectado tras pregunta | Igual, pero con la respuesta a la pregunta posterior del paso 7 (`defecto_detectado_tras_pregunta`). Es secundaria: la pregunta ya sugiere que puede haber algo. |
| Confianza | 1 (nada segura) a 5 (totalmente segura) en su decisión (`confianza_1a5`). |
| SUS | Respuesta 1 a 5 a cada uno de los 10 ítems (`sus_item`, `sus_respuesta_1a5`). |

La detección se codifica **dos veces**: el facilitador y una segunda persona, cada uno con el
criterio preregistrado y sin conocer la codificación del otro. Los desacuerdos se resuelven
discutiendo y se reportan cuántos hubo.

### 5.2 Parte B

Por par y por persona lectora: los 5 ítems de la rúbrica (1 a 5) para cada uno de los dos cambios,
la preferencia (`1`, `2` o `igual`), la adivinanza de condición (`1` o `2` es «el que usó más
controles») y la confianza en la adivinanza (1 a 5). La condición real de cada lado
(`condicion_oculta`) se completa **después** de que las dos personas terminaron.

### 5.3 Columnas de la hoja de registro

`PLANTILLA-REGISTRO.csv` tiene solo encabezados. Cada fila es una de tres clases (`parte`):
`BRIEF` (una por brief y persona), `SUS` (una por ítem y persona) o `RUBRICA` (una por lado, par
y lectora). Las columnas que no corresponden a la clase quedan vacías. Valores `si`/`no`/`na`.

| Columna | Contenido |
|---|---|
| `sha_preregistro` | SHA del commit con la sección del preregistro. |
| `codigo_participante` | `P0` (piloto) a `P5`. Nunca un nombre. |
| `parte` | `BRIEF`, `SUS` o `RUBRICA`. |
| `orden` | Posición en la sesión (1 a 3 para briefs, 1 a 10 para pares). |
| `item_id` | Letra del brief (`A`, `B`, `C`) o número de par (`01` a `10`). |
| `condicion_oculta` | Se completa al final: `con-defecto`/`sin-defecto` (briefs) o `direct`/`harness` (lados). |
| `tiempo_s` | Segundos hasta la decisión (briefs). |
| `tope_alcanzado` | `si` si se pidió la decisión al llegar al tope. |
| `decision` | `APPROVE`, `REQUEST_CHANGES`, `REJECT` o `APPROVE_EXCEPTION`. |
| `justificacion` | Resumen fiel, sin datos identificables. |
| `cita_evidencia` | `si`/`no` (sección 5.1). |
| `defecto_detectado_espontaneo` | `si`/`no`/`na` (medida primaria). |
| `defecto_detectado_tras_pregunta` | `si`/`no`/`na`. |
| `confianza_1a5` | Confianza en la decisión. |
| `sus_item`, `sus_respuesta_1a5` | Ítem SUS (1 a 10) y respuesta. |
| `rubrica_lado` | `1` o `2`. |
| `rubrica_correccion` … `rubrica_riesgo` | Los cinco ítems de `RUBRICA.md`, 1 a 5 o `nd`. |
| `preferencia` | `1`, `2` o `igual`. |
| `adivinanza_condicion` | `1` o `2`. |
| `confianza_adivinanza_1a5` | Confianza en la adivinanza. |
| `notas_facilitador` | Cambios de opinión, incidencias, respuesta literal a la pregunta posterior. |

## 6. Análisis

Todo es descriptivo. No se calculan valores p ni intervalos de confianza para generalizar.

### 6.1 Parte A

- **Por persona:** una tabla con, para cada brief, tiempo, decisión, evidencia citada, defecto
  detectado (espontáneo y tras pregunta) y confianza; y su puntaje SUS.
- **Total:**
  - detección del defecto: `k de n` personas (espontánea) y `k de n` (tras pregunta);
  - decisión sobre el brief con defecto: cuántas no aprobaron (`REQUEST_CHANGES` o `REJECT`);
  - decisiones sobre los briefs sin defecto: cuántas aprobaron;
  - evidencia citada: `k de 3n` justificaciones;
  - tiempo por brief: mediana, mínimo y máximo, total y por brief, y cuántas veces se llegó al
    tope;
  - confianza: mediana y rango, separando el brief con defecto de los otros;
  - SUS: puntaje de cada persona, mediana, media, mínimo y máximo.
- **Puntaje SUS** (Brooke, 1996): para los ítems impares (1, 3, 5, 7, 9) se resta 1 a la
  respuesta; para los pares (2, 4, 6, 8, 10) se resta la respuesta a 5; se suman las diez
  contribuciones (0 a 40) y se multiplica por 2,5. El resultado va de 0 a 100. No es un
  porcentaje. Un ítem sin respuesta se registra vacío y el puntaje de esa persona no se calcula (se
  reporta como faltante). El detalle con un ejemplo está en `SUS.md`.

### 6.2 Parte B

- Después de quitar el ciego: por par, por ítem y por lado, la calificación de cada lectora y su
  media; la diferencia `harness − direct` por ítem.
- Recuento por ítem: en cuántos de los 10 pares el lado `harness` tuvo media mayor, menor o igual.
- Acuerdo entre las dos lectoras: proporción de calificaciones idénticas y a ±1 punto, por ítem.
- Preferencias: cuántas veces se prefirió cada condición o «igual».
- Desciego: cuántas de las 20 adivinanzas acertaron la condición y con qué confianza. Si aciertan
  mucho más que la mitad, la lectura no fue ciega en la práctica y se dice así.

### 6.3 Cómo reportarlo en la tesis

- Se reportan **todas** las personas que empezaron, incluidas las que se retiraron o se excluyeron
  después, con el motivo (sin datos personales).
- Se escribe el tamaño en cada cifra: «2 de 4 personas detectaron el defecto», nunca «el 50 %»
  sin el `n`.
- Se dice qué tipo de defecto se sembró (sección 3.1) y si el brief lo mostraba como hallazgo,
  porque de eso depende qué significa detectarlo.
- Se dice que el facilitador fue el autor, que las personas venían de su red y que el brief se
  leyó en inglés.
- El SUS se presenta como la percepción de esas personas sobre ese material, no como la
  usabilidad del harness. Si se menciona una referencia externa (por ejemplo, la media de 68 que
  suele citarse a partir de Sauro, 2011), se cita la fuente y se aclara que con este `n` no se
  compara estadísticamente.
- Toda desviación de este protocolo o del preregistro va a la tabla de desviaciones (Tabla B).
- Formulación sugerida: «En un estudio exploratorio con N desarrolladores, k detectaron el defecto
  sembrado leyendo el decision brief; la mediana de tiempo por brief fue de X s (rango a–b) y el
  puntaje SUS tuvo mediana Y (rango c–d). Por el tamaño de la muestra, estos resultados describen
  a estas personas y no permiten generalizar.»

## 7. Limitaciones conocidas

- `n` de 3 a 5: no hay potencia para inferir nada; es un estudio de casos.
- Un solo brief con defecto y de un solo tipo: la detección no se generaliza a otros defectos.
- La pregunta posterior (paso 7) sugiere que algo puede estar mal; por eso la medida primaria es
  la detección espontánea.
- Las personas saben que es un estudio y pueden revisar con más cuidado que en su trabajo.
- La preparación del brief (sección 3.2) quita secciones reales; el brief que se evalúa no es
  idéntico al que vería una persona en una corrida real antes de decidir.
- En la parte B, la forma de las pruebas puede delatar la condición; se mide en vez de ocultarse.

## 8. Preregistro antes de la primera sesión

Este protocolo **debe preregistrarse antes de la primera sesión válida** (el piloto `P0` puede
hacerse antes, y lo que cambie se registra). La sección la escribe el plan A0 en
`evaluation/campaign/PREREGISTRO.md` y debe fijar como mínimo:

- las preguntas P1 a P5 y la medida primaria (detección espontánea del defecto);
- los run ids y ChangeSet digests de los tres briefs, cuál tiene el defecto, de qué fuente viene
  (T2 o sondas de fallas) y si el brief lo muestra como hallazgo;
- el criterio de detección de cada brief, palabra por palabra;
- la semilla de las letras A/B/C, la de la selección de los 10 pares, la del orden de lados y la
  del orden de pares por lectora;
- el orden de briefs por participante (tabla de la sección 4.1) y los topes de tiempo;
- el análisis de la sección 6, sin agregar análisis después de ver los datos (si se agrega uno,
  se reporta como exploratorio);
- qué datos de la campaña se habían visto al preregistrar.

El SHA del commit que contiene esa sección se escribe en la columna `sha_preregistro` de cada fila
de la hoja de registro. Una sesión cuyo SHA no exista en el historial no cuenta.
