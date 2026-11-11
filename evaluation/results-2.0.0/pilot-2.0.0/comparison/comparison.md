# Comparación de implementaciones

Generado por `evaluation/analysis/compare_implementations.py` (determinista, sin llamadas a
modelos). Métricas declaradas en la enmienda del 2026-11-11 de
`evaluation/campaign/PREREGISTRO.md`. «—» indica que la métrica no aplica o no está disponible.
Los prompts pobres y casuales van primero (comparación principal); el prompt completo después
(secundaria). «A favor de» sigue solo la dirección declarada de cada métrica (más es mejor o
menos es mejor); las métricas sin dirección declarada se listan sin lado. Los conteos de
prácticas son lo que el cambio añadió (final menos base) y crecen con el tamaño del cambio.

# Prompts pobres y casuales (comparación principal)

## brownfield · prompt `casual` · `claude-haiku-4-5-20251001` · r1

**Falta la corrida `direct` de este prompt y modelo: no hay par que comparar; se muestran solo las condiciones gobernadas.**

### Resultado, cambio y requisitos

| Métrica | `harness` |
|---|---|
| Pruebas ocultas pasadas | 24 |
| Pruebas ocultas (total) | 24 |
| Criterios con todas sus pruebas ocultas en verde | 5 |
| Criterios dados en el prompt | 0 |
| Archivos cambiados | 34 |
| Archivos fuera del alcance | 29 |
| Líneas añadidas | 1476 |
| Líneas eliminadas | 3 |
| Archivos fuente cambiados | 1 |
| Líneas añadidas en fuente | 70 |
| Archivos de prueba cambiados | 19 |
| Líneas añadidas en pruebas | 1050 |
| Archivos de otro tipo (scripts sueltos, etc.) | 14 |
| Funciones de prueba nuevas (netas) | 74 |
| Casos en archivos de prueba cambiados | 97 |
| Casos que fallan en esos archivos | 2 |
| Cobertura de líneas fuente cambiadas | 0.9714 |
| Ruff: hallazgos nuevos | 62 |
| Ruff: imports o variables sin usar nuevos (F401, F841) | 7 |
| Mypy: errores nuevos | 0 |
| Bandit: hallazgos nuevos (fuente) | 0 |
| Secretos nuevos en código no de prueba (patrones) | 0 |
| Literales de credencial nuevos en pruebas (fixtures) | 1 |
| Nombres públicos añadidos | 0 |
| Nombres públicos eliminados | 0 |
| Firmas públicas cambiadas | 0 |
| Docstrings añadidas | 0 |
| Docstrings modificadas | 0 |
| Changelog actualizado | no |
| Preguntas de aclaración (INTENT) | 11 |
| Respuestas recibidas | 11 |
| Criterios formalizados en la tarea | 4 |
| Requisitos formalizados en la tarea | 16 |
| Criterios con archivo de aceptación | 3 |
| Supuestos registrados (harness) o líneas de supuesto (direct) | 4 |
| Decisiones humanas registradas | 4 |
| Pruebas de aceptación disputadas | 2 |
| Ciclos de corrección | 2 |
| Validadores que bloquearon | 3 |
| Afirmaciones no sustentadas del agente | 3 |
| Fase final | VERIFICATION |
| Estado final | FAILED |
| Llamadas al agente | 10 |
| Turnos del agente | 265 |
| Costo del agente (USD) | 2.805 |

### Buenas y malas prácticas

| Métrica | `harness` |
|---|---|
| Fuente: funciones (nuevas, netas) | 1 |
| Fuente: complejidad media (final) | 4.67 |
| Fuente: complejidad máxima (final) | 20 |
| Fuente: funciones con complejidad > 10 (nuevas) | 1 |
| Fuente: función más larga, líneas (final) | 60 |
| Fuente: funciones de más de 50 líneas (nuevas) | 1 |
| Fuente: ventanas de 6 líneas duplicadas (nuevas) | 0 |
| Fuente: `except:` desnudo (nuevos) | 0 |
| Fuente: `except Exception` amplio (nuevos) | 0 |
| Fuente: errores tragados (nuevos) | 0 |
| Fuente: `raise` en `except` con `from` (nuevos) | 0 |
| Fuente: `raise` en `except` sin `from` (nuevos) | 1 |
| Fuente: cobertura de anotaciones de tipo (final) | 1 |
| Fuente: cobertura de docstrings públicas (final) | 0.4 |
| Fuente: números mágicos (nuevos) | 14 |
| Fuente: sentencias inalcanzables (nuevas) | 0 |
| Fuente: `raise` (validación de entrada, nuevos) | 7 |
| Fuente: `isinstance` (validación de entrada, nuevos) | 1 |
| Fuente: nombres fuera de PEP 8 (nuevos) | 0 |
| Fuente: nombres de una letra (nuevos) | 0 |
| Pruebas: funciones de prueba (nuevas, netas) | 74 |
| Pruebas: aserciones por prueba (final) | 1.95 |
| Pruebas: pruebas sin aserción (nuevas) | 0 |
| Pruebas: pruebas negativas con `raises` (nuevas) | 50 |
| Pruebas: casos parametrizados (nuevos) | 35 |
| Pruebas: literales de borde (vacío, None, negativos, no ASCII; nuevos) | 53 |
| Pruebas: `global` en pruebas (nuevos) | 0 |
| Pruebas: pruebas que llaman a otras pruebas (nuevas) | 0 |
| Pruebas: escrituras a estado mutable compartido (nuevas) | 0 |

### Diferencias

Sin par `direct`.

### Código

Diff de los archivos fuente contra la base (contexto de 1 línea, recortado).

`harness` (primeras 60 de 83 líneas):

```diff
--- a/src/itsdangerous/encoding.py
+++ b/src/itsdangerous/encoding.py
@@ -19,2 +19,64 @@ def want_bytes(
 
+def _validate_base64_string(data: str | bytes) -> None:
+    """Validate that data contains only valid URL-safe base64 characters.
+
+    Raises BadData if invalid characters or misplaced padding are found.
+    Valid characters: A-Z, a-z, 0-9, '-', '_', and '=' (only at end)
+    """
+    if isinstance(data, str):
+        try:
+            data_bytes = data.encode("ascii", errors="strict")
+        except UnicodeEncodeError:
+            raise BadData("Invalid base64-encoded data: non-ASCII character found")
+    else:
+        data_bytes = data
+
+    if not data_bytes:
+        return
+
+    # Find the position of first '=' if any
+    padding_start = data_bytes.find(b"=")
+
+    if padding_start != -1:
+        # Check that all remaining characters are '='
+        for idx in range(padding_start, len(data_bytes)):
+            if data_bytes[idx:idx+1] != b"=":
+                raise BadData("Invalid base64-encoded data: '=' padding found before end of string")
+
+        # Check we don't have more than 2 '=' characters
+        num_padding = len(data_bytes) - padding_start
+        if num_padding > 2:
+            raise BadData("Invalid base64-encoded data: too many '=' padding characters")
+
+    # Validate all characters
+    for byte in data_bytes:
+        char_code = byte
+
+        # Control characters (0-31, 127)
+        if char_code < 32 or char_code == 127:
+            raise BadData("Invalid base64-encoded data: control character found")
+
+        # Non-ASCII bytes
+        if char_code > 127:
+            raise BadData("Invalid base64-encoded data: non-ASCII character found")
+
+        # Stop validation at padding characters (we already checked them)
+        if char_code == 61:  # 61 is ASCII code for '='
+            break
+
+        # Reject standard base64 characters '+' (43) and '/' (47)
+        if char_code == 43 or char_code == 47:
+            raise BadData("Invalid base64-encoded data: invalid character found")
+
+        # Valid URL-safe base64 characters: A-Z (65-90), a-z (97-122), 0-9 (48-57), '-' (45), '_' (95)
+        if not (
+            (65 <= char_code <= 90) or
+            (97 <= char_code <= 122) or
+            (48 <= char_code <= 57) or
```

# Prompt completo (comparación secundaria)

## brownfield · prompt `full` · `claude-haiku-4-5-20251001` · r1

### Resultado, cambio y requisitos

| Métrica | `direct` | `harness-core` | `harness` | `harness-anchored` |
|---|---|---|---|---|
| Pruebas ocultas pasadas | 24 | 24 | 24 | 24 |
| Pruebas ocultas (total) | 24 | 24 | 24 | 24 |
| Criterios con todas sus pruebas ocultas en verde | 5 | 5 | 5 | 5 |
| Criterios dados en el prompt | 3 | 3 | 3 | 3 |
| Archivos cambiados | 2 | 2 | 6 | 8 |
| Archivos fuera del alcance | 0 | 0 | 0 | 0 |
| Líneas añadidas | 120 | 128 | 441 | 495 |
| Líneas eliminadas | 1 | 2 | 1 | 5 |
| Archivos fuente cambiados | 1 | 1 | 1 | 1 |
| Líneas añadidas en fuente | 35 | 22 | 28 | 34 |
| Archivos de prueba cambiados | 1 | 1 | 5 | 7 |
| Líneas añadidas en pruebas | 85 | 106 | 413 | 461 |
| Funciones de prueba nuevas (netas) | 3 | 12 | 43 | 42 |
| Casos en archivos de prueba cambiados | 36 | 20 | 43 | 69 |
| Casos que fallan en esos archivos | 0 | 0 | 0 | 0 |
| Cobertura de líneas fuente cambiadas | 0.9524 | 1 | 0.6842 | 0.8889 |
| Ruff: hallazgos nuevos | 0 | 0 | 6 | 8 |
| Ruff: imports o variables sin usar nuevos (F401, F841) | 0 | 0 | 3 | 4 |
| Mypy: errores nuevos | 0 | 0 | 0 | 0 |
| Bandit: hallazgos nuevos (fuente) | 0 | 0 | 0 | 0 |
| Secretos nuevos en código no de prueba (patrones) | 0 | 0 | 0 | 0 |
| Literales de credencial nuevos en pruebas (fixtures) | 0 | 0 | 9 | 1 |
| Nombres públicos añadidos | 0 | 0 | 0 | 0 |
| Nombres públicos eliminados | 0 | 0 | 0 | 0 |
| Firmas públicas cambiadas | 0 | 0 | 0 | 0 |
| Docstrings añadidas | 0 | 0 | 0 | 0 |
| Docstrings modificadas | 0 | 0 | 0 | 0 |
| Changelog actualizado | no | no | no | no |
| Preguntas de aclaración (INTENT) | — | 0 | 0 | 0 |
| Respuestas recibidas | — | 0 | 0 | 0 |
| Criterios formalizados en la tarea | — | 3 | 3 | 3 |
| Requisitos formalizados en la tarea | — | 2 | 2 | 2 |
| Criterios con archivo de aceptación | — | 0 | 2 | 3 |
| Supuestos registrados (harness) o líneas de supuesto (direct) | 0 | 0 | 2 | 12 |
| Preguntas en el texto final (direct) | 0 | — | — | — |
| Decisiones humanas registradas | — | 0 | 3 | 3 |
| Pruebas de aceptación disputadas | — | 0 | 0 | 0 |
| Ciclos de corrección | — | 0 | 0 | 3 |
| Validadores que bloquearon | — | 0 | 0 | 2 |
| Afirmaciones no sustentadas del agente | — | 0 | 0 | 3 |
| Fase final | — | CLOSURE | CLOSURE | VERIFICATION |
| Estado final | — | PASSED | PASSED | FAILED |
| Llamadas al agente | 1 | 1 | 7 | 10 |
| Turnos del agente | 28 | 21 | 89 | 210 |
| Costo del agente (USD) | 0.1882 | 0.1193 | 0.8384 | 1.583 |

### Buenas y malas prácticas

| Métrica | `direct` | `harness-core` | `harness` | `harness-anchored` |
|---|---|---|---|---|
| Fuente: funciones (nuevas, netas) | 0 | 0 | 0 | 0 |
| Fuente: complejidad media (final) | 3.2 | 2.2 | 3 | 3 |
| Fuente: complejidad máxima (final) | 11 | 6 | 10 | 10 |
| Fuente: funciones con complejidad > 10 (nuevas) | 1 | 0 | 0 | 0 |
| Fuente: función más larga, líneas (final) | 45 | 31 | 38 | 39 |
| Fuente: funciones de más de 50 líneas (nuevas) | 0 | 0 | 0 | 0 |
| Fuente: ventanas de 6 líneas duplicadas (nuevas) | 0 | 0 | 0 | 0 |
| Fuente: `except:` desnudo (nuevos) | 0 | 0 | 0 | 0 |
| Fuente: `except Exception` amplio (nuevos) | 0 | 0 | 0 | 0 |
| Fuente: errores tragados (nuevos) | 0 | 0 | 0 | 0 |
| Fuente: `raise` en `except` con `from` (nuevos) | 1 | 1 | 1 | 1 |
| Fuente: `raise` en `except` sin `from` (nuevos) | 0 | 0 | 0 | 0 |
| Fuente: cobertura de anotaciones de tipo (final) | 1 | 1 | 1 | 1 |
| Fuente: cobertura de docstrings públicas (final) | 0.4 | 0.4 | 0.4 | 0.4 |
| Fuente: números mágicos (nuevos) | 0 | 0 | 0 | 2 |
| Fuente: sentencias inalcanzables (nuevas) | 0 | 0 | 0 | 0 |
| Fuente: `raise` (validación de entrada, nuevos) | 4 | 3 | 5 | 4 |
| Fuente: `isinstance` (validación de entrada, nuevos) | 1 | 0 | 0 | 0 |
| Fuente: nombres fuera de PEP 8 (nuevos) | 0 | 0 | 0 | 0 |
| Fuente: nombres de una letra (nuevos) | 0 | 0 | 0 | 0 |
| Pruebas: funciones de prueba (nuevas, netas) | 3 | 12 | 43 | 42 |
| Pruebas: aserciones por prueba (final) | 1.71 | 2.38 | 1.84 | 1.93 |
| Pruebas: pruebas sin aserción (nuevas) | 0 | 0 | 0 | 0 |
| Pruebas: pruebas negativas con `raises` (nuevas) | 2 | 13 | 31 | 30 |
| Pruebas: casos parametrizados (nuevos) | 27 | 0 | 0 | 20 |
| Pruebas: literales de borde (vacío, None, negativos, no ASCII; nuevos) | 19 | 7 | 13 | 34 |
| Pruebas: `global` en pruebas (nuevos) | 0 | 0 | 0 | 0 |
| Pruebas: pruebas que llaman a otras pruebas (nuevas) | 0 | 0 | 0 | 0 |
| Pruebas: escrituras a estado mutable compartido (nuevas) | 0 | 0 | 0 | 0 |

### Diferencias

**`harness-core` frente a `direct`**: 9 métricas a favor de `harness-core`, 2 a favor de `direct`, 9 distintas sin dirección declarada.

- Líneas añadidas: `harness-core` 128 frente a 120 de `direct` (+8).
- Líneas eliminadas: `harness-core` 2 frente a 1 de `direct` (+1).
- Líneas añadidas en fuente: `harness-core` 22 frente a 35 de `direct` (-13).
- Líneas añadidas en pruebas: `harness-core` 106 frente a 85 de `direct` (+21).
- Funciones de prueba nuevas (netas): `harness-core` 12 frente a 3 de `direct` (+9; a favor de `harness-core`).
- Casos en archivos de prueba cambiados: `harness-core` 20 frente a 36 de `direct` (-16).
- Cobertura de líneas fuente cambiadas: `harness-core` 1 frente a 0.9524 de `direct` (+0.0476; a favor de `harness-core`).
- Turnos del agente: `harness-core` 21 frente a 28 de `direct` (-7).
- Costo del agente (USD): `harness-core` 0.1193 frente a 0.1882 de `direct` (-0.0689; a favor de `harness-core`).
- Fuente: complejidad media (final): `harness-core` 2.2 frente a 3.2 de `direct` (-1; a favor de `harness-core`).
- Fuente: complejidad máxima (final): `harness-core` 6 frente a 11 de `direct` (-5; a favor de `harness-core`).
- Fuente: funciones con complejidad > 10 (nuevas): `harness-core` 0 frente a 1 de `direct` (-1; a favor de `harness-core`).
- Fuente: función más larga, líneas (final): `harness-core` 31 frente a 45 de `direct` (-14; a favor de `harness-core`).
- Fuente: `raise` (validación de entrada, nuevos): `harness-core` 3 frente a 4 de `direct` (-1).
- Fuente: `isinstance` (validación de entrada, nuevos): `harness-core` 0 frente a 1 de `direct` (-1).
- Pruebas: funciones de prueba (nuevas, netas): `harness-core` 12 frente a 3 de `direct` (+9).
- Pruebas: aserciones por prueba (final): `harness-core` 2.38 frente a 1.71 de `direct` (+0.67; a favor de `harness-core`).
- Pruebas: pruebas negativas con `raises` (nuevas): `harness-core` 13 frente a 2 de `direct` (+11; a favor de `harness-core`).
- Pruebas: casos parametrizados (nuevos): `harness-core` 0 frente a 27 de `direct` (-27; a favor de `direct`).
- Pruebas: literales de borde (vacío, None, negativos, no ASCII; nuevos): `harness-core` 7 frente a 19 de `direct` (-12; a favor de `direct`).

**`harness` frente a `direct`**: 7 métricas a favor de `harness`, 6 a favor de `direct`, 13 distintas sin dirección declarada.

- Archivos cambiados: `harness` 6 frente a 2 de `direct` (+4).
- Líneas añadidas: `harness` 441 frente a 120 de `direct` (+321).
- Líneas añadidas en fuente: `harness` 28 frente a 35 de `direct` (-7).
- Archivos de prueba cambiados: `harness` 5 frente a 1 de `direct` (+4).
- Líneas añadidas en pruebas: `harness` 413 frente a 85 de `direct` (+328).
- Funciones de prueba nuevas (netas): `harness` 43 frente a 3 de `direct` (+40; a favor de `harness`).
- Casos en archivos de prueba cambiados: `harness` 43 frente a 36 de `direct` (+7).
- Cobertura de líneas fuente cambiadas: `harness` 0.6842 frente a 0.9524 de `direct` (-0.2682; a favor de `direct`).
- Ruff: hallazgos nuevos: `harness` 6 frente a 0 de `direct` (+6; a favor de `direct`).
- Ruff: imports o variables sin usar nuevos (F401, F841): `harness` 3 frente a 0 de `direct` (+3; a favor de `direct`).
- Literales de credencial nuevos en pruebas (fixtures): `harness` 9 frente a 0 de `direct` (+9).
- Supuestos registrados (harness) o líneas de supuesto (direct): `harness` 2 frente a 0 de `direct` (+2).
- Llamadas al agente: `harness` 7 frente a 1 de `direct` (+6).
- Turnos del agente: `harness` 89 frente a 28 de `direct` (+61).
- Costo del agente (USD): `harness` 0.8384 frente a 0.1882 de `direct` (+0.6502; a favor de `direct`).
- Fuente: complejidad media (final): `harness` 3 frente a 3.2 de `direct` (-0.2; a favor de `harness`).
- Fuente: complejidad máxima (final): `harness` 10 frente a 11 de `direct` (-1; a favor de `harness`).
- Fuente: funciones con complejidad > 10 (nuevas): `harness` 0 frente a 1 de `direct` (-1; a favor de `harness`).
- Fuente: función más larga, líneas (final): `harness` 38 frente a 45 de `direct` (-7; a favor de `harness`).
- Fuente: `raise` (validación de entrada, nuevos): `harness` 5 frente a 4 de `direct` (+1).
- Fuente: `isinstance` (validación de entrada, nuevos): `harness` 0 frente a 1 de `direct` (-1).
- Pruebas: funciones de prueba (nuevas, netas): `harness` 43 frente a 3 de `direct` (+40).
- Pruebas: aserciones por prueba (final): `harness` 1.84 frente a 1.71 de `direct` (+0.13; a favor de `harness`).
- Pruebas: pruebas negativas con `raises` (nuevas): `harness` 31 frente a 2 de `direct` (+29; a favor de `harness`).
- Pruebas: casos parametrizados (nuevos): `harness` 0 frente a 27 de `direct` (-27; a favor de `direct`).
- Pruebas: literales de borde (vacío, None, negativos, no ASCII; nuevos): `harness` 13 frente a 19 de `direct` (-6; a favor de `direct`).

**`harness-anchored` frente a `direct`**: 8 métricas a favor de `harness-anchored`, 6 a favor de `direct`, 13 distintas sin dirección declarada.

- Archivos cambiados: `harness-anchored` 8 frente a 2 de `direct` (+6).
- Líneas añadidas: `harness-anchored` 495 frente a 120 de `direct` (+375).
- Líneas eliminadas: `harness-anchored` 5 frente a 1 de `direct` (+4).
- Líneas añadidas en fuente: `harness-anchored` 34 frente a 35 de `direct` (-1).
- Archivos de prueba cambiados: `harness-anchored` 7 frente a 1 de `direct` (+6).
- Líneas añadidas en pruebas: `harness-anchored` 461 frente a 85 de `direct` (+376).
- Funciones de prueba nuevas (netas): `harness-anchored` 42 frente a 3 de `direct` (+39; a favor de `harness-anchored`).
- Casos en archivos de prueba cambiados: `harness-anchored` 69 frente a 36 de `direct` (+33).
- Cobertura de líneas fuente cambiadas: `harness-anchored` 0.8889 frente a 0.9524 de `direct` (-0.0635; a favor de `direct`).
- Ruff: hallazgos nuevos: `harness-anchored` 8 frente a 0 de `direct` (+8; a favor de `direct`).
- Ruff: imports o variables sin usar nuevos (F401, F841): `harness-anchored` 4 frente a 0 de `direct` (+4; a favor de `direct`).
- Literales de credencial nuevos en pruebas (fixtures): `harness-anchored` 1 frente a 0 de `direct` (+1).
- Supuestos registrados (harness) o líneas de supuesto (direct): `harness-anchored` 12 frente a 0 de `direct` (+12).
- Llamadas al agente: `harness-anchored` 10 frente a 1 de `direct` (+9).
- Turnos del agente: `harness-anchored` 210 frente a 28 de `direct` (+182).
- Costo del agente (USD): `harness-anchored` 1.583 frente a 0.1882 de `direct` (+1.395; a favor de `direct`).
- Fuente: complejidad media (final): `harness-anchored` 3 frente a 3.2 de `direct` (-0.2; a favor de `harness-anchored`).
- Fuente: complejidad máxima (final): `harness-anchored` 10 frente a 11 de `direct` (-1; a favor de `harness-anchored`).
- Fuente: funciones con complejidad > 10 (nuevas): `harness-anchored` 0 frente a 1 de `direct` (-1; a favor de `harness-anchored`).
- Fuente: función más larga, líneas (final): `harness-anchored` 39 frente a 45 de `direct` (-6; a favor de `harness-anchored`).
- Fuente: números mágicos (nuevos): `harness-anchored` 2 frente a 0 de `direct` (+2; a favor de `direct`).
- Fuente: `isinstance` (validación de entrada, nuevos): `harness-anchored` 0 frente a 1 de `direct` (-1).
- Pruebas: funciones de prueba (nuevas, netas): `harness-anchored` 42 frente a 3 de `direct` (+39).
- Pruebas: aserciones por prueba (final): `harness-anchored` 1.93 frente a 1.71 de `direct` (+0.22; a favor de `harness-anchored`).
- Pruebas: pruebas negativas con `raises` (nuevas): `harness-anchored` 30 frente a 2 de `direct` (+28; a favor de `harness-anchored`).
- Pruebas: casos parametrizados (nuevos): `harness-anchored` 20 frente a 27 de `direct` (-7; a favor de `direct`).
- Pruebas: literales de borde (vacío, None, negativos, no ASCII; nuevos): `harness-anchored` 34 frente a 19 de `direct` (+15; a favor de `harness-anchored`).

### Código

Diff de los archivos fuente contra la base (contexto de 1 línea, recortado).

`direct`:

```diff
--- a/src/itsdangerous/encoding.py
+++ b/src/itsdangerous/encoding.py
@@ -31,3 +31,37 @@ def base64_decode(string: str | bytes) -> bytes:
     """
-    string = want_bytes(string, encoding="ascii", errors="ignore")
+    # Convert string to bytes, verifying ASCII encoding
+    if isinstance(string, str):
+        try:
+            string = string.encode("ascii")
+        except UnicodeEncodeError:
+            raise BadData("Invalid base64-encoded data") from None
+
+    # Valid characters for base64url data part: A-Za-z0-9-_
+    valid_alphabet = frozenset(
+        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
+    )
+    padding_char = ord(b"=")
+
+    # Find where padding starts
+    padding_start = len(string)
+    for i, byte in enumerate(string):
+        if byte == padding_char:
+            padding_start = i
+            break
+
+    # Validate data part (before padding)
+    for i in range(padding_start):
+        if string[i] not in valid_alphabet:
+            raise BadData("Invalid base64-encoded data")
+
+    # Validate padding part - must contain only '='
+    padding_count = len(string) - padding_start
+    if padding_count > 2:
+        raise BadData("Invalid base64-encoded data")
+
+    for i in range(padding_start, len(string)):
+        if string[i] != padding_char:
+            raise BadData("Invalid base64-encoded data")
+
+    # Add padding if needed to make length multiple of 4
     string += b"=" * (-len(string) % 4)
```

`harness-core`:

```diff
--- a/src/itsdangerous/encoding.py
+++ b/src/itsdangerous/encoding.py
@@ -31,4 +31,24 @@ def base64_decode(string: str | bytes) -> bytes:
     """
-    string = want_bytes(string, encoding="ascii", errors="ignore")
-    string += b"=" * (-len(string) % 4)
+    try:
+        string = want_bytes(string, encoding="ascii", errors="strict")
+    except UnicodeEncodeError as e:
+        raise BadData("Invalid base64-encoded data") from e
+
+    # Remove padding for validation, we'll add it back
+    padded_string = string
+    string_no_pad = string.rstrip(b"=")
+
+    # Check that padding is valid: at most 2 '=' at the end
+    padding = len(padded_string) - len(string_no_pad)
+    if padding > 2:
+        raise BadData("Invalid base64-encoded data")
+
+    # Check that all non-padding characters are in the URL-safe base64 alphabet
+    valid_alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
+    for char in string_no_pad:
+        if char not in valid_alphabet:
+            raise BadData("Invalid base64-encoded data")
+
+    # Add padding to make length a multiple of 4
+    string = string_no_pad + b"=" * (-len(string_no_pad) % 4)
 
```

`harness`:

```diff
--- a/src/itsdangerous/encoding.py
+++ b/src/itsdangerous/encoding.py
@@ -31,3 +31,30 @@ def base64_decode(string: str | bytes) -> bytes:
     """
-    string = want_bytes(string, encoding="ascii", errors="ignore")
+    try:
+        string = want_bytes(string, encoding="ascii", errors="strict")
+    except UnicodeEncodeError as e:
+        raise BadData("Invalid base64-encoded data") from e
+
+    valid_alphabet = set(
+        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
+    )
+
+    equals_index = string.find(b"=")
+
+    if equals_index >= 0:
+        padding_part = string[equals_index:]
+        if padding_part != b"=" * len(padding_part):
+            raise BadData("Invalid base64-encoded data")
+
+        if len(padding_part) > 2:
+            raise BadData("Invalid base64-encoded data")
+
+        data_part = string[:equals_index]
+        for byte in data_part:
+            if byte not in valid_alphabet:
+                raise BadData("Invalid base64-encoded data")
+    else:
+        for byte in string:
+            if byte not in valid_alphabet:
+                raise BadData("Invalid base64-encoded data")
+
     string += b"=" * (-len(string) % 4)
```

`harness-anchored`:

```diff
--- a/src/itsdangerous/encoding.py
+++ b/src/itsdangerous/encoding.py
@@ -19,2 +19,7 @@ def want_bytes(
 
+_base64_url_alphabet = (
+    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
+)
+
+
 def base64_encode(string: str | bytes) -> bytes:
@@ -31,3 +36,31 @@ def base64_decode(string: str | bytes) -> bytes:
     """
-    string = want_bytes(string, encoding="ascii", errors="ignore")
+    try:
+        string = want_bytes(string, encoding="ascii", errors="strict")
+    except UnicodeEncodeError as e:
+        raise BadData("Invalid base64-encoded data") from e
+
+    # Count trailing '=' padding characters
+    padding_count = 0
+    for byte in reversed(string):
+        if byte == 61:  # ASCII code for '='
+            padding_count += 1
+        else:
+            break
+
+    # Validate padding: at most 2 trailing '=' characters
+    if padding_count > 2:
+        raise BadData("Invalid base64-encoded data")
+
+    # Check that all characters are valid
+    for i, byte in enumerate(string):
+        if i >= len(string) - padding_count:
+            # This is part of the padding (must be '=')
+            if byte != 61:  # ASCII code for '='
+                raise BadData("Invalid base64-encoded data")
+        else:
+            # Non-padding character must be in the alphabet
+            if byte not in _base64_url_alphabet:
+                raise BadData("Invalid base64-encoded data")
+
+    # Add padding to make length multiple of 4
     string += b"=" * (-len(string) % 4)
```

## Directorios omitidos

No tienen la forma de una corrida (sin `ws/` o con un nombre que no se reconoce):

- `brownfield-casual-harness-claude-haiku-4-5-20261111T160625Z`
