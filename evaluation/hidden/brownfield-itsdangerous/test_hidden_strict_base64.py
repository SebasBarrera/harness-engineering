"""Hidden acceptance tests for the brownfield task (never shown to the implementer)."""

import os

import pytest
from itsdangerous import BadData, BadSignature, Signer, URLSafeSerializer
from itsdangerous.encoding import base64_decode, base64_encode


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [("YWJj", b"abc"), ("YWI", b"ab"), ("YQ", b"a"), ("YWI=", b"ab"), ("YQ==", b"a"), ("", b"")],
)
def test_valid_url_safe_input_still_decodes(encoded, expected):
    assert base64_decode(encoded) == expected
    assert base64_decode(encoded.encode("ascii")) == expected


def test_round_trip_for_every_length():
    for length in range(64):
        data = os.urandom(length)
        assert base64_decode(base64_encode(data)) == data


def test_url_safe_alphabet_characters_are_accepted():
    assert base64_decode("-_-_") == b"\xfb\xff\xbf"


@pytest.mark.parametrize(
    "encoded",
    ["YW+j", "YW/j", "YW j", "YWJj\n", " YWJj", "YW!j", "YW.j", "Y=Jj", "YW\tj"],
)
def test_characters_outside_the_url_safe_alphabet_are_rejected(encoded):
    with pytest.raises(BadData):
        base64_decode(encoded)
    with pytest.raises(BadData):
        base64_decode(encoded.encode("ascii"))


@pytest.mark.parametrize("encoded", ["YWJjé", b"YW\xffj", b"\x00YWJj"])
def test_non_ascii_and_control_bytes_are_rejected(encoded):
    with pytest.raises(BadData):
        base64_decode(encoded)


def test_impossible_length_is_rejected():
    with pytest.raises(BadData):
        base64_decode("YWJjZ")


def test_signature_with_inserted_character_is_rejected():
    signer = Signer("secret-key")
    token = signer.sign(b"value").decode("ascii")
    value, signature = token.rsplit(".", 1)
    tampered = f"{value}.{signature[:5]}+{signature[5:]}"
    with pytest.raises(BadSignature):
        signer.unsign(tampered)


def test_serialized_token_with_inserted_character_is_rejected():
    serializer = URLSafeSerializer("secret-key")
    token = serializer.dumps({"user": 1})
    payload, signature = token.rsplit(".", 1)
    with pytest.raises(BadData):
        serializer.loads(f"{payload}.{signature[:4]}/{signature[4:]}")


def test_untampered_tokens_still_load():
    serializer = URLSafeSerializer("secret-key")
    assert serializer.loads(serializer.dumps({"user": 1})) == {"user": 1}
