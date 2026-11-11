import json
from pathlib import Path

from governed_harness.evidence.artifact_store import LocalArtifactStore


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
    # ended in phase.error and quarantine).
    store = LocalArtifactStore(tmp_path)
    source = 'password = os.environ.get("ALERTS_PASSWORD")\ntoken = "abcd-1234"\n'
    ref = store.put_json({"files": {"src/alerts.py": {"text": source}}, "next": "kept"})
    value = json.loads(store.get(ref))
    text = value["files"]["src/alerts.py"]["text"]
    assert value["next"] == "kept"
    assert "os.environ.get(" not in text
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
    assert b"os.environ.get(" not in stored
    json.loads(stored)
    assert ref.redacted
