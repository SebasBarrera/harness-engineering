"""Access log of ``harness api serve`` without query strings (#174).

uvicorn writes the request target with its query string to the access log. The API accepts the
token only in the ``Authorization`` header, but a client that sends ``?token=...`` by mistake
would still get it written in clear, so the query string is dropped before any handler formats
the record: the line keeps the client, method, path, HTTP version and status."""

from __future__ import annotations

import copy
import logging
from typing import Any

ACCESS_LOGGER = "uvicorn.access"


class StripQueryFilter(logging.Filter):
    """Remove the query string from the request target of a uvicorn access record."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def log_config() -> dict[str, Any]:
    """uvicorn's default logging configuration with the query filter on the access logger."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["strip_query"] = {"()": StripQueryFilter}
    access = config["loggers"].setdefault(ACCESS_LOGGER, {})
    access["filters"] = [*access.get("filters", []), "strip_query"]
    return config
