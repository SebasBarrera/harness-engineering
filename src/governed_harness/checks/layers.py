"""Layer rules: the forbidden dependencies of an architecture, in any language (#56, #40).

``verification.architecture.forbiddenImports`` (#40) forbids imports between Python module
prefixes. An architecture (``architecture.layers`` and ``allow``, or the rules a person approved
from a survey or an ADR) generalizes it: each layer is a set of path globs and/or module prefixes,
``allow`` lists the layers each layer may depend on (a layer may always use itself; a layer
without an entry may use no other layer), and every import of a changed file is resolved to the
layer it reaches. The imports are read per language:

* Python: the AST (``import``, ``from ... import``, relative imports), as #40 does;
* JavaScript and TypeScript: ``import ... from``, ``export ... from``, ``import()`` and
  ``require()``; a relative specifier is resolved to a workspace path;
* Java, Kotlin, Scala: ``import a.b.C``; C#: ``using A.B``; PHP: ``use A\\B``; Go: the import
  paths; Rust: ``use crate::a::b``; Swift: ``import X``; Ruby: ``require`` and
  ``require_relative``.

A target that resolves to no layer (a library, the standard library) is not a dependency
between layers and is ignored."""

from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from governed_harness.checks.architecture import module_name
from governed_harness.checks.model import Issue, is_test_path
from governed_harness.domain.enums import FindingSeverity

RULE_ID = "architecture.layer-violation"
_CATEGORY = "architecture"
_SEPARATORS = re.compile(r"::|[./\\]")


@dataclass(frozen=True)
class Layer:
    name: str
    paths: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayerRules:
    layers: tuple[Layer, ...]
    allow: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls, layers: Sequence[Mapping[str, object]], allow: Mapping[str, object]
    ) -> LayerRules:
        def strings(value: object) -> tuple[str, ...]:
            return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()

        return cls(
            layers=tuple(
                Layer(
                    name=str(item.get("name")),
                    paths=strings(item.get("paths")),
                    modules=strings(item.get("modules")),
                )
                for item in layers
                if item.get("name")
            ),
            allow={str(key): strings(value) for key, value in allow.items()},
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "layers": [
                {"name": item.name, "paths": list(item.paths), "modules": list(item.modules)}
                for item in self.layers
            ],
            "allow": {key: list(value) for key, value in self.allow.items()},
        }

    def layer_of_path(self, path: str) -> str | None:
        for layer in self.layers:
            if any(_glob(path, pattern) for pattern in layer.paths):
                return layer.name
        return None

    def layer_of_module(self, module: str) -> str | None:
        parts = [item for item in _SEPARATORS.split(module) if item]
        best: tuple[int, str] | None = None
        for layer in self.layers:
            for prefix in layer.modules:
                wanted = [item for item in _SEPARATORS.split(prefix) if item]
                if (
                    wanted
                    and parts[: len(wanted)] == wanted
                    and (best is None or len(wanted) > best[0])
                ):
                    best = (len(wanted), layer.name)
        if best is not None:
            return best[1]
        # A dotted module lives under a path with its segments (``shop.infra.db`` in
        # ``src/shop/infra/db.py``, a Java package in its directory).
        as_path = "/".join(parts)
        if not as_path:
            return None
        for candidate in (
            as_path,
            f"{as_path}.py",
            f"{as_path}/__init__.py",
            f"src/{as_path}",
            f"src/{as_path}.py",
            f"src/{as_path}/__init__.py",
        ):
            found = self.layer_of_path(candidate)
            if found is not None:
                return found
        return None

    def allowed(self, source: str, target: str) -> bool:
        return source == target or target in self.allow.get(source, ())

    def forbidden_pairs(self) -> list[tuple[str, str]]:
        """``(from, to)`` module prefixes of the #40 form, for the layers that declare
        modules."""
        pairs: list[tuple[str, str]] = []
        for source in self.layers:
            for target in self.layers:
                if self.allowed(source.name, target.name):
                    continue
                pairs.extend((left, right) for left in source.modules for right in target.modules)
        return pairs


def _glob(path: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase

    path = path.replace("\\", "/")
    if fnmatchcase(path, pattern):
        return True
    if pattern.startswith("**/") and fnmatchcase(path, pattern[3:]):
        return True
    # "src/domain/**" also matches the directory itself and an extension-less import target.
    return pattern == f"{path}/**"


# ----- imports per language ----------------------------------------------------------------------
_JS_IMPORT = re.compile(
    r"""(?:\bfrom\s+|\b(?:import|require)\s*\(\s*|^\s*import\s+)(['"])(?P<target>[^'"]+)\1"""
)
_JVM_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?(?P<target>[\w.]+)")
_CS_USING = re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?P<target>[A-Z][\w.]*)\s*;")
_PHP_USE = re.compile(r"^\s*use\s+(?:function\s+|const\s+)?(?P<target>[\w\\]+)")
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"(?P<target>[^"]+)"')
_RUST_USE = re.compile(r"^\s*(?:pub\s+)?use\s+(?P<target>(?:crate|super|self)(?:::\w+)+)")
_SWIFT_IMPORT = re.compile(r"^\s*(?:@testable\s+)?import\s+(?P<target>\w+)")
_RUBY_REQUIRE = re.compile(
    r"""^\s*require(?P<relative>_relative)?\s*(?:\(\s*)?(['"])(?P<target>[^'"]+)\2"""
)


_SINGLE_LINE: dict[str, tuple[re.Pattern[str], str]] = {
    "java": (_JVM_IMPORT, ""),
    "kt": (_JVM_IMPORT, ""),
    "kts": (_JVM_IMPORT, ""),
    "scala": (_JVM_IMPORT, ""),
    "cs": (_CS_USING, ""),
    "php": (_PHP_USE, "\\"),
    "rs": (_RUST_USE, "::"),
    "swift": (_SWIFT_IMPORT, ""),
}
"""Languages with one import per line: the pattern and the separator to turn into dots."""


_JS_SUFFIXES = frozenset({"js", "mjs", "cjs", "jsx", "ts", "tsx", "vue"})

_Import = tuple[int, str, str]


def imports(path: str, text: str) -> Iterator[_Import]:
    """``(line, kind, target)`` of every import of a file; ``kind`` is ``path`` (a workspace
    path) or ``module`` (a dotted or package name)."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix == "py":
        yield from _python_imports(path, text)
        return
    directory = posixpath.dirname(path)
    lines = text.splitlines()
    if suffix in _JS_SUFFIXES:
        yield from _js_imports(lines, directory)
    elif suffix == "go":
        yield from _go_imports(lines)
    elif suffix == "rb":
        yield from _ruby_imports(lines, directory)
    elif suffix in _SINGLE_LINE:
        yield from _single_line_imports(lines, *_SINGLE_LINE[suffix])


def _js_imports(lines: list[str], directory: str) -> Iterator[_Import]:
    for number, line in enumerate(lines, start=1):
        for found in _JS_IMPORT.finditer(line):
            target = found["target"]
            if target.startswith("."):
                yield number, "path", posixpath.normpath(posixpath.join(directory, target))
            else:
                yield number, "module", target


def _go_imports(lines: list[str]) -> Iterator[_Import]:
    in_block = False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("import ("):
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        found = _GO_IMPORT.match(line)
        if found is not None and (in_block or stripped.startswith("import ")):
            yield number, "module", found["target"]


def _ruby_imports(lines: list[str], directory: str) -> Iterator[_Import]:
    for number, line in enumerate(lines, start=1):
        ruby = _RUBY_REQUIRE.match(line)
        if ruby is None:
            continue
        if ruby["relative"]:
            yield number, "path", posixpath.normpath(posixpath.join(directory, ruby["target"]))
        else:
            yield number, "module", ruby["target"]


def _single_line_imports(
    lines: list[str], pattern: re.Pattern[str], separator: str
) -> Iterator[_Import]:
    """One import per line (Java, Kotlin, Scala, C#, PHP, Rust, Swift)."""
    for number, line in enumerate(lines, start=1):
        found = pattern.match(line)
        if found is not None:
            target = found["target"]
            yield number, "module", target.replace(separator, ".") if separator else target


def _python_imports(path: str, text: str) -> Iterator[tuple[int, str, str]]:
    from governed_harness.checks.architecture import _imported_modules

    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return
    module = module_name(path)
    is_package = path.replace("\\", "/").endswith("__init__.py")
    for line, imported in _imported_modules(tree, module, is_package):
        yield line, "module", imported


# ----- the check -----------------------------------------------------------------------------------
# Blanks within the line only (``[^\S\n]``) and one optional terminator: linear on any text.
_DECLARATION = re.compile(
    r"^[^\S\n]*(?:package|namespace)\s+(?P<name>[A-Za-z_][\w.\\]*)[^\S\n]*(?:[;{][^\S\n]*)?$",
    re.MULTILINE,
)


def source_layer(rules: LayerRules, path: str, text: str = "") -> str | None:
    """The layer of a file: by its path, else by its module (Python), its declared package or
    namespace (Java, Kotlin, C#, PHP, Go), else by a module prefix that appears in its path
    (``src/main/java/com/shop/domain/Order.java`` is in ``com.shop.domain``)."""
    layer = rules.layer_of_path(path)
    if layer is not None:
        return layer
    if path.endswith(".py"):
        return rules.layer_of_module(module_name(path))
    declared = _DECLARATION.search(text)
    if declared is not None:
        layer = rules.layer_of_module(declared["name"].replace("\\", "."))
        if layer is not None:
            return layer
    return _layer_of_directories(rules, path)


def _layer_of_directories(rules: LayerRules, path: str) -> str | None:
    """The first layer with a module prefix whose segments appear in the file's directories."""
    parts = [item for item in path.replace("\\", "/").split("/")[:-1] if item]
    for layer_item in rules.layers:
        for prefix in layer_item.modules:
            wanted = [item for item in _SEPARATORS.split(prefix) if item]
            if wanted and _contains(parts, wanted):
                return layer_item.name
    return None


def _contains(parts: list[str], wanted: list[str]) -> bool:
    return any(
        parts[start : start + len(wanted)] == wanted
        for start in range(len(parts) - len(wanted) + 1)
    )


def check_layers(
    files: Mapping[str, str],
    rules: LayerRules,
    *,
    severity: FindingSeverity,
) -> list[Issue]:
    """Every import of a changed (non-test) file that crosses into a layer its layer may not
    depend on."""
    issues: list[Issue] = []
    if not rules.layers:
        return issues
    for path in sorted(files):
        if is_test_path(path):
            continue
        source = source_layer(rules, path, files[path])
        if source is not None:
            issues.extend(_violations(rules, path, files[path], source, severity))
    return issues


def _violations(
    rules: LayerRules, path: str, text: str, source: str, severity: FindingSeverity
) -> Iterator[Issue]:
    """The imports of one file that reach a layer ``source`` may not depend on, one issue per
    line and layer."""
    reported: set[tuple[int, str]] = set()
    allowed = ", ".join(rules.allow.get(source, ())) or "no other layer"
    for line, kind, target in imports(path, text):
        layer = rules.layer_of_path(target) if kind == "path" else rules.layer_of_module(target)
        if layer is None or rules.allowed(source, layer) or (line, layer) in reported:
            continue
        reported.add((line, layer))
        yield Issue(
            rule_id=RULE_ID,
            severity=severity,
            message=(
                f"{path} is in layer {source} and imports {target} (layer {layer}); "
                f"{source} may depend on {allowed}"
            ),
            path=path,
            line=line,
            category=_CATEGORY,
            recommendation=(
                f"Invert the dependency (an interface in {source} that {layer} "
                "implements) or move the code to the layer it belongs to."
            ),
        )


__all__ = ["RULE_ID", "Layer", "LayerRules", "check_layers", "imports", "source_layer"]
