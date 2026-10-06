# billing

Pricing rules, an exchange-rate adapter and an invoice service.

```python
from decimal import Decimal
from billing.service import invoice_total

invoice_total(Decimal("10.00"), Decimal("0.15"), Decimal("0.19"))  # Decimal("10.12")
```
