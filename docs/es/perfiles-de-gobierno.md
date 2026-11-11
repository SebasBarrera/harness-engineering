# Perfiles de gobierno por riesgo

No todas las tareas necesitan el mismo gobierno. Corregir una línea no debería costar lo mismo que
una función nueva de autenticación. La clave `governance.riskProfile` elige, tarea por tarea, entre
dos perfiles: el **liviano** para tareas pequeñas y sin riesgo, y el **completo** para las demás.
`harness init` la escribe con el valor `auto`. La guía en inglés con todos los detalles está en
[low friction for small changes](../guides/low-friction.md#governance-profiles-by-risk).

```yaml
governance:
  riskProfile: auto        # auto | light | full
```

## Qué perfil recibe cada tarea

La decisión se toma en INTENT, con el tamaño que calcula el enrutador (requisitos, archivos
propios y sus líneas) y las señales de riesgo de la tarea.

| Valor | Perfil liviano | Perfil completo |
|---|---|---|
| `auto` | tarea de tamaño `S` sin señal de riesgo | todas las demás |
| `light` | toda tarea sin señal de riesgo, sea del tamaño que sea | tarea con señal de riesgo |
| `full` | nunca | todas (no hay carril rápido aunque `friction.fastLane` esté activo) |
| sin la clave | el carril rápido de `friction.fastLane`, como en 1.x | el resto |

Una **señal de riesgo** es `security` (un término de seguridad en la intención, las restricciones
o los requisitos, o un archivo propio que coincide con `agentRouting.riskPaths`) o `interface` (la
tarea declara una interfaz). Una tarea que pide verificación por encima de `L1` (sondas, lista de
chequeo, criterio diferido, manual o verificado por sonda) siempre recibe el perfil completo.

## Qué hace el perfil liviano

Es el carril rápido (`friction.fastLane`, con su `skip` y su `verification` si la sección
existe; si no, los valores por defecto) más dos pasos opcionales que no se hacen:

| Paso | En el perfil liviano |
|---|---|
| Revisión de ambigüedad del agente, descomposición, preflight, mutación ligera | No se hacen (o lo que diga `fastLane.skip`). |
| Revisión del agente o panel de revisión | Solo si hay una señal: un factor de riesgo, un hallazgo de severidad MEDIUM o mayor de este cambio, o un ChangeSet más grande que `S`. |
| `locate` | No se llama al agente que ubica el cambio. |
| Aprobación del plan | No hay punto de aprobación con `planApproval: risk`; con `always` sí se pregunta a una persona. |

Lo que el perfil liviano **conserva siempre**:

- las nueve fases normativas, en su orden;
- la verificación propia del harness con todos los validadores obligatorios antes de la compuerta;
- las pruebas de aceptación congeladas de la tarea, si las hay;
- la compuerta que falla cerrada;
- la decisión humana ligada al digest del ChangeSet (o una preautorización bajo su condición);
- la cadena de eventos encadenada por hash.

Un cambio defectuoso se bloquea en VERIFICATION igual que en el perfil completo. Si el ChangeSet
muestra un factor de riesgo o crece más allá de `S`, la ejecución sale del perfil liviano
(`lane.escalated`) y la revisión del agente sí corre.

## Qué hace el perfil completo

Todo lo que configura `harness init`: la revisión de ambigüedad del agente, `locate` para tareas
`M` y `L`, la descomposición, la aprobación del plan de tareas grandes o riesgosas, el preflight,
la mutación ligera y la revisión del agente o el panel en cada ejecución.

El evento `lane.classified` y la evidencia `lane-decision` registran `profile` (el valor
configurado) y `governance` (`light` o `full`) cuando la clave existe; sin ella no cambian.

## La señal de seguridad, leída en contexto

Sin la clave, la señal `security` es la lista de 1.x: basta que aparezca `password`, `secret`,
`token`, `authentic`, `authoriz`, `credential`, `card`, `payment` o `encrypt` en cualquier parte
del texto, así que "discard", "tokenize" o "product card" vuelven riesgosa una tarea pequeña. Con
cualquier valor de la clave el texto se lee en contexto:

- un término fuerte marca la tarea por sí solo: contraseñas, credenciales, autenticación y
  autorización, OAuth, JWT, CSRF, MFA, cifrado, claves de API, privadas, secretas o de firma,
  tokens de acceso, de refresco, bearer, de sesión o de restablecimiento, tarjetas de crédito o
  débito, números de tarjeta y CVV, en inglés y en español (contraseña, credencial,
  autenticación, autorización, cifrado, encriptar, tarjeta de crédito);
- `token`, `secret`, `card` y `payment` cuentan solo a tres palabras o menos de una palabra de su
  contexto (`token` junto a `bearer`, `refresh`, `user`, `rotate`...; `card` junto a `credit`,
  `number`, `billing`...) o cuando un archivo propio tiene un componente de ruta sensible
  (`auth`, `security`, `crypto`, `payment`, `session`...).

"Hash the users' passwords", "encrypt the e-mail addresses at rest" y "cifrar los datos personales
del usuario" son tareas de seguridad; "count the tokens of the tokenizer" y "rename the product
card" no lo son. Los factores de riesgo del ChangeSet se leen del diff diga lo que diga la tarea,
así que un cambio que toca código de autenticación igual sale del perfil liviano.
