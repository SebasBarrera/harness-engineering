# Inventory library

A Python package `inventory` (in `src/inventory/`) with a class `Inventory` to manage a small
stock of items. Prices are `decimal.Decimal` (the API also accepts `int` and `str`); quantities are
`int`.

## Part A: items

- `add_item(sku, name, price, quantity=0)` adds an item.
- `get_item(sku)` returns a dict with the keys `sku`, `name`, `price` and `quantity`.
- `remove_item(sku)` removes an item.
- `list_items()` returns the items as such dicts.

## Part B: stock movements

- `receive(sku, quantity)` and `ship(sku, quantity)` change the stock.
- `history(sku)` returns the movements as tuples `(kind, quantity)` with kind `"receive"` or `"ship"`.

## Part C: persistence

- `save(path)` and the class method `Inventory.load(path)` store and restore the inventory as JSON.

## Part D: reports

- `low_stock(threshold)` returns SKUs with low stock; `total_value()` returns the stock value.
