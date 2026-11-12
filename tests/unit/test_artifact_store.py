import json
import os
import sys
import threading
from pathlib import Path

import pytest

from governed_harness.evidence import artifact_store
from governed_harness.evidence.artifact_store import LocalArtifactStore
from governed_harness.orchestration.workspace_ops import snapshot_contents
from governed_harness.runtime.snapshots import SnapshotSettings, SnapshotStore


def test_roundtrip_and_deduplication(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.put(b"evidence")
    second = store.put(b"evidence")
    assert first.digest == second.digest
    assert store.get(first) == b"evidence"


def test_redacted_json_artifact_stays_valid_json(tmp_path: Path) -> None:
    # A workspace snapshot stored with put_json carries file texts. Redacting the serialized
    # document matched `password = os.environ.get(\` and dropped the backslash that escaped
    # the next quote, so reading the snapshot back raised JSONDecodeError (a correction round
    # ended in phase.error and quarantine). Since F4 the call is code and stays as written.
    store = LocalArtifactStore(tmp_path)
    source = 'password = os.environ.get("ALERTS_PASSWORD")\ntoken = "abcd-1234"\n'
    ref = store.put_json({"files": {"src/alerts.py": {"text": source}}, "next": "kept"})
    value = json.loads(store.get(ref))
    text = value["files"]["src/alerts.py"]["text"]
    assert value["next"] == "kept"
    assert 'password = os.environ.get("ALERTS_PASSWORD")' in text
    assert "abcd-1234" not in text
    assert ref.redacted
    assert ref.metadata == {"redactionRuleIds": ["generic_secret_assignment"]}


def test_json_artifact_without_secret_is_stored_as_before(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    value = {"b": [1, 2.5, None, True], "a": {"text": "plain ü"}}
    ref = store.put_json(value)
    expected = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
    assert store.get(ref) == expected
    assert not ref.redacted


def test_private_key_split_across_json_strings_is_not_stored(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    begin = "-----BEGIN RSA " + "PRIVATE KEY-----"
    end = "-----END RSA " + "PRIVATE KEY-----"
    ref = store.put_json({"lines": [begin, "MIIBOgIBAAJBAKj34GkxFhD90vcN", end]})
    stored = store.get(ref)
    assert b"MIIBOgIBAAJBAKj34GkxFhD90vcN" not in stored
    json.loads(stored)
    assert ref.redacted


def test_private_key_split_across_strings_in_a_document_redacted_string_by_string(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    begin = "-----BEGIN RSA " + "PRIVATE KEY-----"
    end = "-----END RSA " + "PRIVATE KEY-----"
    value = {
        "a": {"text": 'password = os.environ.get("ALERTS_PASSWORD")\n'},
        "b": [begin, "MIIBOgIBAAJBAKj34GkxFhD90vcN", end],
    }
    ref = store.put_json(value)
    stored = store.get(ref)
    assert b"MIIBOgIBAAJBAKj34GkxFhD90vcN" not in stored
    assert b"os.environ.get(" in stored  # code, not a secret (F4)
    json.loads(stored)
    assert ref.redacted


def test_quoted_secret_inside_a_json_string_is_redacted(tmp_path: Path) -> None:
    # #178: escaped in the JSON text (password = \"...\"), the quoted value was not matched by
    # the text redaction, the document still parsed, and the value was stored in clear.
    store = LocalArtifactStore(tmp_path)
    source = 'def f() -> None:\n    password = "supersecret123"\n\tx = "a\\\\b"\n'
    ref = store.put_json({"patches": [{"content": source, "path": "src/a.py"}], "n": 1})
    stored = store.get(ref)
    assert b"supersecret123" not in stored
    value = json.loads(stored)
    content = value["patches"][0]["content"]
    assert content == 'def f() -> None:\n    password = <REDACTED>\n\tx = "a\\\\b"\n'
    assert value["patches"][0]["path"] == "src/a.py"
    assert value["n"] == 1
    assert ref.redacted
    assert ref.metadata == {"redactionRuleIds": ["generic_secret_assignment"]}
    assert store.describe(ref.uri).redacted


def test_json_document_with_escapes_and_nothing_to_redact_keeps_its_bytes(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    value = {"text": 'line one\nsay "hi"\ta\\b\r\né ☃', "keys": {"token": "ok"}}
    ref = store.put_json(value)
    expected = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
    assert store.get(ref) == expected
    assert json.loads(store.get(ref)) == value
    assert not ref.redacted


def test_a_snapshot_read_back_redacted_never_gives_its_text_back(tmp_path: Path) -> None:
    # #178: a restore or a materialized copy must not write <REDACTED> into a file.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_bytes(b'password = "supersecret123"\n')
    (workspace / "b.py").write_bytes(b"x = 1\n")
    snapshots = SnapshotStore(workspace, LocalArtifactStore(tmp_path / "store"), SnapshotSettings())
    snapshot = snapshots.take(for_storage=True)
    ref = snapshots.store(snapshot, {"kind": "test"})
    assert ref.redacted
    stored = snapshots.load(ref.uri)
    assert stored.snapshot.files["a.py"].text == "password = <REDACTED>\n"
    assert snapshots.content(stored, "a.py") is None
    assert snapshot_contents(stored.snapshot).content("a.py") is None
    assert snapshots.content(stored, "b.py") == b"x = 1\n"
    assert snapshot_contents(snapshot).content("a.py") == b'password = "supersecret123"\n'


def _on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_store.sys, "platform", "win32")
    monkeypatch.setattr(artifact_store.time, "sleep", lambda _seconds: None)


def _leftovers(root: Path) -> list[Path]:
    return [path for path in root.rglob(".*") if path.is_file()]


def _concurrent_writer_first(
    monkeypatch: pytest.MonkeyPatch, before: bytes, error: OSError
) -> list[str]:
    """Simulate a concurrent writer that puts ``before`` at the destination between the
    ``exists`` check and the move, so that the first move fails with ``error``."""
    calls: list[str] = []
    real_rename, real_replace = os.rename, os.replace

    def rename(source: str | Path, destination: str | Path) -> None:
        calls.append("rename")
        if calls == ["rename"]:
            Path(destination).write_bytes(before)
            raise error
        real_rename(source, destination)

    def replace(source: str | Path, destination: str | Path) -> None:
        calls.append("replace")
        real_replace(source, destination)

    _on_windows(monkeypatch)
    monkeypatch.setattr(artifact_store.os, "rename", rename)
    monkeypatch.setattr(artifact_store.os, "replace", replace)
    return calls


@pytest.mark.parametrize(
    "error",
    [FileExistsError(17, "File exists"), PermissionError(13, "Access is denied")],
    ids=["exists", "denied"],
)
def test_windows_concurrent_store_of_identical_blob_is_a_completed_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    store = LocalArtifactStore(tmp_path)
    calls = _concurrent_writer_first(monkeypatch, b"evidence", error)
    reference = store.put(b"evidence")
    assert store.get(reference) == b"evidence"
    # The blob was never moved over: neither a retry nor a replace touched the destination.
    assert calls == ["rename", "rename"]  # the second one is the descriptor's
    assert _leftovers(tmp_path) == []


def test_windows_destination_with_different_bytes_is_overwritten_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalArtifactStore(tmp_path)
    calls = _concurrent_writer_first(monkeypatch, b"tampered", FileExistsError(17, "File exists"))
    reference = store.put(b"evidence")
    assert calls[:2] == ["rename", "replace"]
    assert store.path_for(reference).read_bytes() == b"evidence"
    assert store.get(reference) == b"evidence"
    assert _leftovers(tmp_path) == []


def test_windows_move_that_keeps_failing_raises_and_leaves_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "blob"
    attempts: list[str] = []

    def refuse(source: str | Path, destination: str | Path) -> None:
        attempts.append(Path(destination).name)
        raise PermissionError(13, "Access is denied")

    _on_windows(monkeypatch)
    monkeypatch.setattr(artifact_store.os, "rename", refuse)
    monkeypatch.setattr(artifact_store.os, "replace", refuse)
    with pytest.raises(PermissionError):
        LocalArtifactStore._atomic_write(path, b"evidence")
    assert len(attempts) == artifact_store._WINDOWS_REPLACE_ATTEMPTS
    assert not path.exists()
    assert _leftovers(tmp_path) == []


def test_windows_move_that_keeps_meeting_other_bytes_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "blob"
    path.write_bytes(b"other bytes")

    def refuse(source: str | Path, destination: str | Path) -> None:
        raise PermissionError(13, "Access is denied")

    _on_windows(monkeypatch)
    monkeypatch.setattr(artifact_store.os, "replace", refuse)
    monkeypatch.setattr(artifact_store.os, "rename", refuse)
    (tmp_path / ".tmp").write_bytes(b"evidence")
    with pytest.raises(PermissionError):
        artifact_store._move_into_place_on_windows(tmp_path / ".tmp", path, b"evidence")
    assert path.read_bytes() == b"other bytes"


def test_posix_moves_with_replace_and_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def replace(source: str | Path, destination: str | Path) -> None:
        calls.append("replace")
        raise PermissionError(13, "Permission denied")

    def rename(source: str | Path, destination: str | Path) -> None:
        calls.append("rename")

    monkeypatch.setattr(artifact_store.sys, "platform", "linux")
    monkeypatch.setattr(artifact_store.os, "replace", replace)
    monkeypatch.setattr(artifact_store.os, "rename", rename)
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(PermissionError):
        store.put(b"evidence")
    assert calls == ["replace"]


@pytest.mark.skipif(sys.platform != "win32", reason="the refused move happens on Windows only")
def test_windows_concurrent_stores_of_the_same_blob_all_succeed(tmp_path: Path) -> None:
    rounds, writers = 100, 8
    barrier = threading.Barrier(writers)
    errors: list[Exception] = []
    mismatches: list[int] = []

    def store_every_round() -> None:
        store = LocalArtifactStore(tmp_path)
        for index in range(rounds):
            payload = f"the same evidence from every reviewer, round {index}".encode() * 64
            barrier.wait()  # every writer stores this round's blob at the same moment
            try:
                read_back = store.get(store.put(payload))
            except Exception as error:  # noqa: BLE001 - collected and asserted below
                errors.append(error)
                continue
            if read_back != payload:
                mismatches.append(index)

    threads = [threading.Thread(target=store_every_round) for _ in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert mismatches == []
    assert len(LocalArtifactStore(tmp_path).list()) == rounds


def test_a_path_over_the_windows_limit_fails_with_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A long state directory made run start die with a raw FileNotFoundError from mkstemp.
    from governed_harness.domain.errors import ConfigurationError

    store = LocalArtifactStore(tmp_path / ("s" * max(1, 200 - len(str(tmp_path)))))
    _on_windows(monkeypatch)
    monkeypatch.setattr(artifact_store, "windows_long_paths_enabled", lambda: False)

    def refused(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(artifact_store.tempfile, "mkstemp", refused)
    with pytest.raises(ConfigurationError) as raised:
        store.put(b"evidence")
    message = str(raised.value)
    assert str(store.root) in message
    assert "260" in message
    assert "runtime.stateDir" in message
    assert "LongPathsEnabled" in message
    assert artifact_store.artifact_path_too_long(store.root)
    assert not artifact_store.artifact_path_too_long(tmp_path)
