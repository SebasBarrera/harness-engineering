"""The review cache (#57): one global entry per review and one entry per reviewer answer.

* The **global key** binds a whole report: mode, diff hash, reviewer definitions hash, runner
  version, skip flags, forced model, provider, fallback provider and options. The same review of
  the same diff in the same mode is not repeated.
* The **per-reviewer key** leaves the mode out, so a manual review and the pre-push hook reuse
  each other's answers: cache version, base, provider, fallback, forced model, reviewer, model,
  slice hash, prompt hash, message hash (which includes the reportable locations and the slice
  diff), definition hash, runner version, MCP configuration hash and options.

Entries live as JSON files under ``.harness/review/cache`` with a time to live and an entry
limit (the oldest go first). A corrupt entry is a miss."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from governed_harness.evidence.hashing import sha256_json

CACHE_VERSION = "1"
CACHE_DIRECTORY = ".harness/review/cache"


def global_key(
    *,
    mode: str,
    diff_hash: str,
    definitions_hash: str,
    runner_version: str,
    skip: tuple[str, ...],
    forced_model: str | None,
    provider: str,
    fallback: str | None,
    options: dict[str, Any],
) -> str:
    return sha256_json(
        {
            "kind": "global",
            "version": CACHE_VERSION,
            "mode": mode,
            "diff": diff_hash,
            "definitions": definitions_hash,
            "runner": runner_version,
            "skip": sorted(skip),
            "forcedModel": forced_model,
            "provider": provider,
            "fallback": fallback,
            "options": options,
        }
    )


def reviewer_key(
    *,
    base: str | None,
    provider: str,
    fallback: str | None,
    forced_model: str | None,
    reviewer: str,
    model: str | None,
    slice_hash: str,
    prompt_hash: str,
    message_hash: str,
    definition_hash: str,
    runner_version: str,
    mcp_hash: str,
    options: dict[str, Any],
) -> str:
    return sha256_json(
        {
            "kind": "reviewer",
            "version": CACHE_VERSION,
            "base": base,
            "provider": provider,
            "fallback": fallback,
            "forcedModel": forced_model,
            "reviewer": reviewer,
            "model": model,
            "slice": slice_hash,
            "prompt": prompt_hash,
            "message": message_hash,
            "definition": definition_hash,
            "runner": runner_version,
            "mcp": mcp_hash,
            "options": options,
        }
    )


class ReviewCache:
    def __init__(
        self,
        directory: Path,
        *,
        ttl_seconds: int,
        max_entries: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock

    def _path(self, key: str) -> Path:
        return self.directory / f"{key.split(':', 1)[-1]}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("key") != key:
            return None
        stored = entry.get("storedAt")
        if not isinstance(stored, int | float) or self.clock() - stored > self.ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        value = entry.get("value")
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = json.dumps(
            {"key": key, "storedAt": self.clock(), "value": value}, sort_keys=True
        ).encode("utf-8")
        descriptor, name = tempfile.mkstemp(prefix=".entry.", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(name, self._path(key))
        except OSError:
            Path(name).unlink(missing_ok=True)
            return
        self.prune()

    def prune(self) -> int:
        """Remove expired entries and the oldest ones beyond the limit; return how many."""
        if not self.directory.is_dir():
            return 0
        now = self.clock()
        entries: list[tuple[float, Path]] = []
        removed = 0
        for path in self.directory.glob("*.json"):
            try:
                stored = json.loads(path.read_text(encoding="utf-8")).get("storedAt")
            except (OSError, ValueError, AttributeError):
                stored = None
            if not isinstance(stored, int | float) or now - stored > self.ttl_seconds:
                path.unlink(missing_ok=True)
                removed += 1
                continue
            entries.append((float(stored), path))
        entries.sort(key=lambda item: (item[0], item[1].name))
        for _stored, path in entries[: max(0, len(entries) - self.max_entries)]:
            path.unlink(missing_ok=True)
            removed += 1
        return removed


__all__ = ["CACHE_DIRECTORY", "CACHE_VERSION", "ReviewCache", "global_key", "reviewer_key"]
