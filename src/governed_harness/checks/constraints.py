"""Checks for the constraints a task states in prose.

A task says "use only the standard library" or "never read the clock"; an agent may agree and
still import ``requests`` or call ``datetime.now()``. ``constraints_from_task`` maps the
constraint sentences to a closed set of machine checks (plus any the task declares by name), and
``check_constraints`` applies them to the changed, non-test Python files with ``ast``. The
mapping is keyword based on purpose: it is deterministic and auditable, and a sentence it does not
recognise simply adds no check (the task can still declare the check explicitly)."""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator, Mapping, Sequence

from governed_harness.checks.interface import is_stub_body
from governed_harness.checks.model import Issue, is_test_path
from governed_harness.domain.enums import FindingSeverity

CONSTRAINT_CHECKS: tuple[str, ...] = (
    "stdlib-only",
    "no-clock-random",
    "annotated-public-api",
    "no-float-money",
    "no-stubs",
)

_CATEGORY = "constraints"

_NEGATION = re.compile(r"\b(?:never|no|not|without|deterministic\w*)\b|n't\b")
_MONEY_SENTENCE = re.compile(
    r"\b(?:money|prices?|amounts?|fares?|costs?|balances?|fees?|totals?|currency|currencies)\b"
)
_STUB_SENTENCE = re.compile(r"notimplementederror|\bstub|placeholder")
_MONEY_TOKEN = re.compile(
    r"^(?:price|amount|total|fare|cost|balance|fee|subtotal|money|charge|payment|salary|"
    r"salaries|tax)(?:e?s)?$"
)

_CLOCK_CALLS = frozenset(
    {
        "time.time",
        "time.time_ns",
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "date.today",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "uuid.uuid1",
        "uuid.uuid4",
        "os.urandom",
    }
)


def constraints_from_task(
    constraints: Sequence[str], declared: Sequence[str] = ()
) -> tuple[str, ...]:
    """The constraint checks that apply to a task: the ones it declares by name (unknown names
    are ignored) and the ones its constraint sentences ask for, in ``CONSTRAINT_CHECKS`` order."""
    selected = {name for name in declared if name in CONSTRAINT_CHECKS}
    for sentence in constraints:
        text = sentence.lower()
        if "standard library" in text or "stdlib" in text:
            selected.add("stdlib-only")
        if ("clock" in text or "random" in text) and _NEGATION.search(text):
            selected.add("no-clock-random")
        if ("type hint" in text or "annotat" in text or "typed" in text) and "public" in text:
            selected.add("annotated-public-api")
        if "float" in text and _MONEY_SENTENCE.search(text):
            selected.add("no-float-money")
        if _STUB_SENTENCE.search(text):
            selected.add("no-stubs")
    return tuple(name for name in CONSTRAINT_CHECKS if name in selected)


def first_party_modules(paths: Sequence[str]) -> frozenset[str]:
    """The top-level import names the change itself provides: ``src/<name>/...`` gives
    ``<name>``, ``<dir>/...`` gives ``<dir>`` and a top-level ``<stem>.py`` gives ``<stem>``.
    ``__future__`` is a compiler directive, not a dependency."""
    names = {"__future__"}
    for raw in paths:
        parts = [part for part in raw.replace("\\", "/").split("/") if part and part != "."]
        if parts and parts[0] == "src" and len(parts) > 1:
            parts = parts[1:]
        if not parts:
            continue
        head = parts[0]
        if len(parts) == 1:
            head = head.rsplit(".", 1)[0] if "." in head else head
        names.add(head)
    return frozenset(names)


def _issue(
    rule_id: str,
    message: str,
    path: str,
    line: int | None,
    severity: FindingSeverity,
    recommendation: str,
) -> Issue:
    return Issue(
        rule_id=rule_id,
        severity=severity,
        message=message,
        path=path,
        line=line,
        category=_CATEGORY,
        recommendation=recommendation,
    )


def check_constraints(
    checks: Sequence[str], files: Mapping[str, str], *, severity: FindingSeverity
) -> list[Issue]:
    """Apply the selected constraint checks (unknown names are ignored) to the non-test Python
    files of a change."""
    selected = {name for name in checks if name in CONSTRAINT_CHECKS}
    if not selected:
        return []
    first_party = first_party_modules(list(files))
    issues: list[Issue] = []
    for path in sorted(files):
        if not path.endswith(".py") or is_test_path(path):
            continue
        source = files[path]
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            issues.append(
                Issue(
                    rule_id="constraints.unparsable",
                    severity=FindingSeverity.INFO,
                    message=f"{path} could not be parsed ({error.msg}); "
                    "its constraints were not checked.",
                    path=path,
                    line=error.lineno,
                    category=_CATEGORY,
                )
            )
            continue
        if "stdlib-only" in selected:
            issues.extend(_non_stdlib_imports(path, tree, first_party, severity))
        if "no-clock-random" in selected:
            issues.extend(_clock_or_random(path, tree, source.splitlines(), severity))
        if "annotated-public-api" in selected:
            issues.extend(_unannotated_public_api(path, tree, severity))
        if "no-float-money" in selected:
            issues.extend(_float_money(path, tree, severity))
        if "no-stubs" in selected:
            issues.extend(_stubs(path, tree, severity))
    return issues


# stdlib-only ----------------------------------------------------------------------------------


def _non_stdlib_imports(
    path: str, tree: ast.Module, first_party: frozenset[str], severity: FindingSeverity
) -> list[Issue]:
    seen: set[str] = set()
    issues: list[Issue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            modules = [node.module]
        else:
            continue
        for module in modules:
            top = module.split(".", 1)[0]
            if top in sys.stdlib_module_names or top in first_party or top in seen:
                continue
            seen.add(top)
            issues.append(
                _issue(
                    "constraints.non-stdlib-import",
                    f"{path} imports {module}, which is neither in the standard library nor "
                    "part of the change; the task allows only the standard library.",
                    path,
                    node.lineno,
                    severity,
                    f"Replace {top} with a standard library module.",
                )
            )
    return sorted(issues, key=lambda issue: issue.line or 0)


# no-clock-random ------------------------------------------------------------------------------


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local names bound by imports, mapped to the dotted name they stand for."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".", 1)[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _dotted(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _resolve(call: ast.Call, aliases: dict[str, str]) -> str | None:
    dotted = _dotted(call.func)
    if dotted is None:
        return None
    head, _, rest = dotted.partition(".")
    resolved = aliases.get(head, head)
    return f"{resolved}.{rest}" if rest else resolved


def _is_nondeterministic(name: str, call: ast.Call) -> bool:
    if name in _CLOCK_CALLS:
        return True
    module, _, function = name.partition(".")
    if module == "random" and function:
        # ``random.Random(seed)`` is a seeded generator: deterministic by construction.
        return not (function == "Random" and (call.args or call.keywords))
    return module == "secrets" and bool(function)


def _clock_or_random(
    path: str, tree: ast.Module, lines: list[str], severity: FindingSeverity
) -> list[Issue]:
    aliases = _import_aliases(tree)
    issues: list[Issue] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = function
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                scope = child.name
            if isinstance(child, ast.Call):
                name = _resolve(child, aliases)
                if name is not None and _is_nondeterministic(name, child):
                    issues.extend(_clock_issue(path, child, name, scope, lines, severity))
            visit(child, scope)

    visit(tree, "")
    return issues


def _clock_issue(
    path: str,
    call: ast.Call,
    name: str,
    function: str,
    lines: list[str],
    severity: FindingSeverity,
) -> list[Issue]:
    if name.startswith("secrets.") or name == "os.urandom":
        line_text = lines[call.lineno - 1] if 0 < call.lineno <= len(lines) else ""
        if "salt" in function.lower() or "salt" in line_text.lower():
            return []
    return [
        _issue(
            "constraints.clock-or-random",
            f"{name}() reads the clock or a random source; the task requires deterministic "
            "behaviour.",
            path,
            call.lineno,
            severity,
            "Inject the clock or the random generator (a parameter or a seeded "
            "random.Random) so callers and tests control it.",
        )
    ]


# annotated-public-api -------------------------------------------------------------------------


def _is_public(name: str) -> bool:
    return name == "__init__" or not name.startswith("_")


def _missing_annotations(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = function.args
    missing = [
        arg.arg
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if arg.annotation is None and arg.arg not in {"self", "cls"}
    ]
    for star, arg in (("*", args.vararg), ("**", args.kwarg)):
        if arg is not None and arg.annotation is None:
            missing.append(f"{star}{arg.arg}")
    if function.returns is None and function.name != "__init__":
        missing.append("return")
    return missing


def _unannotated_public_api(path: str, tree: ast.Module, severity: FindingSeverity) -> list[Issue]:
    candidates: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_public(node.name):
            candidates.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) and _is_public(
                    member.name
                ):
                    candidates.append((f"{node.name}.{member.name}", member))
    issues: list[Issue] = []
    for qualified, function in candidates:
        missing = _missing_annotations(function)
        if missing:
            issues.append(
                _issue(
                    "constraints.unannotated-public-api",
                    f"{qualified} is public but lacks type annotations for: {', '.join(missing)}.",
                    path,
                    function.lineno,
                    severity,
                    "Annotate every parameter and the return type of the public API.",
                )
            )
    return issues


# no-float-money -------------------------------------------------------------------------------


def _is_money_name(name: str) -> bool:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return any(_MONEY_TOKEN.match(token) for token in snake.split("_") if token)


def _mentions_float(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    return any(isinstance(node, ast.Name) and node.id == "float" for node in ast.walk(annotation))


def _is_float_value(value: ast.expr | None) -> bool:
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub | ast.UAdd):
        value = value.operand
    if isinstance(value, ast.Constant):
        return isinstance(value.value, float)
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "float"
    )


def _target_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _float_money_sites(tree: ast.Module) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
            defaults.extend(args.defaults)
            pairs = [
                *zip(positional, defaults, strict=True),
                *zip(args.kwonlyargs, args.kw_defaults, strict=True),
            ]
            for arg, default in pairs:
                if _is_money_name(arg.arg) and (
                    _mentions_float(arg.annotation) or _is_float_value(default)
                ):
                    yield arg.arg, arg.lineno
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and _is_money_name(node.name)
                and _mentions_float(node.returns)
            ):
                yield node.name, node.lineno
        elif isinstance(node, ast.AnnAssign):
            name = _target_name(node.target)
            if (
                name is not None
                and _is_money_name(name)
                and (_mentions_float(node.annotation) or _is_float_value(node.value))
            ):
                yield name, node.lineno
        elif isinstance(node, ast.Assign) and _is_float_value(node.value):
            for target in node.targets:
                name = _target_name(target)
                if name is not None and _is_money_name(name):
                    yield name, node.lineno


def _float_money(path: str, tree: ast.Module, severity: FindingSeverity) -> list[Issue]:
    seen: set[tuple[str, int]] = set()
    issues: list[Issue] = []
    for name, line in sorted(_float_money_sites(tree), key=lambda site: (site[1], site[0])):
        if (name, line) in seen:
            continue
        seen.add((name, line))
        issues.append(
            _issue(
                "constraints.float-money",
                f"{name} holds money as a float, which cannot represent most decimal amounts "
                "exactly.",
                path,
                line,
                severity,
                "Use integer minor units (cents) or decimal.Decimal for money.",
            )
        )
    return issues


# no-stubs -------------------------------------------------------------------------------------


def _decorator_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _is_interface_class(node: ast.ClassDef) -> bool:
    """Protocols and abstract base classes declare methods without implementing them."""
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        name = target.id if isinstance(target, ast.Name) else None
        if isinstance(target, ast.Attribute):
            name = target.attr
        if name in {"Protocol", "ABC"}:
            return True
    return any(
        keyword.arg == "metaclass" and _dotted(keyword.value) in {"ABCMeta", "abc.ABCMeta"}
        for keyword in node.keywords
    )


def _stubs(path: str, tree: ast.Module, severity: FindingSeverity) -> list[Issue]:
    issues: list[Issue] = []

    def visit(node: ast.AST, prefix: str, in_interface: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", _is_interface_class(child))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                exempt = in_interface or bool(
                    _decorator_names(child) & {"abstractmethod", "overload"}
                )
                if not exempt and is_stub_body(child.body):
                    issues.append(
                        _issue(
                            "constraints.stub",
                            f"{prefix}{child.name} is a stub (pass, ..., a docstring or raise "
                            "NotImplementedError); the task forbids placeholders.",
                            path,
                            child.lineno,
                            severity,
                            "Implement the function or remove it.",
                        )
                    )
                visit(child, f"{prefix}{child.name}.", False)
            else:
                visit(child, prefix, in_interface)

    visit(tree, "", False)
    return issues
