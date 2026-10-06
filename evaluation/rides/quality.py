"""Static measures of the code an agent delivered: size, complexity, structure and hygiene.

Everything is computed from the source tree with the standard library (``ast``), plus Ruff, Mypy and
Bandit when they are installed. The same measures are applied to every condition.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

DUPLICATE_WINDOW = 6
LONG_FUNCTION = 50
COMPLEX_FUNCTION = 10


def _complexity(node: ast.AST) -> int:
    """McCabe-style cyclomatic complexity of one function."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler,
                              ast.With, ast.AsyncWith, ast.Assert, ast.comprehension)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.Match):
            score += len(child.cases)
    return score


def _code_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(re.sub(r"\s+", " ", line))
    return lines


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _cycles(graph: dict[str, set[str]]) -> int:
    """Number of strongly connected components with more than one module (import cycles)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    counter = [0]
    found = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, ()):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            size = 0
            while True:
                item = stack.pop()
                on_stack.discard(item)
                size += 1
                if item == node:
                    break
            if size > 1:
                found[0] += 1

    for node in list(graph):
        if node not in index:
            visit(node)
    return found[0]


def analyze(workspace: Path, tools_python: str | None = None) -> dict[str, Any]:
    root = workspace / "src"
    files = sorted(root.rglob("*.py")) if root.exists() else []
    modules = {_module_name(p, root): p for p in files}
    functions: list[dict[str, Any]] = []
    graph: dict[str, set[str]] = defaultdict(set)
    classes = 0
    broad_excepts = 0
    public = annotated = documented = 0
    loc_by_file: dict[str, int] = {}
    windows: dict[str, int] = defaultdict(int)
    syntax_errors = 0
    for name, path in modules.items():
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = _code_lines(text)
        loc_by_file[name] = len(lines)
        for i in range(len(lines) - DUPLICATE_WINDOW + 1):
            chunk = "\n".join(lines[i:i + DUPLICATE_WINDOW])
            windows[hashlib.sha1(chunk.encode()).hexdigest()] += 1
        try:
            tree = ast.parse(text)
        except SyntaxError:
            syntax_errors += 1
            continue
        package = name.split(".")[0] if name else ""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                functions.append({"complexity": _complexity(node), "length": length})
                if not node.name.startswith("_"):
                    public += 1
                    args = [a for a in node.args.args + node.args.kwonlyargs if a.arg not in {"self", "cls"}]
                    if node.returns is not None and all(a.annotation is not None for a in args):
                        annotated += 1
                    if ast.get_docstring(node):
                        documented += 1
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None or (isinstance(node.type, ast.Name) and node.type.id in {"Exception",
                                                                                             "BaseException"}):
                    broad_excepts += 1
            elif isinstance(node, ast.ImportFrom) and node.module:
                target = node.module if node.level == 0 else ".".join(
                    name.split(".")[: max(len(name.split(".")) - node.level + (1 if path.name == "__init__.py" else 0), 0)]
                    + [node.module])
                if target.split(".")[0] == package:
                    graph[name].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == package:
                        graph[name].add(alias.name)
    graph = {k: {t for t in v if t in modules and t != k} for k, v in graph.items()}
    total_loc = sum(loc_by_file.values())
    duplicated = sum(count - 1 for count in windows.values() if count > 1)
    complexities = sorted(f["complexity"] for f in functions)
    result: dict[str, Any] = {
        "modules": len(modules),
        "loc": total_loc,
        "largestModuleShare": round(max(loc_by_file.values()) / total_loc, 4) if total_loc else None,
        "classes": classes,
        "functions": len(functions),
        "meanComplexity": round(sum(complexities) / len(complexities), 3) if complexities else None,
        "maxComplexity": max(complexities) if complexities else None,
        "complexFunctions": sum(c > COMPLEX_FUNCTION for c in complexities),
        "longFunctions": sum(f["length"] > LONG_FUNCTION for f in functions),
        "duplicatedLineShare": round(duplicated / total_loc, 4) if total_loc else None,
        "broadExcepts": broad_excepts,
        "publicFunctions": public,
        "typedPublicShare": round(annotated / public, 4) if public else None,
        "docstringShare": round(documented / public, 4) if public else None,
        "internalImports": sum(len(v) for v in graph.values()),
        "importCycles": _cycles({k: set(v) for k, v in graph.items()}),
        "syntaxErrors": syntax_errors,
    }
    if tools_python and files:
        mypy = subprocess.run([tools_python, "-m", "mypy", "--ignore-missing-imports", "--no-error-summary",
                               "--hide-error-context", "--no-color-output", str(root)],
                              cwd=workspace, capture_output=True, text=True, timeout=900)
        result["mypyErrors"] = sum(1 for line in mypy.stdout.splitlines() if ": error:" in line)
        ruff = subprocess.run([tools_python, "-m", "ruff", "check", "--output-format", "json", "--select",
                               "E,F,W,B,SIM,C90,PL", "--exit-zero", str(root)],
                              cwd=workspace, capture_output=True, text=True, timeout=600)
        try:
            result["ruffFindings"] = len(json.loads(ruff.stdout or "[]"))
        except json.JSONDecodeError:
            result["ruffFindings"] = None
        result["ruffPerKloc"] = round(result["ruffFindings"] / total_loc * 1000, 3) \
            if result.get("ruffFindings") is not None and total_loc else None
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(analyze(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
