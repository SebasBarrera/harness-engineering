# Revisión de código a ciegas de las implementaciones de rides

Instrucciones que recibe quien revisa cada grupo de celdas (el mismo nivel de instrucción con y sin el
harness). Las implementaciones se copian sin sus metadatos de ejecución y se nombran A, B, C en un orden
aleatorio; a quien revisa no se le dice qué condición produjo cada una. `prepare_review.py` arma la carpeta
de revisión y guarda la clave aparte.

## Qué leer

- `SPEC.md` (el alcance del producto; los niveles one-line y paragraph no lo recibieron, así que júzgalos
  contra él como el producto que se pidió, y di qué falta).
- `INTERFACE.md` (la API pública que cada implementación tenía que seguir).
- Para cada implementación: `src/` y `tests/`. No ejecutes los agentes; puedes ejecutar sus pruebas y
  scripts pequeños contra ellas.

## Rúbrica (puntaje de 1 a 5 en cada uno, con evidencia como archivo:línea)

1. Arquitectura: capas y fronteras (fachada de API, dominio, servicios, persistencia), dirección de las
   dependencias, separación de responsabilidades.
2. Modularidad y cohesión: tamaño de los módulos, responsabilidad única, clases dios o archivos dios.
3. Modelo de dominio: entidades explícitas, máquinas de estados para viajes y pedidos, invariantes
   aplicadas en un solo lugar.
4. Persistencia: diseño del esquema, transacciones, qué se guarda, cómo se restaura el estado.
5. Dinero y tiempo: Decimal con redondeo half up donde se exige, nada de dinero en float, uso del reloj
   virtual, temporizadores.
6. Errores y validación: tipos de excepción según la especificación, validación centralizada o dispersa,
   precedencia de errores.
7. Seguridad: hash de contraseñas (algoritmo, sal, iteraciones), datos de tarjeta (no se guarda el número
   ni el CVC), bloqueo de cuentas.
8. Diseño para el rendimiento: índices o estructuras espaciales para el despacho, complejidad de las rutas
   críticas.
9. Pruebas: qué cubren, casos límite, independencia, legibilidad; pruebas que no verifican nada.
10. Mantenibilidad: nombres, duplicación, comentarios, anotaciones de tipo, consistencia.
11. Cobertura funcional: qué secciones de la especificación están implementadas, parciales o ausentes.

## Salida

Un archivo JSON con, por implementación, los once puntajes y una justificación de una oración para cada
uno, la lista de secciones de la especificación ausentes o parciales, y una lista de las diferencias más
importantes entre las implementaciones (cada una con evidencia). Luego un resumen corto en Markdown: qué
implementación es más fuerte en qué, y por qué, sin adivinar la condición.
