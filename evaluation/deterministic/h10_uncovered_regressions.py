#!/usr/bin/env python3
"""H10: regressions the suite does not cover, a neutrality test (``CATALOGO.md`` §2.1,
``PREREGISTRO.md``). No model.

itsdangerous 2.2.0 (the archive of ``run_deterministic.py``, checked against its SHA-256) with its
297 tests. The cases are drawn, not chosen: every mutation site of ``src/itsdangerous/*.py`` of
four generic operator classes (off-by-one, exception change, order change, other encoding) is
listed, sorted and shuffled with ``random.Random(20261110)``; per class, in that order, a mutant is
taken when the visible tests pass with it (``pytest -q -x``) and the hidden probe
(``h10_probe.py``, written before the selection) prints something else than on the baseline (a
regression, not an equivalent mutant), five per class, at most 80 candidates tried per class. Five
neutral controls (``return X`` -> ``return (X)``) come from the same draw. The selection is
written to ``DIR/h10-selection.json`` and reused while the archive, the seed, the probe and this
script do not change.

Conditions: ``direct-equivalent`` (D*), ``harness`` (``harness init``) and ``harness-full`` (the
same with ``friction.fastLane.mode: off`` and ``governance.riskProfile: full``: the fast lane of
``init`` skips mutation). The
criterion is one of honesty: the share of regressions that escape both is reported, with no
threshold in favour.

The archive is read from ``$EVAL_CACHE`` or ``<work>/cache`` (downloaded there when missing).

Usage:
  python evaluation/deterministic/h10_uncovered_regressions.py --out DIR [--harness PATH]
      [--work DIR] [--project-venv DIR] [--only id,...] [--conditions a,b] [--reps 1]
      [--summarize-only] [--max-candidates 400]

``--max-candidates`` is the cap of candidates tried per class; its default, 400, is the one of
the amendment of 2026-11-10. The registered selection (cap 80) is its prefix and is written apart
to ``DIR/h10-selection.json``; the widened one goes to ``DIR/h10-selection-wide.json``.

Output: DIR/environment.json, DIR/h10-selection.json, DIR/h10-uncovered-regressions.jsonl (one
record per case, condition and repetition), DIR/h10-uncovered-regressions-summary.json,
DIR/configs/<condition>.yaml.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interception  # noqa: E402
from common import HERE, sha256_file, write_json  # noqa: E402

ARCHIVE = "itsdangerous-2.2.0.tar.gz"
ARCHIVE_URL = "https://github.com/pallets/itsdangerous/archive/refs/tags/2.2.0.tar.gz"
ARCHIVE_SHA256 = "7b0c6d4186e963b88489b69603b7ab2bf7c8e9eb4135a7b13b5f21bd4b937f2b"
SEED = 20261110
PER_CLASS = 5
MAX_TRIED = 80
"""The cap of candidates tried per class as first registered."""
WIDE_TRIED = 400
"""The cap of the amendment of 2026-11-10 (PREREGISTRO.md, H10): larger than the biggest
class, so every class is walked whole; the registered selection is its prefix."""
CONTROLS = 5
CLASSES = ("off-by-one", "exception", "order", "encoding")
PROBE = HERE / "h10_probe.py"
SELECTION = "h10-selection.json"
WIDE_SELECTION = "h10-selection-wide.json"
SIGNAL_PREFIXES = ("ratchet.", "differential.", "principles.", "tests.")
EXCEPTIONS = (
    "BadData",
    "BadSignature",
    "BadTimeSignature",
    "SignatureExpired",
    "BadHeader",
    "BadPayload",
    "ValueError",
    "TypeError",
)
ENCODINGS = ("utf-8", "latin-1", "ascii")
ENCODING_ALIASES = {"utf-8", "utf8", "latin-1", "latin1", "ascii"}
BASE64_SWAPS = {
    "urlsafe_b64encode": "b64encode",
    "b64encode": "urlsafe_b64encode",
    "urlsafe_b64decode": "b64decode",
    "b64decode": "urlsafe_b64decode",
}
PYTEST_PATH = 'pythonpath = ["src"]'

_state: dict[str, Any] = {"archive": None}


# ----- the project ------------------------------------------------------------------------------
def cache_dir(work: Path) -> Path:
    return Path(os.environ["EVAL_CACHE"]) if os.environ.get("EVAL_CACHE") else work / "cache"


def fetch_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ARCHIVE
    if not archive.exists():
        urllib.request.urlretrieve(ARCHIVE_URL, archive)  # noqa: S310 - fixed https URL
    found = sha256_file(archive)
    if found != ARCHIVE_SHA256:
        archive.unlink()
        raise SystemExit(f"{ARCHIVE} has SHA-256 {found}, expected {ARCHIVE_SHA256}")
    return archive


def build_baseline(root: Path) -> None:
    """The archive's tree, with ``pythonpath = ["src"]`` added to the pytest options of its
    ``pyproject.toml`` (PREREGISTRO.md, H10: the shared venv is not touched)."""
    archive = _state["archive"]
    if archive is None:
        raise RuntimeError("the itsdangerous archive was not prepared")
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as handle:
        for member in handle.getmembers():
            member.name = member.name.split("/", 1)[1] if "/" in member.name else ""
            if member.name:
                handle.extract(member, root, filter="data")
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    header = "[tool.pytest.ini_options]\n"
    if header not in text:
        raise RuntimeError("pyproject.toml has no [tool.pytest.ini_options]")
    interception.write_text(pyproject, text.replace(header, header + PYTEST_PATH + "\n", 1))


# ----- mutation sites ---------------------------------------------------------------------------
class Source:
    """A file's text with byte offsets per line (``ast`` columns are UTF-8 byte offsets)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.encode("utf-8").splitlines(keepends=True)
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))
        self.data = text.encode("utf-8")

    def offset(self, line: int, col: int) -> int:
        return self.starts[line - 1] + col

    def span(self, node: ast.AST) -> tuple[int, int]:
        return (
            self.offset(node.lineno, node.col_offset),  # type: ignore[attr-defined]
            self.offset(node.end_lineno, node.end_col_offset),  # type: ignore[attr-defined]
        )

    def segment(self, node: ast.AST) -> str:
        start, end = self.span(node)
        return self.data[start:end].decode("utf-8")

    def replaced(self, start: int, end: int, text: str) -> str:
        return (self.data[:start] + text.encode("utf-8") + self.data[end:]).decode("utf-8")


def site(
    path: str, node: ast.AST, cls: str, operator: str, start: int, end: int, text: str
) -> dict[str, Any]:
    return {
        "file": path,
        "line": node.lineno,  # type: ignore[attr-defined]
        "col": node.col_offset,  # type: ignore[attr-defined]
        "class": cls,
        "operator": operator,
        "start": start,
        "end": end,
        "text": text,
    }


_COMPARE_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}


def _docstrings(tree: ast.Module) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def sites(path: str, text: str) -> Iterator[dict[str, Any]]:
    """Every mutation site of one file, of the four classes and the neutral control."""
    source = Source(text)
    tree = ast.parse(text)
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        yield from _off_by_one(path, source, node)
        yield from _exception(path, source, node)
        yield from _order(path, source, node)
        yield from _encoding(path, source, node, docstrings)
        if isinstance(node, ast.Return) and node.value is not None:
            start, end = source.span(node.value)
            yield site(
                path,
                node,
                "control",
                "return-parenthesized",
                start,
                end,
                "(" + source.segment(node.value) + ")",
            )


def _off_by_one(path: str, source: Source, node: ast.AST) -> Iterator[dict[str, Any]]:
    if isinstance(node, ast.Compare):
        for index, op in enumerate(node.ops):
            swap = _COMPARE_SWAPS.get(type(op))
            if swap is None:
                continue
            mutated = ast.Compare(
                left=node.left,
                ops=[swap() if i == index else o for i, o in enumerate(node.ops)],
                comparators=node.comparators,
            )
            start, end = source.span(node)
            yield site(
                path,
                node,
                "off-by-one",
                f"{type(op).__name__}->{swap.__name__}",
                start,
                end,
                "(" + ast.unparse(mutated) + ")",
            )
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        start, end = source.span(node)
        for delta in (1, -1):
            yield site(
                path, node, "off-by-one", f"int{delta:+d}", start, end, repr(node.value + delta)
            )


def _exception(path: str, source: Source, node: ast.AST) -> Iterator[dict[str, Any]]:
    names: list[ast.Name] = []
    if isinstance(node, ast.Raise) and node.exc is not None:
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(target, ast.Name):
            names.append(target)
    if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
        names.append(node.type)
    for name in names:
        if name.id not in EXCEPTIONS:
            continue
        start, end = source.span(name)
        for other in EXCEPTIONS:
            if other != name.id:
                kind = "raise" if isinstance(node, ast.Raise) else "except"
                yield site(path, name, "exception", f"{kind}:{name.id}->{other}", start, end, other)


def _swap(source: Source, first: ast.AST, second: ast.AST) -> tuple[int, int, str]:
    a_start, a_end = source.span(first)
    b_start, b_end = source.span(second)
    between = source.data[a_end:b_start].decode("utf-8")
    return a_start, b_end, source.segment(second) + between + source.segment(first)


def _order(path: str, source: Source, node: ast.AST) -> Iterator[dict[str, Any]]:
    pairs: list[tuple[str, ast.AST, ast.AST]] = []
    if isinstance(node, ast.Call):
        positional = [arg for arg in node.args if not isinstance(arg, ast.Starred)]
        if len(positional) == len(node.args) and len(positional) >= 2:
            pairs.append(("call-arguments", positional[0], positional[1]))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub | ast.Add):
        pairs.append((f"operands-{type(node.op).__name__}", node.left, node.right))
    if (
        isinstance(node, ast.Tuple | ast.List)
        and len(node.elts) >= 2
        and not any(isinstance(item, ast.Starred) for item in node.elts[:2])
    ):
        pairs.append(("elements", node.elts[0], node.elts[1]))
    for operator, first, second in pairs:
        if source.segment(first) == source.segment(second):
            continue
        start, end, text = _swap(source, first, second)
        yield site(path, first, "order", operator, start, end, text)


def _encoding(
    path: str, source: Source, node: ast.AST, docstrings: set[int]
) -> Iterator[dict[str, Any]]:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ):
        start, end = source.span(node)
        if node.value.lower() in ENCODING_ALIASES:
            for other in ENCODINGS:
                if other.replace("-", "") != node.value.lower().replace("-", ""):
                    yield site(
                        path, node, "encoding", f"{node.value}->{other}", start, end, repr(other)
                    )
        if node.value == "strict":
            yield site(path, node, "encoding", "strict->replace", start, end, repr("replace"))
    if isinstance(node, ast.Attribute) and node.attr in BASE64_SWAPS:
        start, end = source.span(node)
        new = BASE64_SWAPS[node.attr]
        text = source.segment(node)
        yield site(
            path,
            node,
            "encoding",
            f"{node.attr}->{new}",
            start,
            end,
            text[: len(text) - len(node.attr)] + new,
        )


def all_sites(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted((root / "src" / "itsdangerous").glob("*.py")):
        relative = path.relative_to(root).as_posix()
        found.extend(sites(relative, path.read_text(encoding="utf-8")))
    return sorted(found, key=lambda s: (s["file"], s["line"], s["col"], s["operator"], s["text"]))


# ----- selection --------------------------------------------------------------------------------
def code_digest() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__), PROBE):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def run_python(python: Path, args: list[str], cwd: Path, env: dict[str, str], timeout: int) -> Any:
    try:
        return subprocess.run(
            [str(python), *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def probe(python: Path, root: Path, env: dict[str, str]) -> str | None:
    proc = run_python(python, [str(PROBE)], root, {**env, "PYTHONPATH": str(root / "src")}, 300)
    return proc.stdout if proc is not None and proc.returncode == 0 else None


def visible_pass(python: Path, root: Path, env: dict[str, str]) -> bool:
    proc = run_python(
        python, ["-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], root, env, 900
    )
    return proc is not None and proc.returncode == 0


def covered_lines(python: Path, root: Path, env: dict[str, str]) -> dict[str, list[int]] | None:
    """The lines of ``src/itsdangerous`` the visible tests execute on the baseline."""
    data = root / ".h10-coverage"
    run = run_python(
        python,
        [
            "-m",
            "coverage",
            "run",
            f"--data-file={data}",
            "--source=src/itsdangerous",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        root,
        env,
        1800,
    )
    report = root / ".h10-coverage.json"
    if run is None or run.returncode != 0:
        return None
    out = run_python(
        python, ["-m", "coverage", "json", f"--data-file={data}", "-o", str(report)], root, env, 300
    )
    if out is None or out.returncode != 0:
        return None
    files = json.loads(report.read_text(encoding="utf-8")).get("files") or {}
    data.unlink(missing_ok=True)
    report.unlink(missing_ok=True)
    return {
        Path(name).as_posix(): sorted(value.get("executed_lines") or [])
        for name, value in files.items()
    }


def select(work: Path, project_venv: Path, cap: int) -> dict[str, Any]:
    """Draw the cases (PREREGISTRO.md, H10) trying at most ``cap`` candidates per class.
    Deterministic for one archive, seed, cap and code. Every draw is listed, so the selection
    with a smaller cap is its prefix (``prefix``)."""
    root = work / "h10-selection"
    if root.exists():
        shutil.rmtree(root)
    build_baseline(root)
    python = interception.venv_python(project_venv)
    # No bytecode cache: a mutant of the same size written in the same second as the file it
    # replaces would otherwise be run from the stale .pyc of the previous one (mtime and size
    # are what the cache checks).
    env = {
        **interception.harness_env(project_venv, work / "h10-selection-state"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    baseline_probe = probe(python, root, env)
    baseline_pass = visible_pass(python, root, env)
    if baseline_probe is None or not baseline_pass:
        raise SystemExit(
            "H10 apparatus: the baseline's visible tests or the probe do not pass "
            f"(tests pass: {baseline_pass}, probe ran: {baseline_probe is not None})"
        )
    coverage = covered_lines(python, root, env)
    candidates = all_sites(root)
    order = list(candidates)
    random.Random(SEED).shuffle(order)
    texts = {s["file"]: (root / s["file"]).read_text(encoding="utf-8") for s in candidates}
    chosen: list[dict[str, Any]] = []
    draws: list[list[Any]] = []
    tried: dict[str, int] = {}
    outcomes: dict[str, dict[str, int]] = {}
    for cls in (*CLASSES, "control"):
        want = CONTROLS if cls == "control" else PER_CLASS
        count = 0
        tried[cls] = 0
        outcomes[cls] = {"testsFail": 0, "equivalentByProbe": 0, "probeError": 0, "taken": 0}
        for item in (s for s in order if s["class"] == cls):
            if count >= want or tried[cls] >= cap:
                break
            tried[cls] += 1
            original = texts[item["file"]]
            mutated = Source(original).replaced(item["start"], item["end"], item["text"])
            try:
                ast.parse(mutated)
            except SyntaxError:
                outcomes[cls]["probeError"] += 1
                draws.append([cls, tried[cls], "probeError"])
                continue
            target = root / item["file"]
            interception.write_text(target, mutated)
            try:
                passes = visible_pass(python, root, env)
                output = probe(python, root, env) if passes else None
            finally:
                interception.write_text(target, original)
            print(
                "H10 draw",
                cls,
                tried[cls],
                item["operator"],
                f"{item['file']}:{item['line']}",
                "pass" if passes else "killed",
                flush=True,
            )
            if not passes:
                outcomes[cls]["testsFail"] += 1
                draws.append([cls, tried[cls], "testsFail"])
                continue
            differs = output != baseline_probe
            if output is None and cls != "control":
                # The probe crashed on the mutant: its behaviour changed (a regression).
                differs = True
            if cls == "control" and (output is None or differs):
                outcomes[cls]["probeError"] += 1
                draws.append([cls, tried[cls], "probeError"])
                continue
            if cls != "control" and not differs:
                outcomes[cls]["equivalentByProbe"] += 1
                draws.append([cls, tried[cls], "equivalentByProbe"])
                continue
            count += 1
            outcomes[cls]["taken"] += 1
            draws.append([cls, tried[cls], "taken"])
            lines = (coverage or {}).get(item["file"])
            chosen.append(
                {
                    **{
                        k: item[k]
                        for k in (
                            "file",
                            "line",
                            "col",
                            "class",
                            "operator",
                            "start",
                            "end",
                            "text",
                        )
                    },
                    "id": f"{'ct' if cls == 'control' else cls[:3]}-{count}",
                    "drawIndex": order.index(item),
                    "triedAt": tried[cls],
                    "lineCovered": None if lines is None else item["line"] in lines,
                    "probeCrashed": output is None,
                }
            )
    shutil.rmtree(root, ignore_errors=True)
    return {
        "archiveSha256": ARCHIVE_SHA256,
        "seed": SEED,
        "codeDigest": code_digest(),
        "perClass": PER_CLASS,
        "maxTriedPerClass": cap,
        "candidates": {
            c: sum(1 for s in candidates if s["class"] == c) for c in (*CLASSES, "control")
        },
        "tried": tried,
        "outcomes": outcomes,
        "coverageAvailable": coverage is not None,
        "draws": draws,
        "cases": chosen,
    }


def prefix(selection: dict[str, Any], cap: int) -> dict[str, Any]:
    """The selection a smaller ``cap`` gives: the draws tried within it (same seed and order)."""
    draws = [d for d in selection["draws"] if d[1] <= cap]
    classes = (*CLASSES, "control")
    outcomes = {
        cls: {
            name: sum(1 for d in draws if d[0] == cls and d[2] == name)
            for name in ("testsFail", "equivalentByProbe", "probeError", "taken")
        }
        for cls in classes
    }
    return {
        **selection,
        "maxTriedPerClass": cap,
        "tried": {cls: max([d[1] for d in draws if d[0] == cls], default=0) for cls in classes},
        "outcomes": outcomes,
        "draws": draws,
        "cases": [c for c in selection["cases"] if c["triedAt"] <= cap],
    }


def load_selection(
    out: Path, work: Path, project_venv: Path, summarize_only: bool, cap: int
) -> dict[str, Any]:
    """The draw with ``cap`` (the amendment's, ``WIDE_SELECTION``, when larger than the
    registered cap); the registered selection is written apart (``SELECTION``) as its prefix."""
    path = out / (WIDE_SELECTION if cap > MAX_TRIED else SELECTION)
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        current = saved.get("codeDigest") == code_digest() and saved.get("seed") == SEED
        if summarize_only or (current and saved.get("maxTriedPerClass") == cap):
            return saved
    if summarize_only:
        raise SystemExit(f"--summarize-only needs {path}")
    selection = select(work, project_venv, cap)
    write_json(path, selection)
    if cap > MAX_TRIED:
        write_json(out / SELECTION, prefix(selection, MAX_TRIED))
    return selection


# ----- the suite --------------------------------------------------------------------------------
def original_text(path: str) -> str:
    """The text of ``path`` in the archive (its first path component dropped)."""
    with tarfile.open(_state["archive"]) as handle:
        for member in handle.getmembers():
            if "/" in member.name and member.name.split("/", 1)[1] == path:
                extracted = handle.extractfile(member)
                if extracted is not None:
                    return extracted.read().decode("utf-8")
    raise RuntimeError(f"{path} is not in the archive")


def to_case(item: dict[str, Any]) -> dict[str, Any]:
    control = item["class"] == "control"
    write: dict[str, str] = {}
    if _state["archive"] is not None:
        write[item["file"]] = Source(original_text(item["file"])).replaced(
            item["start"], item["end"], item["text"]
        )
    return {
        "id": item["id"],
        "kind": "clean" if control else "seeded",
        "group": item["class"],
        "rule": None,
        "set": "itsdangerous",
        "description": f"{item['operator']} at {item['file']}:{item['line']}",
        "lineCovered": item.get("lineCovered"),
        "inOriginal": item.get("triedAt", 0) <= MAX_TRIED,
        "write": write,
        "delete": [],
    }


def task_for(item: dict[str, Any], condition: str) -> dict[str, Any]:
    return {
        "taskId": "task_h10_" + item["id"].replace("-", "_"),
        "title": f"Tidy the internals of itsdangerous ({item['id']})",
        "intent": "Tidy the internal code of itsdangerous without changing its behaviour.",
        "acceptanceCriteria": [
            {
                "criterionId": "ac_unchanged",
                "text": "The public behaviour of itsdangerous is unchanged and its test suite passes.",
            }
        ],
    }


def signal_rules(findings: list[dict[str, Any]]) -> list[str]:
    return sorted({f["rule"] for f in findings if f["rule"].startswith(SIGNAL_PREFIXES)})


def detect(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    return bool(signal_rules(findings))


def full_lane(config: dict[str, Any]) -> dict[str, Any]:
    friction = config.setdefault("friction", {})
    lane = friction.get("fastLane")
    friction["fastLane"] = {**(lane if isinstance(lane, dict) else {}), "mode": "off"}
    # 2.0.1: under governance.riskProfile auto the light profile exists without a fast lane;
    # only ``full`` leaves every task on the full flow (PREREGISTRO.md, 2.0.1 amendment).
    config.setdefault("governance", {})["riskProfile"] = "full"
    return config


def make_suite(cases: list[dict[str, Any]], selection: dict[str, Any]) -> interception.Suite:
    return interception.Suite(
        suite_id="H10",
        title="regressions the suite does not cover (neutrality)",
        build_baseline=build_baseline,
        cases=cases,
        conditions={
            interception.DIRECT: None,
            "harness": interception.init_config,
            "harness-full": full_lane,
        },
        task_for=task_for,
        detect=detect,
        rules=(),
        relevant=SIGNAL_PREFIXES,
        notes={
            "selection": {k: v for k, v in selection.items() if k not in ("cases", "draws")},
            "original": f"neutrality: the cases drawn within the registered cap of {MAX_TRIED}; neutralityWidened: every case, the widened draw of the amendment",
            "signal": "a finding of a rule ratchet.*, differential.*, principles.* or tests.*, any severity (CERTIFICATION_NOT_CERTIFIED reported apart)",
            "escape": "delivered by D* (visible tests pass) and by H (not blocked)",
            "harness-full": "harness init with friction.fastLane.mode: off and governance.riskProfile: full (init's fast lane skips mutation)",
            "hidden": "the hidden tests of the brownfield scenario test its task's feature, absent from the baseline; the oracle is h10_probe.py (PREREGISTRO.md, deviation)",
        },
    )


def summarizer(suite: interception.Suite) -> Any:
    def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
        summary = interception.summarize(suite, records)
        first = [r for r in records if r["rep"] == 1 and "apparatusError" not in r]
        direct = {r["case"]: r["delivered"] for r in first if r["condition"] == interception.DIRECT}
        covered = {c["id"]: c.get("lineCovered") for c in suite.cases}
        original = {c["id"] for c in suite.cases if c.get("inOriginal")}
        summary["neutrality"] = neutrality_of(suite, first, direct, covered, original)
        summary["neutralityWidened"] = neutrality_of(
            suite, first, direct, covered, {c["id"] for c in suite.cases}
        )
        summary["directDeliveredRegressions"] = interception.proportion(
            sum(bool(direct.get(c["id"])) for c in suite.cases if c["kind"] == "seeded"),
            sum(1 for c in suite.cases if c["kind"] == "seeded" and c["id"] in direct),
        )
        return summary

    return summarize


def neutrality_of(
    suite: interception.Suite,
    first: list[dict[str, Any]],
    direct: dict[str, bool],
    covered: dict[str, Any],
    cases: set[str],
) -> dict[str, Any]:
    """The neutrality metrics per governed condition over ``cases``."""
    neutrality: dict[str, Any] = {}
    for condition, builder in suite.conditions.items():
        rows = [r for r in first if r["condition"] == condition and r["case"] in cases]
        if builder is None or not rows:
            continue
        regressions = [r for r in rows if r["kind"] == "seeded"]
        controls = [r for r in rows if r["kind"] == "clean"]

        def escaped(r: dict[str, Any]) -> bool:
            return bool(direct.get(r["case"])) and not r["blocked"]

        def rules_count(items: list[dict[str, Any]]) -> dict[str, int]:
            counts: dict[str, int] = {}
            for r in items:
                for rule in signal_rules(r["findings"]):
                    counts[rule] = counts.get(rule, 0) + 1
            return dict(sorted(counts.items()))

        uncovered = [r for r in regressions if covered.get(r["case"]) is False]
        neutrality[condition] = {
            "regressionsDeliveredByHarness": interception.proportion(
                sum(not r["blocked"] for r in regressions), len(regressions)
            ),
            "escapedBoth": interception.proportion(
                sum(escaped(r) for r in regressions), len(regressions)
            ),
            "escapedBothByClass": {
                cls: interception.proportion(
                    sum(escaped(r) for r in regressions if r["group"] == cls),
                    sum(1 for r in regressions if r["group"] == cls),
                )
                for cls in CLASSES
            },
            "escapedBothUncoveredLines": interception.proportion(
                sum(escaped(r) for r in uncovered), len(uncovered)
            ),
            "withSignal": {
                "regressions": interception.proportion(
                    sum(r["detected"] for r in regressions), len(regressions)
                ),
                "controls": interception.proportion(
                    sum(r["detected"] for r in controls), len(controls)
                ),
            },
            "signalRules": {
                "regressions": rules_count(regressions),
                "controls": rules_count(controls),
            },
            "ratchetRegressed": sum(
                any(f["rule"] == "ratchet.regressed" for f in r["findings"]) for r in rows
            ),
            "certificationNotCertified": {
                "regressions": sum(
                    "CERTIFICATION_NOT_CERTIFIED" in r["gateReasons"] for r in regressions
                ),
                "controls": sum(
                    "CERTIFICATION_NOT_CERTIFIED" in r["gateReasons"] for r in controls
                ),
            },
            "controlsBlocked": interception.proportion(
                sum(r["blocked"] for r in controls), len(controls)
            ),
            "blockedRegressions": sorted(r["case"] for r in regressions if r["blocked"]),
            "lanes": sorted({str((r.get("lanes") or {}).get("classified")) for r in rows}),
        }
    return neutrality


def criteria(summary: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered criterion (PREREGISTRO.md, H10): report, no threshold in favour."""
    neutrality = summary.get("neutrality") or {}
    widened = summary.get("neutralityWidened") or {}
    return {
        "statement": "report the share of regressions that escape both D* and H; no threshold in favour; on the registered selection (cap 80) and, per the amendment of 2026-11-10, on the widened one (cap 400), reported apart",
        "harnessEscapedBoth": (neutrality.get("harness") or {}).get("escapedBoth"),
        "harnessFullEscapedBoth": (neutrality.get("harness-full") or {}).get("escapedBoth"),
        "widened": {
            "harnessEscapedBoth": (widened.get("harness") or {}).get("escapedBoth"),
            "harnessFullEscapedBoth": (widened.get("harness-full") or {}).get("escapedBoth"),
        },
        "met": None,
        "evaluable": bool(neutrality.get("harness")),
    }


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--out", type=Path)
    pre.add_argument("--work", type=Path, default=None)
    pre.add_argument("--project-venv", type=Path, default=Path(sys.executable).parent.parent)
    pre.add_argument("--summarize-only", action="store_true")
    pre.add_argument("--max-candidates", type=int, default=WIDE_TRIED)
    known, _ = pre.parse_known_args()
    # --max-candidates is this suite's own option: the shared command line does not know it.
    argv = list(sys.argv)
    for index, value in enumerate(argv):
        if value == "--max-candidates":
            del argv[index : index + 2]
            break
        if value.startswith("--max-candidates="):
            del argv[index]
            break
    sys.argv[:] = argv
    if known.out is None or "-h" in sys.argv or "--help" in sys.argv:
        suite = make_suite([], {})
        return interception.main(
            suite, __doc__ or "", "h10-uncovered-regressions-summary.json", criteria
        )
    out = known.out.resolve()
    work = (known.work or out / ".work").resolve()
    work.mkdir(parents=True, exist_ok=True)
    if not known.summarize_only:
        _state["archive"] = fetch_archive(cache_dir(work))
    if known.max_candidates < MAX_TRIED:
        raise SystemExit(f"--max-candidates must be at least the registered cap, {MAX_TRIED}")
    selection = load_selection(
        out, work, known.project_venv.resolve(), known.summarize_only, known.max_candidates
    )
    suite = make_suite([to_case(item) for item in selection["cases"]], selection)
    return interception.main(
        suite,
        __doc__ or "",
        "h10-uncovered-regressions-summary.json",
        criteria,
        summarizer=summarizer(suite),
    )


if __name__ == "__main__":
    raise SystemExit(main())
