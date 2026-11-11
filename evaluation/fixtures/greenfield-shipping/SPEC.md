# Biblioteca de costos de envío: especificación

Implementa un paquete de Python `shipping` (en `src/shipping/`) que expone una función:

```python
def shipping_cost(weight_kg, zone, subtotal, *, express=False, coupon=None) -> Decimal: ...
```

Todos los valores de dinero son `decimal.Decimal`. Las reglas se aplican en el orden de abajo.

## 1. Entradas y validación

- `weight_kg` y `subtotal` aceptan `int`, `float`, `str` o `Decimal`. Un `float` se convierte a
  través de su representación en texto (`Decimal(str(value))`), nunca por conversión binaria.
- `weight_kg` debe ser mayor que 0 y como máximo 30. Si no, lanza `ValueError`.
- `subtotal` debe ser mayor o igual que 0. Si no, lanza `ValueError`.
- `zone` debe ser exactamente uno de `"local"`, `"national"` o `"international"` (en minúsculas).
  Cualquier otro valor lanza `ValueError`.

## 2. Costo base por peso

| Zona | Hasta 1 kg (inclusive) | Hasta 5 kg (inclusive) | Más de 5 kg |
|---|---:|---:|---|
| local | 5.00 | 8.00 | 8.00 + 1.50 por kg empezado por encima de 5 |
| national | 9.00 | 14.00 | 14.00 + 2.50 por kg empezado por encima de 5 |
| international | 25.00 | 40.00 | 40.00 + 6.00 por kg empezado por encima de 5 |

«Por kg empezado» (per started kg) significa que cualquier fracción cuenta como un kilogramo completo:
5.01 kg suma un kilogramo y 7.2 kg suma tres.

## 3. Envío express

- `express=True` suma un recargo del 50 % del costo base.
- El envío express no está disponible para `"international"`: lanza `ValueError`.

## 4. Envío gratis

- Para `"local"` y `"national"`, cuando `subtotal` es al menos 100.00 y `express` es `False`, el
  costo es 0.00.
- El envío gratis nunca aplica a los envíos express ni a `"international"`.

## 5. Cupones

- `coupon` es opcional. Los códigos no distinguen mayúsculas de minúsculas y se ignoran los espacios
  en blanco de los extremos.
- `ENVIO10`: 10 % de descuento sobre el costo calculado hasta ese punto.
- `FLAT5`: 5.00 de descuento sobre el costo calculado hasta ese punto; el resultado nunca queda por
  debajo de 0.00.
- Un cupón no tiene efecto cuando el costo ya es 0.00.
- Cualquier otro código no vacío lanza `ValueError`. `None` o una cadena vacía significa que no hay
  cupón.

## 6. Redondeo

- Calcula con precisión completa y redondea una sola vez, al final, a dos decimales con
  `ROUND_HALF_UP`. El `Decimal` devuelto siempre tiene exactamente dos decimales.
