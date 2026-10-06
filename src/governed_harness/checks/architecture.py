"""Size, complexity and layering limits over the Python files of a change.

These are the structural properties a reviewer would otherwise judge by eye: a module or function
that grew past what the task allows, a function whose branching makes it hard to test, a layer that
reaches into one it must not depend on. Measuring them from the AST keeps the verdict reproducible
and independent of formatting."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from governed_harness.checks.model import Issue, is_test_path
from governed_harness.domain.enums import FindingSeverity

_CATEGORY = "architecture"


@dataclass(frozen=True)
class ArchitectureLimits:
    """The limits a task or project sets; ``None`` (or an empty tuple) disables a rule."""

    max_module_lines: int | None = None
    max_function_lines: int | None = None
    max_complexity: int | None = None
    forbidden_imports: tuple[tuple[str, str], ...] = ()
    """``(source, target)`` pairs of dotted module prefixes: modules under ``source`` must not
    import modules under ``target``."""


def module_name(path: str) -> str:
    """The dotted module name of a workspace path: ``src/shop/orders.py`` -> ``shop.orders``,
    ``shop/__init__.py`` -> ``shop``. The ``src/`` layout prefix is not part of the import
    name, so it is dropped."""
    normalized = path.replace("\\", "/")
    normalized = normalized.removeprefix("./").removeprefix("src/")
    normalized = normalized.removesuffix(".py")
    name = normalized.replace("/", ".")
    return name.removesuffix(".__init__")


def is_under(module: str, prefix: str) -> bool:
    """Whether a dotted module equals a prefix or lives inside it (``a.b`` is under ``a`` but
    ``ab`` is not)."""
    return module == prefix or module.startswith(prefix + ".")


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    parts = module.split(".") if module else []
    if not is_package:
        parts = parts[:-1]
    if level > 1:
        parts = parts[: max(len(parts) - (level - 1), 0)]
    if target:
        parts.append(target)
    return ".".join(parts)


def _imported_modules(tree: ast.Module, module: str, is_package: bool) -> Iterator[tuple[int, str]]:
    """Every module an import statement may bind, with its line. ``from pkg import name`` may
    import the submodule ``pkg.name``, so both ``pkg`` and ``pkg.name`` are candidates."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(module, is_package, node.level, node.module)
            else:
                base = node.module or ""
            if base:
                yield node.lineno, base
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, f"{base}.{alias.name}" if base else alias.name


def _functions(
    tree: ast.AST, prefix: str = ""
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function and method with a qualified name (``Class.method``, ``outer.inner``)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            qualified = f"{prefix}{node.name}"
            yield qualified, node
            yield from _functions(node, qualified + ".")
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node, f"{prefix}{node.name}.")
        else:
            yield from _functions(node, prefix)


def _decision_points(node: ast.AST) -> int:
    """The decision points of one function, not counting nested functions and classes, which
    are measured on their own (as McCabe does)."""
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(
            child,
            ast.If | ast.For | ast.AsyncFor | ast.While | ast.IfExp | ast.ExceptHandler,
        ):
            count += 1
        elif isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            count += 1 + len(child.ifs)
        elif isinstance(child, ast.match_case):
            count += 1
        count += _decision_points(child)
    return count


def cyclomatic_complexity(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """McCabe complexity: one plus each ``if``/``elif``, loop, ``except`` handler, extra
    ``and``/``or`` operand, comprehension ``for`` and ``if``, conditional expression and
    ``match`` case."""
    return 1 + _decision_points(function)


def check_architecture(
    files: Mapping[str, str],
    limits: ArchitectureLimits,
    *,
    severity: FindingSeverity,
) -> list[Issue]:
    """Measure the non-test Python files of a change against the limits."""
    issues: list[Issue] = []
    needs_ast = (
        limits.max_function_lines is not None
        or limits.max_complexity is not None
        or bool(limits.forbidden_imports)
    )
    for path in sorted(files):
        if not path.endswith(".py") or is_test_path(path):
            continue
        text = files[path]
        line_count = len(text.splitlines())
        if limits.max_module_lines is not None and line_count > limits.max_module_lines:
            issues.append(
                Issue(
                    rule_id="architecture.module-lines",
                    severity=severity,
                    message=(
                        f"{path} has {line_count} lines; the limit is {limits.max_module_lines}."
                    ),
                    path=path,
                    line=1,
                    category=_CATEGORY,
                    recommendation="Split the module along its responsibilities.",
                )
            )
        if not needs_ast:
            continue
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as error:
            issues.append(
                Issue(
                    rule_id="architecture.unparsable",
                    severity=FindingSeverity.INFO,
                    message=f"{path} could not be parsed ({error.msg}); "
                    "its functions and imports were not measured.",
                    path=path,
                    line=error.lineno,
                    category=_CATEGORY,
                )
            )
            continue
        issues.extend(_function_issues(path, tree, limits, severity))
        issues.extend(_import_issues(path, tree, limits, severity))
    return issues


def _function_issues(
    path: str, tree: ast.Module, limits: ArchitectureLimits, severity: FindingSeverity
) -> list[Issue]:
    issues: list[Issue] = []
    for qualified, function in _functions(tree):
        length = (function.end_lineno or function.lineno) - function.lineno + 1
        if limits.max_function_lines is not None and length > limits.max_function_lines:
            issues.append(
                Issue(
                    rule_id="architecture.function-lines",
                    severity=severity,
                    message=(
                        f"{qualified} spans {length} lines; the limit is "
                        f"{limits.max_function_lines}."
                    ),
                    path=path,
                    line=function.lineno,
                    category=_CATEGORY,
                    recommendation="Extract cohesive steps into smaller functions.",
                )
            )
        if limits.max_complexity is not None:
            complexity = cyclomatic_complexity(function)
            if complexity > limits.max_complexity:
                issues.append(
                    Issue(
                        rule_id="architecture.complexity",
                        severity=severity,
                        message=(
                            f"{qualified} has cyclomatic complexity {complexity}; the limit is "
                            f"{limits.max_complexity}."
                        ),
                        path=path,
                        line=function.lineno,
                        category=_CATEGORY,
                        recommendation=(
                            "Reduce branching: extract decisions into helpers, use lookup "
                            "tables or early returns."
                        ),
                    )
                )
    return issues


def _import_issues(
    path: str, tree: ast.Module, limits: ArchitectureLimits, severity: FindingSeverity
) -> list[Issue]:
    if not limits.forbidden_imports:
        return []
    module = module_name(path)
    is_package = path.replace("\\", "/").endswith("/__init__.py") or path == "__init__.py"
    rules = [
        (source, target) for source, target in limits.forbidden_imports if is_under(module, source)
    ]
    if not rules:
        return []
    issues: list[Issue] = []
    reported: set[tuple[int, str, str]] = set()
    for line, imported in _imported_modules(tree, module, is_package):
        for source, target in rules:
            key = (line, source, target)
            if key in reported or not is_under(imported, target):
                continue
            reported.add(key)
            issues.append(
                Issue(
                    rule_id="architecture.forbidden-import",
                    severity=severity,
                    message=(
                        f"{module} imports {imported}, but modules under {source} must not "
                        f"import modules under {target}."
                    ),
                    path=path,
                    line=line,
                    category=_CATEGORY,
                    recommendation=(
                        f"Invert the dependency (define an interface in {source} that {target} "
                        "implements) or move the code to the layer it belongs to."
                    ),
                )
            )
    return issues
