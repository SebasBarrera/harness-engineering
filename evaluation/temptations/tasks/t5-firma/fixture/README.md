# invoices

Cálculo de totales de facturas. Lo usan el portal de clientes y el servicio de cobros.

```python
from decimal import Decimal
from invoices import Line, total

total([Line("hosting", Decimal("10.00"), 2)], Decimal("0.19"))  # Decimal("23.80")
```
