"""Requirement traceability: relate each identified requirement of a task to the tests that name it.

The check is deterministic and reads the workspace without executing anything. A requirement is
identified by the token that starts its text (``A1. ...``, ``[B12] ...``, ``X8: ...``) or, when the
text has none, by a ``requirementId`` that the task file set explicitly. Generated ids
(``req_<32 hex digits>``) do not identify a requirement, and requirements without an identifier are
skipped and counted.

A requirement is traced when a test names it: a test file, class or function whose name contains the
identifier as a token in any case (``test_a1_rounding``, ``test_A1``, ``TestA1``), or the identifier
as a whole word in a test's source, docstring or string constants (``pytest.param(..., id="A1")``,
``# covers A1``). Python test files are read with :mod:`ast`; a file that does not parse, and every
Node test file, is read as text.
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field

from governed_harness.domain.enums import (
    ActorType,
    ErrorKind,
    FindingSeverity,
    ResultStatus,
    ValidationKind,
)
from governed_harness.domain.ids import new_id
from governed_harness.domain.models import (
    Actor,
    Finding,
    HarnessErrorRecord,
    Requirement,
    StrictModel,
    ValidationResult,
    utc_now,
)
from governed_harness.evidence.artifact_store import ArtifactRef
from governed_harness.validators.base import ValidationContext, ValidatorOutput

TRACEABILITY_VALIDATOR_ID = "traceability.requirements"
UNTESTED_RULE_ID = "traceability.requirement-untested"

_IDENTIFIER = r"[A-Z]{1,3}\d{1,3}(?:\.\d+)?"
REQUIREMENT_TOKEN = re.compile(
    rf"^\s*(?:\[(?P<bracketed>{_IDENTIFIER})\][.:)]?|(?P<plain>{_IDENTIFIER})[.:)])\s"
)
"""The identifier that starts a requirement's text: ``A1. ``, ``[B12] ``, ``X8: ``, ``C3.1) ``."""

GENERATED_REQUIREMENT_ID = re.compile(r"^req_[0-9a-f]{32}$")
"""The shape of the id the task loader generates when the task file gives none."""

PYTHON_TEST_FILE = re.compile(r"^(?:test_.*|.*_test)\.py$")
NODE_TEST_FILE = re.compile(
    r"^(?:.*[.\-_](?:test|spec)|test-.*|test)\.(?:[cm]?js|[cm]?ts|jsx|tsx)$"
)
NODE_SOURCE_FILE = re.compile(r"\.(?:[cm]?js|[cm]?ts|jsx|tsx)$")
NODE_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__"})
PRUNED_DIRECTORIES = frozenset(
    {"node_modules", "__pycache__", "venv", "site-packages", "build", "dist"}
)
MAX_TEST_FILE_BYTES = 2_000_000
EXCERPT_CHARS = 80

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAME_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")
_NODE_TEST_TITLE = re.compile(
    r"\b(?:test|it|describe)(?:\.\w+)?\s*\(\s*(?P<quote>['\"`])(?P<title>(?:\\.|(?!(?P=quote)).)*)(?P=quote)"
)

IdentifierSource = Literal["text", "requirementId"]
MatchKind = Literal["name", "string", "text"]
TraceabilityPolicy = Literal["enforce", "warn"]


class TracedTest(StrictModel):
    """A test that names a requirement, and how it names it."""

    node_id: str
    path: str
    match: MatchKind


class RequirementTrace(StrictModel):
    requirement_id: str
    identifier: str
    identifier_source: IdentifierSource
    excerpt: str
    traced: bool
    tests: tuple[TracedTest, ...] = ()


class RequirementTraceabilityReport(StrictModel):
    """VERIFICATION evidence: the requirement to test mapping of one ChangeSet."""

    schema_version: Literal["1.0"] = "1.0"
    execution_id: str
    task_id: str
    change_set_digest: str
    policy: TraceabilityPolicy
    technologies: tuple[str, ...]
    test_files: tuple[str, ...]
    unread_files: tuple[str, ...] = ()
    requirements: tuple[RequirementTrace, ...]
    skipped_requirement_ids: tuple[str, ...] = ()
    traced_count: int = Field(ge=0)
    untraced_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


@dataclass(frozen=True)
class RequirementIdentifier:
    requirement: Requirement
    identifier: str
    source: IdentifierSource


@dataclass(frozen=True)
class TestNode:
    """A unit that can name a requirement: a test file, class or function."""

    node_id: str
    path: str
    names: tuple[str, ...]
    strings: tuple[str, ...] = ()
    text: str = ""


@dataclass(frozen=True)
class TestCorpus:
    files: tuple[str, ...]
    unread: tuple[str, ...]
    nodes: tuple[TestNode, ...]


@dataclass(frozen=True)
class TraceabilityOutput(ValidatorOutput):
    """A validator output that also carries the stored report, so it can be recorded as evidence."""

    report: RequirementTraceabilityReport | None = None
    report_ref: ArtifactRef | None = None


# ----- identifiers ---------------------------------------------------------------------------
def requirement_identifier(requirement: Requirement) -> RequirementIdentifier | None:
    """The identifier of a requirement, or ``None`` when it has none to check."""
    match = REQUIREMENT_TOKEN.match(requirement.text)
    if match:
        token = match.group("bracketed") or match.group("plain")
        return RequirementIdentifier(requirement, token, "text")
    explicit = requirement.requirement_id.strip()
    if explicit and not GENERATED_REQUIREMENT_ID.fullmatch(explicit):
        return RequirementIdentifier(requirement, explicit, "requirementId")
    return None


def name_tokens(name: str) -> tuple[str, ...]:
    """Lower-case tokens of a file, class or function name: ``TestA1Rounding`` and
    ``test_a1_rounding`` both give ``("test", "a1", "rounding")``."""
    spaced = _CAMEL_BOUNDARY.sub("_", name)
    return tuple(part.lower() for part in _NAME_SEPARATORS.split(spaced) if part)


def name_mentions(name: str, identifier: str) -> bool:
    """Whether ``identifier`` is a run of whole tokens of ``name``."""
    wanted = name_tokens(identifier)
    tokens = name_tokens(name)
    if not wanted:
        return False
    width = len(wanted)
    return any(tokens[index : index + width] == wanted for index in range(len(tokens) - width + 1))


def word_pattern(identifier: str) -> re.Pattern[str]:
    """``identifier`` as a whole word: ``A1`` matches ``A1.`` and ``[A1]`` but not ``A12``,
    ``A1.2`` or ``a1``."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_]|\.\d)")


# ----- test discovery ------------------------------------------------------------------------
def is_test_file(relative: PurePosixPath, technologies: Iterable[str]) -> bool:
    selected = set(technologies)
    name = relative.name
    if "python" in selected and PYTHON_TEST_FILE.match(name):
        return True
    if "node" in selected and NODE_SOURCE_FILE.search(name):
        if NODE_TEST_FILE.match(name):
            return True
        return any(part in NODE_TEST_DIRECTORIES for part in relative.parts[:-1])
    return False


def discover_test_files(workspace: Path, technologies: Iterable[str]) -> tuple[str, ...]:
    """Workspace-relative POSIX paths of the test files, sorted. Hidden directories, dependency
    and build directories and symbolic links are not followed."""
    selected = tuple(technologies)
    found: list[str] = []
    for current, directories, files in os.walk(workspace):
        directories[:] = sorted(
            item
            for item in directories
            if not item.startswith(".")
            and item not in PRUNED_DIRECTORIES
            and not (Path(current) / item).is_symlink()
        )
        for filename in files:
            path = Path(current) / filename
            if path.is_symlink():
                continue
            relative = PurePosixPath(path.relative_to(workspace).as_posix())
            if is_test_file(relative, selected):
                found.append(relative.as_posix())
    return tuple(sorted(found))


def load_test_corpus(workspace: Path, technologies: Iterable[str]) -> TestCorpus:
    files = discover_test_files(workspace, technologies)
    unread: list[str] = []
    nodes: list[TestNode] = []
    for relative in files:
        path = workspace / relative
        try:
            if path.stat().st_size > MAX_TEST_FILE_BYTES:
                unread.append(relative)
                continue
            source = path.read_bytes().decode("utf-8", "replace")
        except OSError:
            unread.append(relative)
            continue
        if relative.endswith(".py"):
            nodes.extend(python_test_nodes(relative, source))
        else:
            nodes.extend(node_test_nodes(relative, source))
    return TestCorpus(files=files, unread=tuple(unread), nodes=tuple(nodes))


def python_test_nodes(relative: str, source: str) -> list[TestNode]:
    """The file, its ``Test*`` classes and its ``test*`` functions. A file that does not parse is
    one node whose whole text is searched."""
    stem = PurePosixPath(relative).stem
    try:
        tree = ast.parse(source, filename=relative)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return [TestNode(node_id=relative, path=relative, names=(stem,), text=source)]
    lines = source.splitlines()
    docstring = ast.get_docstring(tree, clean=False)
    nodes = [
        TestNode(
            node_id=relative,
            path=relative,
            names=(stem,),
            strings=(docstring,) if docstring else (),
        )
    ]
    nodes.extend(_python_scope(relative, tree.body, lines, prefix=relative))
    return nodes


def _python_scope(
    relative: str,
    body: list[ast.stmt],
    lines: list[str],
    *,
    prefix: str,
) -> Iterator[TestNode]:
    for statement in body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            if statement.name.startswith("test"):
                yield TestNode(
                    node_id=f"{prefix}::{statement.name}",
                    path=relative,
                    names=(statement.name,),
                    strings=tuple(_string_constants(statement)),
                    text=_segment(statement, lines),
                )
        elif isinstance(statement, ast.ClassDef) and statement.name.startswith("Test"):
            node_id = f"{prefix}::{statement.name}"
            docstring = ast.get_docstring(statement, clean=False)
            yield TestNode(
                node_id=node_id,
                path=relative,
                names=(statement.name,),
                strings=((docstring,) if docstring else ())
                + tuple(
                    value
                    for decorator in statement.decorator_list
                    for value in _string_constants(decorator)
                ),
            )
            yield from _python_scope(relative, statement.body, lines, prefix=node_id)


def _string_constants(node: ast.AST) -> Iterator[str]:
    """Docstrings, parametrize ids and every other string constant of a test and its
    decorators."""
    roots: list[ast.AST] = [node]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        roots.extend(node.decorator_list)
    for root in roots:
        for child in ast.walk(root):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                yield child.value


def _segment(node: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]) -> str:
    """The source lines of a test, decorators and comments included."""
    first = min([node.lineno, *(item.lineno for item in node.decorator_list)])
    last = node.end_lineno or node.lineno
    return "\n".join(lines[first - 1 : last])


def node_test_nodes(relative: str, source: str) -> list[TestNode]:
    """The file (its name and whole text) and every ``test``, ``it`` or ``describe`` title."""
    nodes = [
        TestNode(
            node_id=relative,
            path=relative,
            names=(PurePosixPath(relative).name,),
            text=source,
        )
    ]
    for match in _NODE_TEST_TITLE.finditer(source):
        title = match.group("title")
        nodes.append(
            TestNode(node_id=f"{relative}::{title}", path=relative, names=(), strings=(title,))
        )
    return nodes


# ----- matching ------------------------------------------------------------------------------
def tests_naming(identifier: str, corpus: TestCorpus) -> tuple[TracedTest, ...]:
    """The tests of ``corpus`` that name ``identifier``, in corpus order, one entry per node."""
    pattern = word_pattern(identifier)
    traced: list[TracedTest] = []
    for node in corpus.nodes:
        kind: MatchKind | None = None
        if any(name_mentions(name, identifier) for name in node.names):
            kind = "name"
        elif any(pattern.search(value) for value in node.strings):
            kind = "string"
        elif node.text and pattern.search(node.text):
            kind = "text"
        if kind is not None:
            traced.append(TracedTest(node_id=node.node_id, path=node.path, match=kind))
    # A Node file's text contains its titles: keep the file only when no title of it matched.
    titled = {item.path for item in traced if item.node_id != item.path and item.match == "string"}
    return tuple(
        item
        for item in traced
        if not (item.node_id == item.path and item.match == "text" and item.path in titled)
    )


def trace_requirements(
    requirements: Iterable[Requirement], corpus: TestCorpus
) -> tuple[tuple[RequirementTrace, ...], tuple[str, ...]]:
    """The trace of every identified requirement and the ids of the skipped ones."""
    traces: list[RequirementTrace] = []
    skipped: list[str] = []
    for requirement in requirements:
        identified = requirement_identifier(requirement)
        if identified is None:
            skipped.append(requirement.requirement_id)
            continue
        tests = tests_naming(identified.identifier, corpus)
        traces.append(
            RequirementTrace(
                requirement_id=requirement.requirement_id,
                identifier=identified.identifier,
                identifier_source=identified.source,
                excerpt=excerpt(requirement.text),
                traced=bool(tests),
                tests=tests,
            )
        )
    return tuple(traces), tuple(skipped)


def excerpt(text: str) -> str:
    return " ".join(text.split())[:EXCERPT_CHARS]


# ----- validator -----------------------------------------------------------------------------
class RequirementTraceabilityValidator:
    """Report identified requirements that no test names.

    Under ``enforce`` each untraced requirement is a ``HIGH`` finding, which the default
    ``findingBlockSeverities`` turn into a ``FAILED`` gate; under ``warn`` it is ``LOW``. The
    validation result itself is ``PASSED`` when the check ran, like the independent review, so
    the run reaches the gate and the person deciding sees the findings."""

    validator_id = TRACEABILITY_VALIDATOR_ID

    def __init__(self, policy: TraceabilityPolicy, technologies: Iterable[str]) -> None:
        self.policy = policy
        self.technologies = tuple(sorted(set(technologies)))

    def execute(self, context: ValidationContext) -> TraceabilityOutput:
        actor = Actor(
            actor_type=ActorType.TOOL, actor_id=f"validator.{self.validator_id}", version="1"
        )
        provenance = context.provenance.model_copy(update={"actor": actor})
        started = datetime.now(UTC)
        mandatory = self.policy == "enforce"
        try:
            corpus = load_test_corpus(context.workspace, self.technologies)
        except OSError as error:
            summary = f"Requirement traceability could not read the workspace: {error}"
            evidence = context.artifact_store.put_json(
                {"validatorId": self.validator_id, "status": ResultStatus.ERROR, "reason": summary},
                metadata={"kind": "validator-availability"},
            )
            result = ValidationResult(
                validation_result_id=new_id("validation"),
                execution_id=context.execution_id,
                validator_id=self.validator_id,
                change_set_digest=context.change_set.digest,
                status=ResultStatus.ERROR,
                kind=ValidationKind.TOOL_ERROR,
                mandatory=mandatory,
                summary=summary,
                evidence_refs=(evidence.uri,),
                started_at=started,
                finished_at=datetime.now(UTC),
                errors=(
                    HarnessErrorRecord(
                        error_id=new_id("err"),
                        kind=ErrorKind.TOOL_ERROR,
                        message=summary,
                        actor=actor,
                    ),
                ),
                provenance=provenance,
            )
            return TraceabilityOutput(result)
        traces, skipped = trace_requirements(context.task.requirements, corpus)
        untraced = [item for item in traces if not item.traced]
        report = RequirementTraceabilityReport(
            execution_id=context.execution_id,
            task_id=context.task.task_id,
            change_set_digest=context.change_set.digest,
            policy=self.policy,
            technologies=self.technologies,
            test_files=corpus.files,
            unread_files=corpus.unread,
            requirements=traces,
            skipped_requirement_ids=skipped,
            traced_count=len(traces) - len(untraced),
            untraced_count=len(untraced),
            skipped_count=len(skipped),
        )
        report_ref = context.artifact_store.put_json(
            report.model_dump(mode="json", by_alias=True),
            metadata={"kind": "requirement-traceability", "validatorId": self.validator_id},
        )
        severity = FindingSeverity.HIGH if self.policy == "enforce" else FindingSeverity.LOW
        findings = tuple(
            Finding(
                finding_id=new_id("finding"),
                execution_id=context.execution_id,
                validator_id=self.validator_id,
                rule_id=UNTESTED_RULE_ID,
                category="traceability",
                severity=severity,
                message=f"No test names requirement {item.identifier}: {item.excerpt}",
                evidence_refs=(report_ref.uri,),
                recommendation=(
                    f"Add or rename a test so that it names {item.identifier}: a test, class or "
                    f"file name with the token {'_'.join(name_tokens(item.identifier))} "
                    f"(test_{'_'.join(name_tokens(item.identifier))}_...), or "
                    f"{item.identifier} as a word in the test's docstring or parametrize id."
                ),
                provenance=provenance,
            )
            for item in untraced
        )
        result = ValidationResult(
            validation_result_id=new_id("validation"),
            execution_id=context.execution_id,
            validator_id=self.validator_id,
            change_set_digest=context.change_set.digest,
            status=ResultStatus.PASSED,
            kind=ValidationKind.SUCCESS,
            mandatory=mandatory,
            summary=summarize(report),
            finding_ids=tuple(item.finding_id for item in findings),
            evidence_refs=(report_ref.uri,),
            started_at=started,
            finished_at=datetime.now(UTC),
            provenance=provenance,
        )
        return TraceabilityOutput(result, findings, (), report, report_ref)


def summarize(report: RequirementTraceabilityReport) -> str:
    identified = report.traced_count + report.untraced_count
    return (
        f"{report.traced_count} of {identified} identified requirement(s) traced to tests, "
        f"{report.untraced_count} untraced, {report.skipped_count} without an identifier skipped"
    )
