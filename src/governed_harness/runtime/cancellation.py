from __future__ import annotations

import threading
from collections.abc import Callable


class CancellationToken:
    def __init__(self, checker: Callable[[], bool] | None = None) -> None:
        self._event = threading.Event()
        self._checker = checker

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or bool(self._checker and self._checker())
