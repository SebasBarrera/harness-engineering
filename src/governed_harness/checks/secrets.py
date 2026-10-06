"""Context-aware secret detection over the added lines of a ChangeSet diff.

A plain pattern scanner either drowns a reviewer in test fixtures (``password="test"``) or misses
the secret an agent copied from the task statement into a test. This check weighs where the line
lives (test or production path), whether the value is an obvious dummy, and whether the value was
disclosed in the task text: a declared value is critical wherever it appears.

The value itself is never reported: messages show its first two characters and its length."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from governed_harness.domain.enums import FindingSeverity

from .model import DiffFile, Issue, is_test_path

CATEGORY = "security"

# ----- values --------------------------------------------------------------------------------
_DUMMY_WORDS = frozenset(
    {
        "test",
        "testing",
        "dummy",
        "example",
        "changeme",
        "secret",
        "password",
        "pass",
        "pwd",
        "fake",
        "placeholder",
        "sample",
        "foo",
        "bar",
        "baz",
        "xxx",
        "redacted",
        "none",
        "null",
    }
)
_DUMMY_PREFIX = re.compile(r"(?:test|dummy|fake|example|sample|mock)[\w.-]*")
_PLACEHOLDER = re.compile(
    r"\$\{[^}]*\}|\{\{[^}]*\}\}|\{[A-Za-z_][\w.]*(?:![rsa])?(?::[^{}]*)?\}|\{\}|%(?:\([^)]*\))?[sdr]"
)
_MIN_SECRET_LENGTH = 6


def _literal(tag: str = "") -> str:
    """A quoted string literal pattern; ``tag`` prefixes its group names so the pattern can be
    used several times in one expression."""
    return (
        rf"(?P<{tag}prefix>[rRbBuUfF]{{0,2}})(?P<{tag}quote>[\"'`])"
        rf"(?P<{tag}value>(?:\\.|(?!(?P={tag}quote)).)*)(?P={tag}quote)"
    )


_LITERAL = _literal()
_LITERAL_RE = re.compile(_LITERAL)


def is_dummy_value(value: str) -> bool:
    """Whether a string literal is an obvious placeholder rather than a usable secret: a dummy
    word, a ``test…``/``fake…`` style value, a mask (``xxx``, ``***``), a template (``<token>``,
    ``${VAR}``, ``{name}``) or a value too short to be a credential."""
    stripped = value.strip()
    lowered = stripped.lower()
    return (
        lowered in _DUMMY_WORDS
        or _DUMMY_PREFIX.fullmatch(lowered) is not None
        or re.fullmatch(r"x+", lowered) is not None
        or re.fullmatch(r"\*+", stripped) is not None
        or re.fullmatch(r"<[^<>]*>", stripped) is not None
        or _PLACEHOLDER.search(stripped) is not None
        or len(stripped) < _MIN_SECRET_LENGTH
    )


def _is_template(value: str, prefix: str) -> bool:
    """A value built at run time (f-string, ``${VAR}``, ``<token>``) holds no secret itself."""
    stripped = value.strip()
    return (
        ("f" in prefix.lower() and "{" in stripped)
        or _PLACEHOLDER.search(stripped) is not None
        or re.fullmatch(r"<[^<>]*>", stripped) is not None
    )


def _mask(value: str) -> str:
    return f"'{value[:2]}…' ({len(value)} chars)"


# ----- declared values -----------------------------------------------------------------------
_SECRET_WORDING = re.compile(
    r"pass(?:word|wd|phrase)?|pwd|secret|token|api[ _-]?key|\bkey\b|credential", re.IGNORECASE
)
_QUOTED_IN_TEXT = re.compile(r"\"([^\"\n]+)\"|'([^'\n]+)'|`([^`\n]+)`|“([^”\n]+)”")
_WORDED_IN_TEXT = re.compile(
    r"(?:password|passwd|passphrase|pwd|secret|token|api[ _-]?key|key|credential)s?\b"
    r"(?:\s+(?:is|=|:)|\s*[:=])\s*(?P<value>[^\s\"'`“”,;]+)",
    re.IGNORECASE,
)
_CONTEXT_CHARS = 80


def _declared_values(task_text: str) -> set[str]:
    """Values the task statement discloses as secrets.

    A quoted or backticked string counts only when secret wording precedes it nearby: a task
    quotes endpoints and identifiers too, and those must not become critical findings. An
    unquoted value after the wording counts unless it is a plain lowercase word ("the token is
    stored ...")."""
    values: set[str] = set()
    for match in _QUOTED_IN_TEXT.finditer(task_text):
        value = next(group for group in match.groups() if group is not None).strip()
        context = task_text[max(0, match.start() - _CONTEXT_CHARS) : match.start()]
        context = context.rsplit("\n", 1)[-1]
        if len(value) >= _MIN_SECRET_LENGTH and _SECRET_WORDING.search(context):
            values.add(value)
    for match in _WORDED_IN_TEXT.finditer(task_text):
        value = match.group("value").rstrip(".)]")
        if len(value) >= _MIN_SECRET_LENGTH and not (value.isalpha() and value.islower()):
            values.add(value)
    return values


# ----- line patterns -------------------------------------------------------------------------
_CREDENTIAL_FORMAT = re.compile(
    r"\bgh[oprsu]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b"
    r"|\bsk-[A-Za-z0-9]{20,}|\bxox[baprs]-[A-Za-z0-9-]+"
    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
_SECRET_NAME = re.compile(
    r"password|passwd|pwd|secret|token|api_key|apikey|credential|private_key", re.IGNORECASE
)
_NOT_A_SECRET_NAME = re.compile(
    r"(?:^|_)(?:url|uri|name|field|type|env|var|path|file|header|prefix|label|message|msg"
    r"|endpoint|length|len|policy|pattern|regex|format)s?$|tokeniz",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?<![\w.\"'])(?P<name>[A-Za-z_][\w]*)\s*(?::\s*[\w\[\], |.]+?)?\s*=(?!=)\s*" + _LITERAL
)
_KEYED = re.compile(r"(?P<q>[\"']?)(?P<name>[A-Za-z_][\w-]*)(?P=q)\s*:\s*" + _LITERAL)
_AUTH_CALL = re.compile(
    r"(?<![\w])(?P<function>[\w.]*?(?:login|register|authenticate|set_password|hash_password"
    r"|verify_password|create_user|signup|sign_up)\w*)\s*\(",
    re.IGNORECASE,
)
_ENVIRONMENT = re.compile(
    r"os\.environ\[\s*[\"'](?P<k1>[^\"']+)[\"']\s*\]\s*=(?!=)\s*"
    + _literal("a")
    + r"|os\.environ\.setdefault\(\s*[\"'](?P<k2>[^\"']+)[\"']\s*,\s*"
    + _literal("b")
    + r"|\b(?:os\.putenv|\w+\.setenv)\(\s*[\"'](?P<k3>[^\"']+)[\"']\s*,\s*"
    + _literal("c")
    + r"|process\.env(?:\.(?P<k4>\w+)|\[\s*[\"'](?P<k5>[^\"']+)[\"']\s*\])\s*=(?!=)\s*"
    + _literal("d")
)
_ENVIRONMENT_KEY = re.compile(r"PASS|SECRET|TOKEN|KEY|PWD|CREDENTIAL")
_CONSTANT = re.compile(
    r"^\s*(?:export\s+)?(?:(?:const|let|var|final)\s+)?(?P<name>[A-Z][A-Z0-9_]*)"
    r"\s*(?::\s*[\w\[\], |.]+?)?\s*=(?!=)\s*" + _LITERAL
)
_CONSTANT_WORD = re.compile(r"PASSWORD|PASSWD|PASSPHRASE|SECRET|TOKEN|CREDENTIAL")
_CONSTANT_SEGMENTS = frozenset(
    {"PW", "PWD", "PASS", "KEY", "KEYS", "APIKEY", "PRIVATEKEY", "CRED", "CREDS"}
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.\w+")
_IDENTIFIER_PATH = re.compile(r"[a-z_]\w*(?:\.[a-z_]\w*)+")
_IDENTIFIER = re.compile(r"[a-z]+(?:_[a-z0-9]+)+|[a-z]+(?:[A-Z][a-z0-9]*)+")


_ENVIRONMENT_GROUPS = (("k1", "a"), ("k2", "b"), ("k3", "c"), ("k4", "d"), ("k5", "d"))


def _secret_name(name: str) -> bool:
    return _SECRET_NAME.search(name) is not None and _NOT_A_SECRET_NAME.search(name) is None


def _constant_name(name: str) -> bool:
    if _NOT_A_SECRET_NAME.search(name):
        return False
    return any(
        _CONSTANT_WORD.search(segment) or segment in _CONSTANT_SEGMENTS
        for segment in name.split("_")
    )


def _not_a_value(value: str) -> bool:
    """URLs without user info, absolute paths, e-mail addresses and dotted identifiers are
    configuration, not credentials."""
    return (
        (("://" in value) and "@" not in value)
        or value.startswith("/")
        or _EMAIL.fullmatch(value) is not None
        or _IDENTIFIER_PATH.fullmatch(value) is not None
    )


def _field_reference(value: str) -> bool:
    """An identifier that itself names a secret (``"input_tokens"``, ``"userPassword"``) maps
    one field name to another; it is not a credential."""
    return _IDENTIFIER.fullmatch(value) is not None and _SECRET_NAME.search(value) is not None


def _credential_literals(text: str) -> Iterator[tuple[str, str, str]]:
    """``(name, prefix, value)`` for every credential-named assignment, keyword or key."""
    for pattern in (_ASSIGNMENT, _KEYED):
        for match in pattern.finditer(text):
            if _secret_name(match.group("name")) and not _field_reference(match.group("value")):
                yield match.group("name"), match.group("prefix"), match.group("value")


def _auth_call_literals(text: str) -> Iterator[tuple[str, str, str]]:
    """``(function, prefix, value)`` for positional string literals of an authentication call."""
    if re.match(r"^\s*(?:async\s+)?(?:def|function)\b", text):
        return
    for call in _AUTH_CALL.finditer(text):
        for literal in _LITERAL_RE.finditer(text, call.end()):
            before = text[call.end() : literal.start()].rstrip()
            if before.endswith(("=", ":")):
                continue
            value = literal.group("value")
            if not _not_a_value(value):
                yield call.group("function"), literal.group("prefix"), value


def _environment_literals(text: str) -> Iterator[tuple[str, str, str]]:
    for match in _ENVIRONMENT.finditer(text):
        for key_group, letter in _ENVIRONMENT_GROUPS:
            key = match.group(key_group)
            if key is not None:
                yield key, match.group(f"{letter}prefix"), match.group(f"{letter}value")
                break


class _Collector:
    """Issues keyed by (path, line, rule): one per rule and line, first wins."""

    def __init__(self) -> None:
        self.issues: dict[tuple[str, int, str], Issue] = {}

    def add(
        self,
        rule: str,
        severity: FindingSeverity,
        message: str,
        path: str,
        line: int,
        recommendation: str,
    ) -> None:
        self.issues.setdefault(
            (path, line, rule),
            Issue(
                rule_id=rule,
                severity=severity,
                message=message,
                path=path,
                line=line,
                category=CATEGORY,
                recommendation=recommendation,
            ),
        )


_MOVE_TO_SECRET_STORE = "Read the value from the environment or a secret store; never commit it."  # nosec B105 - remediation text, not a secret
_TEST_RECOMMENDATION = "Use an obvious dummy value or generate the credential in a fixture."


def _graded(
    collector: _Collector,
    rule: str,
    what: str,
    value: str,
    prefix: str,
    path: str,
    line: int,
    *,
    in_tests: bool,
) -> None:
    """Record a credential under the path context: critical in production, medium in tests,
    informational for a dummy value in tests."""
    if not value.strip() or _is_template(value, prefix):
        return
    message = f"{what} with a literal value {_mask(value)}"
    if not in_tests:
        collector.add(rule, FindingSeverity.CRITICAL, message, path, line, _MOVE_TO_SECRET_STORE)
    elif is_dummy_value(value):
        collector.add(
            "secrets.test-dummy",
            FindingSeverity.INFO,
            message + " (dummy test value)",
            path,
            line,
            _TEST_RECOMMENDATION,
        )
    else:
        test_rule = "secrets.test-credential" if rule == "secrets.credential" else rule
        collector.add(test_rule, FindingSeverity.MEDIUM, message, path, line, _TEST_RECOMMENDATION)


def scan_secrets(diff: Sequence[DiffFile], *, task_text: str = "") -> list[Issue]:
    """Secret issues on the added lines of ``diff``, in diff and line order."""
    declared = _declared_values(task_text)
    collector = _Collector()
    for file in diff:
        if file.is_deleted:
            continue
        path = file.path
        in_tests = is_test_path(path)
        for diff_line in file.added:
            text, number = diff_line.text, diff_line.number
            for value in sorted(declared):
                if value in text:
                    collector.add(
                        "secrets.declared-value",
                        FindingSeverity.CRITICAL,
                        f"A value the task discloses as a secret is written into the code "
                        f"{_mask(value)}",
                        path,
                        number,
                        "Never copy a secret from the task; read it from the environment.",
                    )
                    break
            credential_format = _CREDENTIAL_FORMAT.search(text)
            if credential_format:
                collector.add(
                    "secrets.credential-format",
                    FindingSeverity.CRITICAL,
                    f"A known credential format appears {_mask(credential_format.group(0))}",
                    path,
                    number,
                    "Revoke the credential and remove it from the change.",
                )
            named = False
            for name, prefix, value in _credential_literals(text):
                named = True
                _graded(
                    collector,
                    "secrets.credential",
                    f"{name!r} is assigned",
                    value,
                    prefix,
                    path,
                    number,
                    in_tests=in_tests,
                )
            for function, prefix, value in _auth_call_literals(text):
                named = True
                _graded(
                    collector,
                    "secrets.credential",
                    f"{function}() receives a positional string",
                    value,
                    prefix,
                    path,
                    number,
                    in_tests=in_tests,
                )
            for key, prefix, value in _environment_literals(text):
                if _ENVIRONMENT_KEY.search(key.upper()):
                    named = True
                    _graded(
                        collector,
                        "secrets.environment-assignment",
                        f"environment variable {key!r} is set",
                        value,
                        prefix,
                        path,
                        number,
                        in_tests=in_tests,
                    )
            constant = None if named else _CONSTANT.match(text)
            if (
                constant
                and _constant_name(constant.group("name"))
                and constant.group("value").strip()
                and not is_dummy_value(constant.group("value"))
                and not _is_template(constant.group("value"), constant.group("prefix"))
                and not _not_a_value(constant.group("value"))
            ):
                collector.add(
                    "secrets.constant",
                    FindingSeverity.MEDIUM if in_tests else FindingSeverity.CRITICAL,
                    f"constant {constant.group('name')!r} holds a literal "
                    f"{_mask(constant.group('value'))}",
                    path,
                    number,
                    _TEST_RECOMMENDATION if in_tests else _MOVE_TO_SECRET_STORE,
                )
    return list(collector.issues.values())
