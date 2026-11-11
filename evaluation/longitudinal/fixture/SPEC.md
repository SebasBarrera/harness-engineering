# Biblioteca de inventario

Un paquete de Python `inventory` (en `src/inventory/`) con una clase `Inventory` para gestionar un
stock pequeño de artículos (items). Los precios son `decimal.Decimal` (la API también acepta `int` y
`str`); las cantidades son `int`.

## Parte A: artículos (items)

- `add_item(sku, name, price, quantity=0)` agrega un artículo.
- `get_item(sku)` devuelve un dict con las claves `sku`, `name`, `price` y `quantity`.
- `remove_item(sku)` elimina un artículo.
- `list_items()` devuelve los artículos como dicts de ese tipo.

## Parte B: movimientos de stock (stock movements)

- `receive(sku, quantity)` y `ship(sku, quantity)` cambian el stock.
- `history(sku)` devuelve los movimientos como tuplas `(kind, quantity)` con kind `"receive"` o `"ship"`.

## Parte C: persistencia

- `save(path)` y el método de clase `Inventory.load(path)` guardan y restauran el inventario como JSON.

## Parte D: reportes

- `low_stock(threshold)` devuelve los SKU con stock bajo; `total_value()` devuelve el valor del stock.
