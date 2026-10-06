"""Exchange-rate client (adapter layer)."""

import json
import urllib.request
from decimal import Decimal

TIMEOUT_SECONDS = 5


def fetch_rate(url: str) -> Decimal:
    """Read ``{"rate": "..."}`` from ``url`` within the timeout."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return Decimal(json.load(response)["rate"])
