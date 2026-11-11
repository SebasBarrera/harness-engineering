"""The ``security`` risk flag of a task, read from its text before any change.

Two detectors exist. ``legacy_security_words`` is the substring list of 1.x: any of its words
anywhere in the intent, the constraints or the requirements flags the task, so "discard" (card),
"tokenize" (token) or "the encrypted backup is out of scope" all count. It stays the behaviour of
a project without ``governance.riskProfile``.

``security_terms`` is the contextual detector a project with ``governance.riskProfile`` uses:

* a **strong** term flags the task on its own: passwords and passphrases, credentials,
  authentication and authorisation, OAuth, JWT, CSRF, MFA, encryption and decryption, API,
  private, secret and signing keys, access, refresh, bearer, session, auth and reset tokens,
  credit, debit and payment cards, card numbers, CVV and CVC, and their Spanish equivalents
  (contraseña, credencial, autenticación, autorización, cifrado, encriptar, tarjeta de crédito);
* a **weak** term (``token``, ``secret``, ``card``, ``payment``) flags the task only next to a
  word of its own context (within three words), or when one of the files the task owns has a
  sensitive path component (``auth``, ``security``, ``crypto``, ``payment``...).

So "count the tokens of the tokenizer" or "rename the product card" stay low risk, while "store
the API secret in the vault" or "mask the card number" do not. The ChangeSet's own risk factors
(``checks.risk``) are read later from the diff whatever the task says.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Final

LEGACY_SECURITY_WORDS: Final[tuple[str, ...]] = (
    "password",
    "secret",
    "token",
    "authentic",
    "authoriz",
    "credential",
    "card",
    "payment",
    "encrypt",
)
"""The 1.x substring list (``orchestration/agent_results.py``), kept as it was."""

_STRONG = re.compile(
    r"""\b(?:
        pass(?:word|phrase)s?|passwd|
        credentials?|
        authenticat\w*|authoriz\w*|authoris\w*|authn|authz|
        oauth\w*|jwts?|csrf|mfa|2fa|two[\s-]factor|
        (?:en|de)crypt\w*|
        (?:api|private|secret|signing)[\s_-]?keys?|
        (?:access|refresh|bearer|session|auth|reset)[\s_-]?tokens?|
        (?:credit|debit|payment)[\s_-]?cards?|card[\s_-]?numbers?|cvv|cvc|
        contrase(?:ñ|n)as?|credencial(?:es)?|autentica\w*|autoriza\w*|
        (?:des)?cifrad[oa]s?|(?:des)?cifrar|(?:des)?encript\w*|
        tarjetas?\s+de\s+(?:cr[eé]dito|d[eé]bito)
    )\b""",
    re.VERBOSE | re.IGNORECASE,
)

_WEAK: Final[dict[str, re.Pattern[str]]] = {
    "token": re.compile(
        r"auth\w*|log(?:in|on)s?|sessions?|bearer|apis?|access|refresh\w*|csrf|oauth\w*|jwts?|"
        r"secrets?|passwords?|credentials?|users?|sign(?:in|ed|ing)?|revok\w*|rotat\w*|"
        r"expir\w*|leak\w*|headers?|cookies?|usuarios?|sesi[oó]n"
    ),
    "secret": re.compile(
        r"apis?|keys?|tokens?|passwords?|credentials?|env|environment|vaults?|leak\w*|"
        r"expos\w*|rotat\w*|clients?|stor\w*|manager|claves?"
    ),
    "card": re.compile(
        r"credit|debit|payments?|pay|number|numbers|cvv|cvc|pan|expir\w*|billing|holders?|"
        r"banks?|banking|cr[eé]dito|d[eé]bito|pagos?"
    ),
    "payment": re.compile(
        r"cards?|charg\w*|refund\w*|gateways?|checkout|billing|process\w*|transactions?|"
        r"banks?|credit|tarjetas?"
    ),
}
_WEAK_FORMS: Final[dict[str, str]] = {
    "token": "token",
    "tokens": "token",
    "secret": "secret",
    "secrets": "secret",
    "card": "card",
    "cards": "card",
    "tarjeta": "card",
    "tarjetas": "card",
    "payment": "payment",
    "payments": "payment",
    "pago": "payment",
    "pagos": "payment",
}
_WINDOW = 3
_WORD = re.compile(r"[a-z0-9ñáéíóúü]+")
SENSITIVE_PATH_PARTS: Final[frozenset[str]] = frozenset(
    {
        "auth",
        "authn",
        "authz",
        "authentication",
        "authorization",
        "security",
        "crypto",
        "cryptography",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "password",
        "passwords",
        "payment",
        "payments",
        "billing",
        "session",
        "sessions",
        "login",
        "oauth",
        "jwt",
    }
)
"""Path components that make a weak term count: ``src/auth/tokens.py`` is sensitive,
``src/tokenizer.py`` is not."""


def legacy_security_words(text: str) -> bool:
    """The 1.x rule: any word of :data:`LEGACY_SECURITY_WORDS` anywhere in ``text``."""
    lowered = text.lower()
    return any(word in lowered for word in LEGACY_SECURITY_WORDS)


def sensitive_path(path: str) -> bool:
    """Whether a component of ``path`` (split on ``/``, ``.``, ``_`` and ``-``) is sensitive."""
    parts = re.split(r"[/\\._-]+", PurePosixPath(path.replace("\\", "/")).as_posix().lower())
    return any(part in SENSITIVE_PATH_PARTS for part in parts)


def security_terms(text: str, paths: Sequence[str] = ()) -> tuple[str, ...]:
    """The terms of ``text`` that make the task a security task, empty when there are none.

    A strong term is returned as written (``password``); a weak one with the word of context
    that made it count (``token~bearer``) or with the sensitive path (``secret@src/auth.py``).
    """
    found: list[str] = [match.group(0).lower() for match in _STRONG.finditer(text)]
    words = _WORD.findall(text.lower())
    sensitive = next((path for path in paths if sensitive_path(path)), None)
    for index, word in enumerate(words):
        weak = _WEAK_FORMS.get(word)
        if weak is None:
            continue
        context = _WEAK[weak]
        near = words[max(0, index - _WINDOW) : index] + words[index + 1 : index + 1 + _WINDOW]
        neighbour = next((item for item in near if context.fullmatch(item)), None)
        if neighbour is not None:
            found.append(f"{weak}~{neighbour}")
        elif sensitive is not None:
            found.append(f"{weak}@{sensitive}")
    return tuple(dict.fromkeys(found))
