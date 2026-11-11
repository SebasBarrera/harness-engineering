"""``terminate_process_group`` when the liveness probe answers EPERM (macOS, zombie group)."""

from __future__ import annotations

import os
import signal
import sys

import pytest

from governed_harness.runtime import lease


def test_eperm_after_sigterm_counts_as_terminated(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def killpg(pgid: int, signum: int) -> None:
        calls.append(signum)
        if signum == 0:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "getpgid", lambda pid: 1, raising=False)
    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    monkeypatch.setattr(signal, "SIGTERM", 15, raising=False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)

    assert lease.terminate_process_group(4242, grace_seconds=5.0) is True
    assert calls == [15, 0, 9]
