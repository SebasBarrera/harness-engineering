from __future__ import annotations

import re
from uuid import uuid4

_PREFIX = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def new_id(prefix: str) -> str:
    if not _PREFIX.fullmatch(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{uuid4().hex}"
