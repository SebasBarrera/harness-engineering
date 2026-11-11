"""Good and bad coding practices of a set of Python files, computed with ``ast`` only.

Used by ``compare_implementations.py`` on the changed files of a run, after and before the change,
so every count can be read as "what the change added". The thresholds (complexity above 10, a
function longer than 50 lines, a duplicated window of 6 normalized lines) are those of
``evaluation/rides/quality.py``; the complexity is its McCabe-style count.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rides.quality import (  # noqa: E402
    COMPLEX_FUNCTION,
    DUPLICATE_WINDOW,
    LONG_FUNCTION,
    _code_lines,
    _complexity,
)

ALLOWED_NUMBERS = {0, 1, 2, -1}
SNAKE = re.compile(r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$")
CAPWORDS = re.compile(r"^_?[A-Z][A-Za-z0-9]*$")
ALLOWED_SINGLE = {"i", "j", "k", "n", "x", "y", "_", "e", "f"}
TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _parse(text: str | None) -> ast.Module | None:
    if not text:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, FUNCTIONS)]


def _swallows(handler: ast.ExceptHandler) -> bool:
    """A handler that neither re-raises, logs, nor returns a value: the error disappears."""
    body = handler.body
    if all(
        isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in body
    ):
        return True
    if len(body) == 1 and isinstance(body[0], ast.Continue | ast.Break):
        return True
    return (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and (
            body[0].value is None
            or (isinstance(body[0].value, ast.Constant) and body[0].value.value is None)
        )
    )


def _module_constants(tree: ast.Module) -> set[int]:
    """ids of the constants directly assigned to a module-level name (named constants)."""
    found: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            for sub in ast.walk(node.value):
                found.add(id(sub))
    return found


def _defaults(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for fn in _functions(tree):
        for default in [*fn.args.defaults, *fn.args.kw_defaults]:
            if default is not None:
                found.update(id(sub) for sub in ast.walk(default))
    return found


def source_practices(texts: dict[str, str]) -> dict[str, Any]:
    """Practices of source modules (``path -> text``)."""
    functions = complexities = 0
    complex_functions = long_functions = max_complexity = max_length = 0
    bare = broad = swallowed = raise_in_except = raise_from = 0
    params = annotated = returns = annotated_returns = 0
    public = documented = 0
    magic = unreachable = raises = isinstance_checks = 0
    bad_names = single_letter = 0
    code_lines = 0
    windows: Counter[str] = Counter()
    for _path, text in sorted(texts.items()):
        tree = _parse(text)
        if tree is None:
            continue
        lines = _code_lines(text)
        code_lines += len(lines)
        for start in range(len(lines) - DUPLICATE_WINDOW + 1):
            windows["\n".join(lines[start : start + DUPLICATE_WINDOW])] += 1
        named = _module_constants(tree) | _defaults(tree)
        for node in ast.walk(tree):
            if isinstance(node, FUNCTIONS):
                functions += 1
                score = _complexity(node)
                complexities += score
                max_complexity = max(max_complexity, score)
                complex_functions += score > COMPLEX_FUNCTION
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                max_length = max(max_length, length)
                long_functions += length > LONG_FUNCTION
                arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                arguments += [a for a in (node.args.vararg, node.args.kwarg) if a is not None]
                arguments = [a for a in arguments if a.arg not in ("self", "cls")]
                params += len(arguments)
                annotated += sum(1 for a in arguments if a.annotation is not None)
                returns += 1
                annotated_returns += node.returns is not None
                if not node.name.startswith("_") or node.name == "__init__":
                    public += not node.name.startswith("_")
                    documented += bool(ast.get_docstring(node)) and not node.name.startswith("_")
                bad_names += not SNAKE.match(node.name) and not node.name.startswith("visit_")
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    public += 1
                    documented += bool(ast.get_docstring(node))
                bad_names += not CAPWORDS.match(node.name)
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare += 1
                elif isinstance(node.type, ast.Name) and node.type.id in (
                    "Exception",
                    "BaseException",
                ):
                    broad += 1
                swallowed += _swallows(node)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Raise) and sub.exc is not None:
                        raise_in_except += 1
                        raise_from += sub.cause is not None
            elif isinstance(node, ast.Raise):
                raises += 1
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
            ):
                isinstance_checks += 1
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, int | float)
                and not isinstance(node.value, bool)
                and node.value not in ALLOWED_NUMBERS
                and id(node) not in named
            ):
                magic += 1
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                single_letter += len(node.id) == 1 and node.id not in ALLOWED_SINGLE
            for field in ("body", "orelse", "finalbody"):
                body = getattr(node, field, None)
                if isinstance(body, list):
                    for index, statement in enumerate(body[:-1]):
                        if isinstance(statement, TERMINATORS):
                            unreachable += len(body) - index - 1
                            break
    duplicated = sum(n - 1 for n in windows.values() if n > 1)
    return {
        "codeLines": code_lines,
        "functions": functions,
        "meanComplexity": round(complexities / functions, 2) if functions else None,
        "maxComplexity": max_complexity,
        "complexFunctions": complex_functions,
        "maxFunctionLength": max_length,
        "longFunctions": long_functions,
        "duplicatedWindows": duplicated,
        "bareExcept": bare,
        "broadExcept": broad,
        "swallowedErrors": swallowed,
        "raiseInExcept": raise_in_except,
        "raiseInExceptWithFrom": raise_from,
        "raiseInExceptWithoutFrom": raise_in_except - raise_from,
        "parameters": params,
        "annotatedParameters": annotated,
        "typeHintCoverage": round((annotated + annotated_returns) / (params + returns), 4)
        if params + returns
        else None,
        "publicDefinitions": public,
        "documentedPublic": documented,
        "docstringCoverage": round(documented / public, 4) if public else None,
        "magicNumbers": magic,
        "unreachableStatements": unreachable,
        "raiseStatements": raises,
        "isinstanceChecks": isinstance_checks,
        "namingViolations": bad_names,
        "singleLetterNames": single_letter,
    }


def _is_test(node: ast.AST) -> bool:
    return isinstance(node, FUNCTIONS) and node.name.startswith("test")


def _test_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    found = [n for n in tree.body if _is_test(n)]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found += [n for n in node.body if _is_test(n)]
    return found  # type: ignore[return-value]


def _assertions(fn: ast.AST) -> int:
    count = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("raises", "warns")
                ):
                    count += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name.startswith("assert") or name in ("raises", "fail"):
                count += 1
    return count


def _parametrize_cases(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    cases = 0
    for decorator in fn.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "parametrize"
            and len(decorator.args) > 1
            and isinstance(decorator.args[1], ast.List | ast.Tuple)
        ):
            cases += len(decorator.args[1].elts)
    return cases


def _edge_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or value in ("", b""):
            return True
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and (value < 0 or abs(value) >= 2**31)
        ):
            return True
        if isinstance(value, str) and (
            value.isspace() or any(ord(c) > 127 or ord(c) < 32 for c in value)
        ):
            return True
        if isinstance(value, bytes) and any(b > 127 or b < 32 for b in value):
            return True
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
    ):
        return True
    return isinstance(node, ast.List | ast.Dict | ast.Set | ast.Tuple) and not getattr(
        node, "elts", getattr(node, "keys", [1])
    )


def test_practices(texts: dict[str, str]) -> dict[str, Any]:
    """Quality of test modules: assertions, empty tests, edge cases, independence."""
    tests = assertions = empty = raises = parametrized = edges = 0
    globals_used = tests_calling_tests = shared_mutable = 0
    for _path, text in sorted(texts.items()):
        tree = _parse(text)
        if tree is None:
            continue
        found = _test_functions(tree)
        names = {fn.name for fn in found}
        mutable_names = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.List | ast.Dict | ast.Set)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for fn in found:
            tests += 1
            count = _assertions(fn)
            assertions += count
            empty += count == 0
            parametrized += _parametrize_cases(fn)
            for node in ast.walk(fn):
                if isinstance(node, ast.With) and any(
                    isinstance(i.context_expr, ast.Call)
                    and isinstance(i.context_expr.func, ast.Attribute)
                    and i.context_expr.func.attr == "raises"
                    for i in node.items
                ):
                    raises += 1
                if isinstance(node, ast.Global):
                    globals_used += 1
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in names
                ):
                    tests_calling_tests += 1
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in mutable_names
                    and node.func.attr
                    in ("append", "extend", "update", "add", "pop", "clear", "remove", "insert")
                ):
                    shared_mutable += 1
            for decorator in fn.decorator_list:
                edges += sum(1 for sub in ast.walk(decorator) if _edge_literal(sub))
            edges += sum(1 for sub in ast.walk(fn) if _edge_literal(sub))
    return {
        "tests": tests,
        "assertions": assertions,
        "assertionsPerTest": round(assertions / tests, 2) if tests else None,
        "testsWithoutAssertion": empty,
        "negativeTests": raises,
        "parametrizedCases": parametrized,
        "edgeCaseLiterals": edges,
        "globalStatements": globals_used,
        "testsCallingTests": tests_calling_tests,
        "sharedMutableStateWrites": shared_mutable,
    }


def delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """after - before for the integer counts (ratios and maxima keep both values)."""
    out: dict[str, Any] = {}
    for key, value in after.items():
        old = before.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and isinstance(old, int):
            out[key] = value - old
    return out
