"""Copies of the head of each run's event chain kept outside ``.harness`` (``governance.chainAnchor``).

The event chain detects an edited or reordered event, but not a chain whose last events were
deleted: the shorter chain is still well linked. An anchor records the sequence and digest of the
head after every command that appended events; ``harness verify`` then checks that the chain still
contains that event. Two places are supported:

* ``file``: a JSON file per workspace under the user's data directory
  (``$HARNESS_ANCHOR_DIR``, else ``$XDG_DATA_HOME/governed-harness/anchors``, else the platform's
  application-data directory).
* ``git-note``: a Git note on a blob named after the run, under ``refs/notes/governed-harness`` of
  the workspace repository.

Neither is tamper-proof: a process with the user's permissions can rewrite both. The anchor
raises the cost of a silent truncation from editing one SQLite file to editing two places.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from governed_harness.evidence.hashing import sha256_bytes

AnchorMode = Literal["file", "git-note", "off"]

ANCHOR_DIR_ENV = "HARNESS_ANCHOR_DIR"
NOTES_REF = "refs/notes/governed-harness"
_NOTE_IDENTITY = {
    "GIT_AUTHOR_NAME": "governed-harness",
    "GIT_AUTHOR_EMAIL": "governed-harness@localhost",
    "GIT_COMMITTER_NAME": "governed-harness",
    "GIT_COMMITTER_EMAIL": "governed-harness@localhost",
}


@dataclass(frozen=True)
class ChainAnchor:
    execution_id: str
    sequence: int
    event_digest: str
    recorded_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "executionId": self.execution_id,
            "sequence": self.sequence,
            "eventDigest": self.event_digest,
            "recordedAt": self.recorded_at,
        }


def default_anchor_dir() -> Path:
    configured = os.environ.get(ANCHOR_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "governed-harness" / "anchors"
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "governed-harness" / "anchors"
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home) / "governed-harness" / "anchors"
    return home / ".local" / "share" / "governed-harness" / "anchors"


class AnchorStore:
    """Read and write the chain anchors of one workspace in the configured place."""

    def __init__(
        self, mode: AnchorMode, workspace: Path, project_id: str, directory: Path | None = None
    ) -> None:
        self.mode = mode
        self.workspace = workspace
        self.project_id = project_id
        self.directory = directory

    @property
    def location(self) -> str | None:
        if self.mode == "file":
            return str(self._file())
        if self.mode == "git-note":
            return f"{self.workspace} {NOTES_REF}"
        return None

    def record(self, execution_id: str, sequence: int, event_digest: str) -> ChainAnchor:
        anchor = ChainAnchor(execution_id, sequence, event_digest, datetime.now(UTC).isoformat())
        if self.mode == "file":
            self._write_file(anchor)
        elif self.mode == "git-note":
            self._write_note(anchor)
        return anchor

    def read(self, execution_id: str) -> ChainAnchor | None:
        if self.mode == "file":
            runs = self._read_file().get("runs", {})
            value = runs.get(execution_id) if isinstance(runs, dict) else None
        elif self.mode == "git-note":
            value = self._read_note(execution_id)
        else:
            value = None
        if not isinstance(value, dict):
            return None
        try:
            return ChainAnchor(
                execution_id=execution_id,
                sequence=int(value["sequence"]),
                event_digest=str(value["eventDigest"]),
                recorded_at=str(value.get("recordedAt", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    # ----- file -------------------------------------------------------------------
    def _file(self) -> Path:
        directory = self.directory or default_anchor_dir()
        key = sha256_bytes(str(self.workspace).encode("utf-8")).split(":", 1)[1][:16]
        return directory / f"{self.project_id}-{key}.json"

    def _read_file(self) -> dict[str, object]:
        try:
            value = json.loads(self._file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_file(self, anchor: ChainAnchor) -> None:
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        content = self._read_file()
        runs = content.get("runs")
        runs = dict(runs) if isinstance(runs, dict) else {}
        runs[anchor.execution_id] = anchor.as_dict()
        data = {"workspace": str(self.workspace), "projectId": self.project_id, "runs": runs}
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    # ----- git note ---------------------------------------------------------------
    def _git(self, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", *args],
            cwd=self.workspace,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=30,
            env={**os.environ, **_NOTE_IDENTITY},
        )

    def _subject(self, execution_id: str, write: bool) -> str | None:
        arguments = ["hash-object", "--stdin"] + (["-w"] if write else [])
        result = self._git(
            *arguments, stdin=f"governed-harness chain anchor {execution_id}\n".encode()
        )
        text = result.stdout.decode("utf-8", "replace").strip()
        return text if result.returncode == 0 and text else None

    def _write_note(self, anchor: ChainAnchor) -> None:
        subject = self._subject(anchor.execution_id, write=True)
        if subject is None:
            raise OSError("the workspace is not a Git repository; cannot write a chain anchor note")
        message = json.dumps(anchor.as_dict(), sort_keys=True)
        result = self._git("notes", "--ref", NOTES_REF, "add", "-f", "-m", message, subject)
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", "replace").strip() or "git notes failed")

    def _read_note(self, execution_id: str) -> object:
        subject = self._subject(execution_id, write=False)
        if subject is None:
            return None
        result = self._git("notes", "--ref", NOTES_REF, "show", subject)
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout.decode("utf-8", "replace"))
        except ValueError:
            return None
