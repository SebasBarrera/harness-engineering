#!/usr/bin/env python3
"""The hidden probe of H10 (``PREREGISTRO.md``): a fixed battery of calls to the public API of
itsdangerous 2.2.0, run in a workspace (``PYTHONPATH=src``) and printed as JSON. No model.

A mutant whose output differs from the baseline's changes the behaviour of the library (it is a
regression, not an equivalent mutant). The battery was written before the selection of the H10
cases and is not changed after it. The clock is fixed (``time.time`` returns ``NOW`` plus the
offset of the step), so the timed signers are deterministic.

Usage: python h10_probe.py  (from the workspace root, with src on PYTHONPATH)
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import Any

NOW = 1_700_000_000.0
_offset = [0.0]
time.time = lambda: NOW + _offset[0]  # noqa: E731 - the fixed clock of the probe

import itsdangerous  # noqa: E402
from itsdangerous import encoding  # noqa: E402

KEYS = (b"secret-key", "another key", [b"old-key", b"new-key"])
VALUES = (b"hello", b"", b"a.b.c", "unicode-é", b"x" * 300)
DERIVATIONS = ("concat", "django-concat", "hmac", "none")
OBJECTS = ({"id": 5, "name": "item"}, [1, 2, 3], "text", 0, None, {"long": "z" * 500})
results: list[Any] = []


def show(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes": value.decode("latin-1")}
    if isinstance(value, tuple):
        return [show(item) for item in value]
    if hasattr(value, "isoformat"):
        return {"datetime": value.isoformat()}
    return repr(value)


def step(name: str, call: Callable[[], Any]) -> None:
    try:
        results.append([name, "ok", show(call())])
    except Exception as error:  # noqa: BLE001 - the exception is the observed behaviour
        detail: dict[str, Any] = {"type": type(error).__name__, "message": str(error)}
        for attribute in ("payload", "date_signed", "header", "original_error"):
            if hasattr(error, attribute):
                detail[attribute] = show(getattr(error, attribute))
        results.append([name, "error", detail])


def signers() -> None:
    for key_index, key in enumerate(KEYS):
        for derivation in DERIVATIONS:
            signer = itsdangerous.Signer(key, salt="probe", key_derivation=derivation)
            for value_index, value in enumerate(VALUES):
                tag = f"signer/{key_index}/{derivation}/{value_index}"
                step(f"{tag}/sign", lambda s=signer, v=value: s.sign(v))
                signed = signer.sign(value)
                step(f"{tag}/unsign", lambda s=signer, v=signed: s.unsign(v))
                step(f"{tag}/validate", lambda s=signer, v=signed: s.validate(v))
                step(f"{tag}/tampered", lambda s=signer, v=signed: s.unsign(v[:-1] + b"A"))
                step(f"{tag}/no-sep", lambda s=signer, v=value: s.unsign(encoding.want_bytes(v)))
    signer = itsdangerous.Signer(b"k", sep=":", salt=b"other")
    step("signer/sep", lambda: signer.sign(b"v"))
    step("signer/sep-unsign", lambda: signer.unsign(b"v:" + signer.get_signature(b"v")))
    step("signer/bad-sep", lambda: itsdangerous.Signer(b"k", sep="-"))
    step("signer/derive", lambda: itsdangerous.Signer(b"k").derive_key())
    step(
        "signer/unknown-derivation",
        lambda: itsdangerous.Signer(b"k", key_derivation="x").sign(b"v"),
    )
    step(
        "signer/none-algorithm",
        lambda: itsdangerous.Signer(b"k", algorithm=itsdangerous.NoneAlgorithm()).sign(b"v"),
    )
    step(
        "signer/fallback",
        lambda: itsdangerous.Signer(
            b"k", fallback_signers=[{"digest_method": __import__("hashlib").sha512}]
        ).unsign(itsdangerous.Signer(b"k", digest_method=__import__("hashlib").sha512).sign(b"v")),
    )
    step("signer/verify", lambda: itsdangerous.Signer(b"k").verify_signature(b"v", b"!!"))
    step("signer/empty-key", lambda: itsdangerous.Signer([]).sign(b"v"))


def timed() -> None:
    signer = itsdangerous.TimestampSigner(b"secret-key", salt="probe")
    for index, value in enumerate(VALUES):
        _offset[0] = 0.0
        signed = signer.sign(value)
        step(f"timed/{index}/sign", lambda v=signed: v)
        for age, offset in ((10, 5.0), (10, 10.0), (10, 11.0), (None, 10_000.0), (0, 1.0)):
            _offset[0] = offset
            step(
                f"timed/{index}/unsign/{age}/{offset}",
                lambda v=signed, a=age: signer.unsign(v, max_age=a, return_timestamp=True),
            )
            step(
                f"timed/{index}/validate/{age}/{offset}",
                lambda v=signed, a=age: signer.validate(v, a),
            )
        _offset[0] = -100.0
        step(f"timed/{index}/future", lambda v=signed: signer.unsign(v, max_age=10))
    _offset[0] = 0.0
    step(
        "timed/missing-timestamp",
        lambda: signer.unsign(itsdangerous.Signer(b"secret-key", salt="probe").sign(b"v")),
    )
    step(
        "timed/bad-timestamp",
        lambda: signer.unsign(itsdangerous.Signer(b"secret-key", salt="probe").sign(b"v.!!!")),
    )
    step("timed/timestamp", lambda: signer.get_timestamp())
    step("timed/to-datetime", lambda: signer.timestamp_to_datetime(1_700_000_123))


def serializers() -> None:
    kinds = (
        itsdangerous.Serializer,
        itsdangerous.URLSafeSerializer,
        itsdangerous.TimedSerializer,
        itsdangerous.URLSafeTimedSerializer,
    )
    for kind in kinds:
        for key_index, key in enumerate(KEYS):
            serializer = kind(key, salt="probe")
            for index, value in enumerate(OBJECTS):
                tag = f"{kind.__name__}/{key_index}/{index}"
                _offset[0] = 0.0
                dumped = serializer.dumps(value)
                step(f"{tag}/dumps", lambda d=dumped: d)
                step(f"{tag}/loads", lambda s=serializer, d=dumped: s.loads(d))
                step(f"{tag}/other-salt", lambda s=serializer, d=dumped: s.loads(d, salt="else"))
                step(f"{tag}/unsafe", lambda s=serializer, d=dumped: s.loads_unsafe(d))
                tampered = dumped[:-2] + ("A" if isinstance(dumped, str) else b"A") * 2
                step(f"{tag}/tampered", lambda s=serializer, d=tampered: s.loads(d))
                step(f"{tag}/unsafe-tampered", lambda s=serializer, d=tampered: s.loads_unsafe(d))
                if "Timed" in kind.__name__:
                    _offset[0] = 30.0
                    step(f"{tag}/expired", lambda s=serializer, d=dumped: s.loads(d, max_age=10))
                    step(
                        f"{tag}/fresh",
                        lambda s=serializer, d=dumped: s.loads(
                            d, max_age=60, return_timestamp=True
                        ),
                    )
    url = itsdangerous.URLSafeSerializer(b"k")
    step("urlsafe/bad-payload", lambda: url.loads(url.make_signer().sign(b"!!notbase64").decode()))
    step(
        "urlsafe/bad-zlib",
        lambda: url.loads(url.make_signer().sign(b".eJzLSM3JyQcABiwCFQ").decode()),
    )
    step(
        "urlsafe/bad-json",
        lambda: url.loads(url.make_signer().sign(encoding.base64_encode(b"{")).decode()),
    )
    plain = itsdangerous.Serializer(b"k")
    step("serializer/bad-json", lambda: plain.loads(plain.make_signer().sign(b"{").decode()))
    step("serializer/dump-payload", lambda: plain.dump_payload({"b": 2, "a": 1}))
    step("serializer/load-payload", lambda: plain.load_payload(b'{"a": 1}'))
    step(
        "serializer/fallback",
        lambda: itsdangerous.Serializer(b"k", fallback_signers=[{"key_derivation": "hmac"}]).loads(
            itsdangerous.Serializer(b"k", signer_kwargs={"key_derivation": "hmac"}).dumps("v")
        ),
    )
    step("serializer/text", lambda: itsdangerous.Serializer(b"k").dumps("v"))
    step("serializer/bytes-salt", lambda: itsdangerous.Serializer(b"k", salt=b"s").dumps("v"))
    step("serializer/none-salt", lambda: itsdangerous.Serializer(b"k", salt=None).dumps("v"))


def encodings() -> None:
    for index, value in enumerate((b"", b"a", b"ab", b"abc", b"\xff\xfe\x00", "été", b"x" * 61)):
        step(f"encoding/{index}/encode", lambda v=value: encoding.base64_encode(v))
        step(
            f"encoding/{index}/roundtrip",
            lambda v=value: encoding.base64_decode(encoding.base64_encode(v)),
        )
    for text in ("YQ", "YWI", "YWJj", "!!!", "YQ==", "Y", "/+8", "_-8"):
        step(f"encoding/decode/{text}", lambda t=text: encoding.base64_decode(t))
    for number in (0, 1, 255, 256, 65535, 1_700_000_000, 2**64 - 1):
        step(f"encoding/int/{number}", lambda n=number: encoding.int_to_bytes(n))
        step(
            f"encoding/int-roundtrip/{number}",
            lambda n=number: encoding.bytes_to_int(encoding.int_to_bytes(n)),
        )
    for value in ("text", b"bytes", "é"):
        step(f"encoding/want/{value!r}", lambda v=value: encoding.want_bytes(v))


def exceptions() -> None:
    for kind in (
        itsdangerous.BadData,
        itsdangerous.BadSignature,
        itsdangerous.BadTimeSignature,
        itsdangerous.SignatureExpired,
        itsdangerous.BadHeader,
        itsdangerous.BadPayload,
    ):
        step(f"exc/{kind.__name__}", lambda k=kind: [c.__name__ for c in k.__mro__])
        step(f"exc/{kind.__name__}/str", lambda k=kind: str(k("message")))


for part in (signers, timed, serializers, encodings, exceptions):
    part()
json.dump(results, sys.stdout, sort_keys=True)
sys.stdout.write("\n")
