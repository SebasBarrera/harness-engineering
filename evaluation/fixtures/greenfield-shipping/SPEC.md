# Shipping cost library: specification

Implement a Python package `shipping` (in `src/shipping/`) exposing one function:

```python
def shipping_cost(weight_kg, zone, subtotal, *, express=False, coupon=None) -> Decimal: ...
```

All money values are `decimal.Decimal`. Rules are applied in the order below.

## 1. Inputs and validation

- `weight_kg` and `subtotal` accept `int`, `float`, `str` or `Decimal`. A `float` is converted
  through its string representation (`Decimal(str(value))`), never through binary conversion.
- `weight_kg` must be greater than 0 and at most 30. Otherwise raise `ValueError`.
- `subtotal` must be greater than or equal to 0. Otherwise raise `ValueError`.
- `zone` must be exactly one of `"local"`, `"national"` or `"international"` (lowercase).
  Any other value raises `ValueError`.

## 2. Base cost by weight

| Zone | Up to 1 kg (inclusive) | Up to 5 kg (inclusive) | Above 5 kg |
|---|---:|---:|---|
| local | 5.00 | 8.00 | 8.00 + 1.50 per started kg above 5 |
| national | 9.00 | 14.00 | 14.00 + 2.50 per started kg above 5 |
| international | 25.00 | 40.00 | 40.00 + 6.00 per started kg above 5 |

"Per started kg" means that any fraction counts as a full kilogram: 5.01 kg adds one kilogram,
7.2 kg adds three.

## 3. Express delivery

- `express=True` adds a surcharge of 50 % of the base cost.
- Express delivery is not available for `"international"`: raise `ValueError`.

## 4. Free shipping

- For `"local"` and `"national"`, when `subtotal` is at least 100.00 and `express` is `False`, the
  cost is 0.00.
- Free shipping never applies to express deliveries or to `"international"`.

## 5. Coupons

- `coupon` is optional. Codes are case-insensitive and surrounding whitespace is ignored.
- `ENVIO10`: 10 % off the cost computed so far.
- `FLAT5`: 5.00 off the cost computed so far; the result is never below 0.00.
- A coupon has no effect when the cost is already 0.00.
- Any other non-empty code raises `ValueError`. `None` or an empty string means no coupon.

## 6. Rounding

- Compute with full precision and round only once, at the end, to two decimal places with
  `ROUND_HALF_UP`. The returned `Decimal` always has exactly two decimal places.
