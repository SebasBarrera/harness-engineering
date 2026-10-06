"""Whether an implementation honours a declared Python interface.

A task can declare the interface it expects (a ``.pyi`` stub, or a ``.py`` file whose bodies are
``...``). An agent that renames a parameter, turns a keyword-only argument into a positional one or
leaves ``raise NotImplementedError`` behind still passes a test suite that does not exercise that
path, so the comparison is structural: it reads both sources with ``ast`` and never imports them.
Annotations are deliberately not compared: they do not change how a caller invokes the symbol, and
stub annotations routinely differ in spelling (``Optional[int]`` vs ``int | None``)."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from governed_harness.checks.model import Issue
from governed_harness.domain.enums import FindingSeverity

_CATEGORY = "interface"

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _Param:
    name: str
    kind: str
    """One of ``posonly``, ``regular``, ``vararg``, ``kwonly``, ``kwarg``."""
    default: str | None
    """``ast.unparse`` of the default, or ``None`` when the parameter has no default."""


@dataclass(frozen=True)
class _Signature:
    name: str
    params: tuple[_Param, ...]
    is_async: bool
    line: int | None
    node: FunctionNode | None
    """The implementing ``def``; ``None`` for a signature synthesised from a dataclass."""

    def render(self) -> str:
        parts: list[str] = []
        previous = ""
        star_written = False
        for param in self.params:
            if previous == "posonly" and param.kind != "posonly":
                parts.append("/")
            if param.kind == "kwonly" and not star_written:
                parts.append("*")
                star_written = True
            if param.kind == "vararg":
                parts.append(f"*{param.name}")
                star_written = True
            elif param.kind == "kwarg":
                parts.append(f"**{param.name}")
            elif param.default is not None:
                parts.append(f"{param.name}={param.default}")
            else:
                parts.append(param.name)
            previous = param.kind
        if previous == "posonly":
            parts.append("/")
        prefix = "async " if self.is_async else ""
        return f"{prefix}{self.name}({', '.join(parts)})"


def is_public(name: str) -> bool:
    """Public names are those a caller is meant to use: no leading underscore, plus
    ``__init__``, whose signature is how a class is constructed."""
    return name == "__init__" or not name.startswith("_")


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_not_implemented(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.Raise) or statement.exc is None:
        return False
    target = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
    return (isinstance(target, ast.Name) and target.id == "NotImplementedError") or (
        isinstance(target, ast.Attribute) and target.attr == "NotImplementedError"
    )


def is_stub_body(body: list[ast.stmt]) -> bool:
    """Whether a function body does nothing: only ``pass``, ``...``, a docstring, or
    ``raise NotImplementedError`` (optionally after a docstring). Such a body satisfies the
    interface on paper while delivering no behaviour."""
    statements = list(body)
    if statements and _is_docstring(statements[0]):
        statements = statements[1:]
    if not statements:
        return True
    if len(statements) != 1:
        return False
    only = statements[0]
    if isinstance(only, ast.Pass):
        return True
    if (
        isinstance(only, ast.Expr)
        and isinstance(only.value, ast.Constant)
        and only.value.value is Ellipsis
    ):
        return True
    return _is_not_implemented(only)


def _decorator_names(node: FunctionNode | ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _signature(node: FunctionNode) -> _Signature:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(args.defaults)
    params: list[_Param] = []
    for index, arg in enumerate(positional):
        kind = "posonly" if index < len(args.posonlyargs) else "regular"
        default = defaults[index]
        params.append(_Param(arg.arg, kind, None if default is None else ast.unparse(default)))
    if args.vararg is not None:
        params.append(_Param(args.vararg.arg, "vararg", None))
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        rendered = None if kw_default is None else ast.unparse(kw_default)
        params.append(_Param(arg.arg, "kwonly", rendered))
    if args.kwarg is not None:
        params.append(_Param(args.kwarg.arg, "kwarg", None))
    return _Signature(
        name=node.name,
        params=tuple(params),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        line=node.lineno,
        node=node,
    )


def _functions(body: list[ast.stmt]) -> dict[str, FunctionNode]:
    """The functions defined directly in a body, by name. ``@overload`` variants only stand in
    for a name when nothing else defines it; a later definition replaces an earlier one, as at
    run time."""
    found: dict[str, FunctionNode] = {}
    for statement in body:
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        is_overload = "overload" in _decorator_names(statement)
        current = found.get(statement.name)
        if is_overload and current is not None and "overload" not in _decorator_names(current):
            continue
        found[statement.name] = statement
    return found


def _classes(body: list[ast.stmt]) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in body if isinstance(node, ast.ClassDef)}


def _is_dataclass(node: ast.ClassDef) -> bool:
    return "dataclass" in _decorator_names(node)


def _dataclass_kw_only(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "kw_only"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    return True
    return False


def _field_call(value: ast.expr | None) -> ast.Call | None:
    if not isinstance(value, ast.Call):
        return None
    func = value.func
    is_field = (isinstance(func, ast.Name) and func.id == "field") or (
        isinstance(func, ast.Attribute) and func.attr == "field"
    )
    return value if is_field else None


def _is_classvar(annotation: ast.expr) -> bool:
    target = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    return (isinstance(target, ast.Name) and target.id == "ClassVar") or (
        isinstance(target, ast.Attribute) and target.attr == "ClassVar"
    )


def _is_kw_only_marker(annotation: ast.expr) -> bool:
    return (isinstance(annotation, ast.Name) and annotation.id == "KW_ONLY") or (
        isinstance(annotation, ast.Attribute) and annotation.attr == "KW_ONLY"
    )


def _dataclass_params(node: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> list[_Param]:
    """The ``__init__`` parameters ``@dataclass`` generates, base dataclasses of the same
    module first. Without this a declared ``__init__`` would be reported missing for every
    dataclass, which is how most value objects are written."""
    params: list[_Param] = []
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in classes and base.id != node.name:
            base_node = classes[base.id]
            if _is_dataclass(base_node):
                params.extend(_dataclass_params(base_node, classes))
    kw_only = _dataclass_kw_only(node)
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        if _is_classvar(statement.annotation):
            continue
        if _is_kw_only_marker(statement.annotation):
            kw_only = True
            continue
        default: str | None = None
        include = True
        call = _field_call(statement.value)
        if call is not None:
            for keyword in call.keywords:
                if keyword.arg == "default":
                    default = ast.unparse(keyword.value)
                elif keyword.arg == "default_factory":
                    default = f"field(default_factory={ast.unparse(keyword.value)})"
                elif (
                    keyword.arg == "init"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                ):
                    include = False
        elif statement.value is not None:
            default = ast.unparse(statement.value)
        if not include:
            continue
        name = statement.target.id
        params = [param for param in params if param.name != name]
        params.append(_Param(name, "kwonly" if kw_only else "regular", default))
    return params


def _find_method(
    cls: ast.ClassDef, name: str, classes: dict[str, ast.ClassDef], seen: set[str]
) -> _Signature | None:
    """A method of a class, looked up in the class, then in its bases defined in the same
    module (an inherited method satisfies the interface), then, for ``__init__`` of a
    dataclass, the generated constructor."""
    seen.add(cls.name)
    method = _functions(cls.body).get(name)
    if method is not None:
        return _signature(method)
    if name == "__init__" and _is_dataclass(cls):
        params = (_Param("self", "regular", None), *_dataclass_params(cls, classes))
        return _Signature("__init__", tuple(params), False, cls.lineno, None)
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
            found = _find_method(classes[base.id], name, classes, seen)
            if found is not None:
                return found
    return None


def _shape(signature: _Signature) -> tuple[bool, tuple[tuple[str, str], ...]]:
    return signature.is_async, tuple((param.name, param.kind) for param in signature.params)


def _default_differences(declared: _Signature, actual: _Signature) -> list[str]:
    differences: list[str] = []
    for want, have in zip(declared.params, actual.params, strict=True):
        if want.default is None and have.default is None:
            continue
        if want.default == "..." and have.default is not None:
            continue
        if want.default != have.default:
            differences.append(
                f"{want.name}: declared {want.default or 'no default'}, "
                f"actual {have.default or 'no default'}"
            )
    return differences


def _compare(
    qualified: str,
    declared: _Signature,
    actual: _Signature,
    *,
    implementation_path: str,
    severity: FindingSeverity,
) -> list[Issue]:
    issues: list[Issue] = []
    if _shape(declared) != _shape(actual):
        issues.append(
            Issue(
                rule_id="interface.signature",
                severity=severity,
                message=(
                    f"{qualified} does not match the declared signature: declared "
                    f"`{declared.render()}`, actual `{actual.render()}`."
                ),
                path=implementation_path,
                line=actual.line,
                category=_CATEGORY,
                recommendation=(
                    "Keep the declared parameter names, order and kinds (positional-only, "
                    "keyword-only, *args, **kwargs) and the declared async/sync form."
                ),
            )
        )
    else:
        differences = _default_differences(declared, actual)
        if differences:
            issues.append(
                Issue(
                    rule_id="interface.default",
                    severity=severity,
                    message=(
                        f"{qualified} changes declared defaults ({'; '.join(differences)}): "
                        f"declared `{declared.render()}`, actual `{actual.render()}`."
                    ),
                    path=implementation_path,
                    line=actual.line,
                    category=_CATEGORY,
                    recommendation="Use the default values the interface declares.",
                )
            )
    if actual.node is not None and is_stub_body(actual.node.body):
        issues.append(
            Issue(
                rule_id="interface.stub",
                severity=severity,
                message=(
                    f"{qualified} is declared by the interface but its implementation is a "
                    "stub (pass, ..., a docstring or raise NotImplementedError)."
                ),
                path=implementation_path,
                line=actual.line,
                category=_CATEGORY,
                recommendation="Implement the behaviour the interface declares.",
            )
        )
    return issues


def _missing(
    qualified: str,
    kind: str,
    *,
    implementation_path: str,
    line: int | None,
    severity: FindingSeverity,
) -> Issue:
    return Issue(
        rule_id="interface.missing",
        severity=severity,
        message=f"The declared {kind} {qualified} is not defined in {implementation_path}.",
        path=implementation_path,
        line=line,
        category=_CATEGORY,
        recommendation=f"Define {qualified} with the declared signature.",
    )


def _unparsable(which: str, path: str, error: SyntaxError) -> Issue:
    return Issue(
        rule_id="interface.unparsable",
        severity=FindingSeverity.INFO,
        message=f"The {which} source {path} could not be parsed ({error.msg}); "
        "the interface was not compared.",
        path=path,
        line=error.lineno,
        category=_CATEGORY,
    )


def check_interface(
    declared_source: str,
    implementation_source: str,
    *,
    declared_path: str,
    implementation_path: str,
    severity: FindingSeverity,
) -> list[Issue]:
    """Compare the public top-level functions and classes (and their public methods) of a
    declared interface with an implementation, and report missing symbols, signature and default
    mismatches and stub bodies."""
    try:
        declared_tree = ast.parse(declared_source, filename=declared_path)
    except SyntaxError as error:
        return [_unparsable("declared", declared_path, error)]
    try:
        implementation_tree = ast.parse(implementation_source, filename=implementation_path)
    except SyntaxError as error:
        return [_unparsable("implementation", implementation_path, error)]

    issues: list[Issue] = []
    actual_functions = _functions(implementation_tree.body)
    actual_classes = _classes(implementation_tree.body)

    for name, node in _functions(declared_tree.body).items():
        if not is_public(name):
            continue
        actual = actual_functions.get(name)
        if actual is None:
            issues.append(
                _missing(
                    name,
                    "function",
                    implementation_path=implementation_path,
                    line=None,
                    severity=severity,
                )
            )
            continue
        issues.extend(
            _compare(
                name,
                _signature(node),
                _signature(actual),
                implementation_path=implementation_path,
                severity=severity,
            )
        )

    for class_name, class_node in _classes(declared_tree.body).items():
        if not is_public(class_name):
            continue
        actual_class = actual_classes.get(class_name)
        if actual_class is None:
            issues.append(
                _missing(
                    class_name,
                    "class",
                    implementation_path=implementation_path,
                    line=None,
                    severity=severity,
                )
            )
            continue
        for method_name, method in _functions(class_node.body).items():
            if not is_public(method_name):
                continue
            qualified = f"{class_name}.{method_name}"
            found = _find_method(actual_class, method_name, actual_classes, set())
            if found is None:
                issues.append(
                    _missing(
                        qualified,
                        "method",
                        implementation_path=implementation_path,
                        line=actual_class.lineno,
                        severity=severity,
                    )
                )
                continue
            issues.extend(
                _compare(
                    qualified,
                    _signature(method),
                    found,
                    implementation_path=implementation_path,
                    severity=severity,
                )
            )
    return issues


def declared_names(declared_source: str) -> list[str]:
    """The public functions and methods an interface declares (``create_order``,
    ``Dispatcher.advance``), in source order, so other checks can look for them (in tests, in
    traceability). Constructors are left out: callers name the class, not ``__init__``. An
    unparsable source declares nothing."""
    try:
        tree = ast.parse(declared_source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_") and node.name not in names:
                names.append(node.name)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for member in node.body:
                if isinstance(
                    member, ast.FunctionDef | ast.AsyncFunctionDef
                ) and not member.name.startswith("_"):
                    qualified = f"{node.name}.{member.name}"
                    if qualified not in names:
                        names.append(qualified)
    return names
