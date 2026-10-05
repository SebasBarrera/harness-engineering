"""Security patterns an agent introduces without being asked: unsafe deserialization, stored card
data, card numbers in source and weak password hashing.

Each one is a defect a test suite rarely exercises (the code works, it is just unsafe), so it has
to be recognised in the change itself. Diff-based rules read only the added lines, so a change is
not blamed for code it did not write; AST-based rules read the current text of the changed Python
files. Test paths are skipped: fixtures legitimately contain test card numbers and pickles."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping, Sequence

from governed_harness.checks.model import DiffFile, Issue, is_test_path
from governed_harness.domain.enums import FindingSeverity

_CATEGORY = "security"

_PICKLE_PATTERNS = (
    re.compile(r"\bpickle\.(?:loads?|Unpickler)\s*\("),
    re.compile(r"\bcPickle\b"),
)
_OTHER_DESERIALIZATION = (
    re.compile(r"\bmarshal\.loads?\s*\("),
    re.compile(r"\bshelve\.open\s*\("),
    re.compile(r"\bdill\.loads?\s*\("),
    re.compile(r"\bjsonpickle\.decode\s*\("),
    re.compile(r"\byaml\.unsafe_load\s*\("),
)
_YAML_LOAD = re.compile(r"\byaml\.load\s*\(")
_SAFE_LOADER = re.compile(r"\b(?:C?SafeLoader)\b")

# A run of 13-19 digits, optionally grouped by single spaces or dashes. A digit or a decimal
# point right before it means it is part of a longer number or a float, not a card number.
_CARD_DIGITS = re.compile(r"(?<![\d.])\d(?:[ -]?\d){12,18}(?!\d)")

_CARD_NAME = re.compile(
    r"(?:^|_)(?:card_?number|pan|cvc|cvv2?|card_cvc|security_code|card_code)(?:_|$)"
)
_CARD_SAFE = re.compile(r"last_?4|last_?four|masked|token|hash|fingerprint")
_CREATE_TABLE = re.compile(r"create\s+table", re.IGNORECASE)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_HASH_FUNCTIONS = frozenset({"md5", "sha1", "sha224", "sha256", "sha384", "sha512", "new"})
_PASSWORD_NAME = re.compile(r"password|passwd|passphrase|pwd|(?:^|_)pass(?:_|$|\d)")
_MIN_PBKDF2_ITERATIONS = 100_000


def normalize_name(name: str) -> str:
    """A name in snake case, lowercase: ``cardNumber`` and ``card-number`` -> ``card_number``,
    so one pattern covers the spellings of Python, JSON and SQL."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.replace("-", "_").lower()


def is_card_data_name(name: str) -> bool:
    """Whether a name denotes a full card number or a card security code. Names that say the
    value is reduced (last four digits, masked, a token, a hash, a fingerprint) are safe to
    store."""
    normalized = normalize_name(name)
    return bool(_CARD_NAME.search(normalized)) and not _CARD_SAFE.search(normalized)


def luhn_valid(digits: str) -> bool:
    """The Luhn checksum every payment card number satisfies."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _issue(rule_id: str, message: str, path: str, line: int | None, recommendation: str) -> Issue:
    return Issue(
        rule_id=rule_id,
        severity=FindingSeverity.HIGH,
        message=message,
        path=path,
        line=line,
        category=_CATEGORY,
        recommendation=recommendation,
    )


def _has_restricted_unpickler(tree: ast.Module | None) -> bool:
    """Whether the file defines a ``pickle.Unpickler`` subclass that overrides ``find_class``,
    the documented way to restrict what a pickle may construct."""
    if tree is None:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        subclasses_unpickler = any(
            (isinstance(base, ast.Attribute) and base.attr == "Unpickler")
            or (isinstance(base, ast.Name) and base.id == "Unpickler")
            for base in node.bases
        )
        overrides = any(
            isinstance(member, ast.FunctionDef) and member.name == "find_class"
            for member in node.body
        )
        if subclasses_unpickler and overrides:
            return True
    return False


def _deserialization_call(text: str, *, restricted: bool) -> str | None:
    stripped = text.strip()
    if stripped.startswith("#"):
        return None
    if not restricted:
        for pattern in _PICKLE_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0).rstrip("( ")
    for pattern in _OTHER_DESERIALIZATION:
        match = pattern.search(text)
        if match:
            return match.group(0).rstrip("( ")
    if _YAML_LOAD.search(text) and not _SAFE_LOADER.search(text):
        return "yaml.load"
    return None


def _card_numbers(text: str) -> Iterator[str]:
    for match in _CARD_DIGITS.finditer(text):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and luhn_valid(digits):
            yield digits


def _diff_issues(diff: Sequence[DiffFile], trees: Mapping[str, ast.Module | None]) -> list[Issue]:
    issues: list[Issue] = []
    for entry in diff:
        if entry.is_deleted or is_test_path(entry.path):
            continue
        restricted = _has_restricted_unpickler(trees.get(entry.path))
        for added in entry.added:
            call = _deserialization_call(added.text, restricted=restricted)
            if call is not None:
                issues.append(
                    _issue(
                        "security.unsafe-deserialization",
                        f"{call} deserializes untrusted data in a way that can execute code.",
                        entry.path,
                        added.number,
                        "Use JSON (or yaml.safe_load), or a restricted pickle.Unpickler that "
                        "overrides find_class.",
                    )
                )
            for digits in _card_numbers(added.text):
                issues.append(
                    _issue(
                        "security.card-number-literal",
                        f"The added line contains what looks like a card number "
                        f"(a {len(digits)}-digit Luhn-valid sequence ending in {digits[-4:]}).",
                        entry.path,
                        added.number,
                        "Never put card numbers in source; read test cards from fixtures under "
                        "tests/ and real ones only through the payment provider.",
                    )
                )
                break
    return issues


def _stored_names(tree: ast.Module) -> Iterator[tuple[str, int]]:
    """Names under which a value is stored: attribute and subscript targets, class fields,
    module-level names, dict literal keys and keyword arguments. Local variables of a function
    are not storage and are left out."""
    scopes: list[tuple[ast.AST, bool]] = [(tree, True)]
    while scopes:
        node, stores_names = scopes.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                scopes.append((child, False))
                continue
            if isinstance(child, ast.ClassDef):
                scopes.append((child, True))
                continue
            yield from _stores_of(child, stores_names)
            scopes.append((child, stores_names))


def _target_names(target: ast.expr, stores_names: bool) -> Iterator[tuple[str, int]]:
    if isinstance(target, ast.Attribute):
        yield target.attr, target.lineno
    elif isinstance(target, ast.Subscript):
        key = target.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            yield key.value, target.lineno
    elif isinstance(target, ast.Name) and stores_names:
        yield target.id, target.lineno
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _target_names(element, stores_names)


def _stores_of(node: ast.AST, stores_names: bool) -> Iterator[tuple[str, int]]:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _target_names(target, stores_names)
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        yield from _target_names(node.target, stores_names)
    elif isinstance(node, ast.Dict):
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value, key.lineno
    elif isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg is not None:
                yield keyword.arg, keyword.value.lineno
    elif (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _CREATE_TABLE.search(node.value)
    ):
        for identifier in _IDENTIFIER.findall(node.value):
            yield identifier, node.lineno


def _card_data_issues(path: str, tree: ast.Module) -> list[Issue]:
    first: dict[str, tuple[str, int]] = {}
    for name, line in _stored_names(tree):
        if not is_card_data_name(name):
            continue
        key = normalize_name(name)
        if key not in first or line < first[key][1]:
            first[key] = (name, line)
    return [
        _issue(
            "security.card-data-stored",
            f"{name} stores a full card number or security code.",
            path,
            line,
            "Store the provider's token and the last four digits; never store the CVC.",
        )
        for name, line in sorted(first.values(), key=lambda item: (item[1], item[0]))
    ]


def _hashlib_aliases(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """The names bound to the ``hashlib`` module and to functions imported from it."""
    modules: set[str] = set()
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "hashlib":
                    modules.add(alias.asname or "hashlib")
        elif isinstance(node, ast.ImportFrom) and node.module == "hashlib" and not node.level:
            for alias in node.names:
                functions[alias.asname or alias.name] = alias.name
    return modules, functions


def _hash_function(call: ast.Call, modules: set[str], functions: dict[str, str]) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in modules or func.value.id == "hashlib":
            return func.attr
    elif isinstance(func, ast.Name) and func.id in functions:
        return functions[func.id]
    return None


def _mentions_password(call: ast.Call) -> bool:
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        for node in ast.walk(argument):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name is not None and _PASSWORD_NAME.search(normalize_name(name)):
                return True
    return False


def _iterations(call: ast.Call) -> int | None:
    value: ast.expr | None = call.args[3] if len(call.args) > 3 else None
    for keyword in call.keywords:
        if keyword.arg == "iterations":
            value = keyword.value
    if (
        isinstance(value, ast.Constant)
        and isinstance(value.value, int)
        and not isinstance(value.value, bool)
    ):
        return value.value
    return None


def _password_hash_issues(path: str, tree: ast.Module) -> list[Issue]:
    modules, functions = _hashlib_aliases(tree)
    issues: list[Issue] = []
    calls = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
        key=lambda call: (call.lineno, call.col_offset),
    )
    for node in calls:
        name = _hash_function(node, modules, functions)
        if name in _HASH_FUNCTIONS and _mentions_password(node):
            issues.append(
                _issue(
                    "security.weak-password-hash",
                    f"A password is hashed with hashlib.{name}, a fast unsalted digest that is "
                    "cheap to brute-force.",
                    path,
                    node.lineno,
                    "Use hashlib.scrypt, or hashlib.pbkdf2_hmac with a per-user salt and at "
                    "least 100000 iterations.",
                )
            )
        elif name == "pbkdf2_hmac":
            iterations = _iterations(node)
            if iterations is not None and iterations < _MIN_PBKDF2_ITERATIONS:
                issues.append(
                    _issue(
                        "security.weak-password-hash",
                        f"hashlib.pbkdf2_hmac runs {iterations} iterations; at least "
                        f"{_MIN_PBKDF2_ITERATIONS} are needed.",
                        path,
                        node.lineno,
                        "Raise the iteration count to at least 100000 (or use hashlib.scrypt).",
                    )
                )
    return issues


def check_security_patterns(diff: Sequence[DiffFile], files: Mapping[str, str]) -> list[Issue]:
    """The security issues of a change: diff rules over the added lines, AST rules over the
    current text of the changed, non-test Python files. Every issue is HIGH."""
    trees: dict[str, ast.Module | None] = {}
    issues: list[Issue] = []
    for path in sorted(files):
        if not path.endswith(".py") or is_test_path(path):
            continue
        try:
            trees[path] = ast.parse(files[path], filename=path)
        except SyntaxError as error:
            trees[path] = None
            issues.append(
                Issue(
                    rule_id="security.unparsable",
                    severity=FindingSeverity.INFO,
                    message=f"{path} could not be parsed ({error.msg}); "
                    "its AST-based security rules were not applied.",
                    path=path,
                    line=error.lineno,
                    category=_CATEGORY,
                )
            )
    issues.extend(_diff_issues(diff, trees))
    for path, tree in trees.items():
        if tree is not None:
            issues.extend(_card_data_issues(path, tree))
            issues.extend(_password_hash_issues(path, tree))
    return issues
