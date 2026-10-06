"""``harness review-code`` and the ``harness review`` subcommands outside governed runs (#57).

* ``review_code``: resolve the base (explicit, then the pull or merge request base the CI
  exposes, then the branch convention, then the merge base with the default branch; in hook mode
  the base is fetched first and a failed fetch aborts), take the diff (``manual`` and ``hook``:
  base..head; ``staged``: the index against ``HEAD``), run the panel and, when it passes, record
  the evidence as a Git ref and, when asked, one pull or merge request comment per result;
* ``verify_review``: check the evidence ref of a commit without calling any model (CI);
* ``install_hook``: a ``pre-push`` hook that runs ``harness review-code --mode hook``;
* ``rules_report`` and ``sync_rules``: the catalog and the generated blocks of the reviewers;
* ``review_variance``: the same review N times without cache, to measure its variance."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from governed_harness import __version__
from governed_harness.configuration.models import ResolvedConfiguration
from governed_harness.delivery.vcs import Git
from governed_harness.domain.enums import ActorType, PhaseId
from governed_harness.domain.errors import ConfigurationError, PolicyViolationError
from governed_harness.domain.models import Actor, Provenance
from governed_harness.evidence.hashing import sha256_bytes
from governed_harness.review.cache import CACHE_DIRECTORY, ReviewCache
from governed_harness.review.diff import diff_hash
from governed_harness.review.invoke import ProviderInvoker
from governed_harness.review.panel import PanelInputs, PanelReport, run_panel
from governed_harness.review.project import (
    ReviewSetup,
    consistency_runner,
    linter_runner,
    panel_context,
    review_setup,
    reviewer_route,
)
from governed_harness.review.providers import build_reviewer_provider, mcp_digest, mcp_servers
from governed_harness.review.reviewers import sync_blocks
from governed_harness.runtime.process_runner import SafeProcessRunner

MODES = ("hook", "manual", "staged")
REF_PREFIX = "refs/harness/review"
REF_KINDS = {"PASS": "pass", "PASS_WARN": "pass-warn"}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
HOOK_MARKER = "# governed-harness review hook (harness review hook install)"
PULL_REQUEST_BASE_ENV = (
    "GITHUB_BASE_REF",
    "CI_MERGE_REQUEST_TARGET_BRANCH_NAME",
    "BITBUCKET_PR_DESTINATION_BRANCH",
    "SYSTEM_PULLREQUEST_TARGETBRANCH",
)
"""Variables in which GitHub (and Gitea) Actions, GitLab CI, Bitbucket Pipelines and Azure
Pipelines expose the base branch of the pull or merge request being built."""
PULL_REQUEST_NUMBER_ENV = (
    "CI_MERGE_REQUEST_IID",
    "BITBUCKET_PR_ID",
    "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER",
    "SYSTEM_PULLREQUEST_PULLREQUESTID",
)
BUILTIN_CONVENTIONS: tuple[tuple[str, str], ...] = (
    ("release/*", "@default"),
    ("hotfix/*", "@default"),
    ("*", "develop"),
)
"""Branch conventions applied after ``review.panel.baseBranches``: release and hotfix branches
against the default branch, every other branch against ``develop`` when it exists."""
REVIEW_EXECUTION_PREFIX = "review"
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")


class ReviewAborted(PolicyViolationError):
    """A review that cannot run as asked (hook mode without its base)."""

    exit_code = 6


@dataclass(frozen=True)
class BaseResolution:
    ref: str
    sha: str
    source: str
    fetched: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "sha": self.sha, "source": self.source, "fetched": self.fetched}


# ----- base resolution --------------------------------------------------------------------------
def _ref_exists(git: Git, ref: str) -> bool:
    return git.resolve(ref) is not None


def _has_remote(git: Git, remote: str = "origin") -> bool:
    return git.run("remote", "get-url", remote, check=False).returncode == 0


def default_branch(git: Git) -> str | None:
    head = git.run("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if head.returncode == 0:
        name = head.stdout.decode().strip().removeprefix("refs/remotes/origin/")
        if name:
            return name
    for candidate in ("main", "master", "trunk", "develop"):
        if _ref_exists(git, f"refs/remotes/origin/{candidate}") or _ref_exists(
            git, f"refs/heads/{candidate}"
        ):
            return candidate
    return None


def _convention(branch: str, configured: dict[str, str] | None, git: Git) -> str | None:
    import fnmatch

    for pattern, target in (*(configured or {}).items(), *BUILTIN_CONVENTIONS):
        if not fnmatch.fnmatchcase(branch, pattern):
            continue
        name = default_branch(git) if target == "@default" else target
        if (
            name
            and name != branch
            and (
                _ref_exists(git, f"refs/heads/{name}")
                or _ref_exists(git, f"refs/remotes/origin/{name}")
            )
        ):
            return name
    return None


def _fetch(git: Git, branch: str) -> None:
    result = git.run(
        "fetch",
        "--quiet",
        "--no-tags",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip() or "no message"
        raise ReviewAborted(
            f"hook mode: the base branch {branch} could not be fetched from origin ({message}); "
            "the review is aborted"
        )


def resolve_base(
    git: Git, mode: str, explicit: str | None, configured: dict[str, str] | None
) -> BaseResolution:
    """explicit, then the pull or merge request base, then the branch convention, then the
    merge base with the default branch."""
    if mode == "staged":
        head = git.resolve("HEAD")
        return BaseResolution("HEAD", head or EMPTY_TREE, "staged")
    candidates: list[tuple[str, str]] = []
    if explicit:
        candidates.append((explicit, "explicit"))
    for name in PULL_REQUEST_BASE_ENV:
        value = os.environ.get(name, "").strip().removeprefix("refs/heads/")
        if value:
            candidates.append((value, f"pull-request:{name}"))
            break
    branch = git.run("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.decode().strip()
    if branch and branch != "HEAD":
        convention = _convention(branch, configured, git)
        if convention:
            candidates.append((convention, "branch-convention"))
    fallback = default_branch(git)
    if fallback and fallback != branch:
        candidates.append((fallback, "merge-base"))
    for name, source in candidates:
        fetched = False
        is_branch = source != "explicit" or not _COMMIT.match(name)
        if mode == "hook" and is_branch:
            if not _has_remote(git):
                raise ReviewAborted(
                    "hook mode: there is no origin remote to fetch the base from; the review is "
                    "aborted"
                )
            _fetch(git, name)
            fetched = True
        ref = name
        if is_branch and _ref_exists(git, f"refs/remotes/origin/{name}"):
            ref = f"origin/{name}"
        if not _ref_exists(git, ref):
            if source == "explicit":
                raise ConfigurationError(f"base {name!r} does not resolve to a commit")
            continue
        merge = git.run("merge-base", "HEAD", ref, check=False)
        sha = merge.stdout.decode().strip()
        if merge.returncode != 0 or not sha:
            continue
        return BaseResolution(ref, sha, source, fetched)
    raise ConfigurationError(
        "no base to review against: pass --base, or create the default branch (main) or the "
        "convention's base branch"
    )


def _diff_args() -> tuple[str, ...]:
    return (
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "-U3",
    )


def collect_diff(git: Git, mode: str, base: BaseResolution, head_ref: str) -> tuple[str, str]:
    """The diff to review and the head it is bound to (a commit, or ``tree:SHA`` of the
    index)."""
    if mode == "staged":
        tree = git.text("write-tree")
        diff = git.run(*_diff_args(), "--cached", base.sha).stdout.decode("utf-8", "replace")
        return diff, f"tree:{tree}"
    head = git.resolve(head_ref)
    if head is None:
        raise ConfigurationError(f"head {head_ref!r} does not resolve to a commit")
    diff = git.run(*_diff_args(), base.sha, head).stdout.decode("utf-8", "replace")
    return diff, head


# ----- running the panel ------------------------------------------------------------------------
def _invoker(
    resolved: ResolvedConfiguration,
    services: Any,
    setup: ReviewSetup,
    execution_id: str,
) -> ProviderInvoker:
    workspace = resolved.workspace_root
    servers = mcp_servers(workspace, setup.settings.mcp_servers)
    runtime = resolved.project.runtime

    def build(provider_id: str) -> Any:
        built = build_reviewer_provider(
            resolved,
            provider_id,
            workspace=workspace,
            protected=(),
            allow_network=True,
        )
        return built if isinstance(built, str) else built.built

    from governed_harness.capabilities import grants_from_rules

    return ProviderInvoker(
        workspace=workspace,
        artifacts=services.artifacts,
        runner=SafeProcessRunner(workspace),
        provenance=Provenance(
            actor=Actor(actor_type=ActorType.HARNESS, actor_id="harness.review", version="1"),
            core_version=__version__,
        ),
        build=build,
        grants_for=lambda actor: grants_from_rules(
            execution_id, actor, resolved.effective_capabilities
        ),
        execution_id=execution_id,
        phase_id=PhaseId.INDEPENDENT_REVIEW,
        default_timeout=min(runtime.command_timeout_seconds, 3600),
        max_output_bytes=runtime.max_output_bytes,
        mcp_servers=servers,
    )


def panel_inputs(
    resolved: ResolvedConfiguration,
    services: Any,
    *,
    mode: str,
    diff_text: str,
    base: str | None,
    head: str | None,
    provider: str | None,
    fallback: str | None,
    model: str | None,
    skip: tuple[str, ...],
    use_cache: bool,
    setup: ReviewSetup | None = None,
) -> PanelInputs:
    setup = setup or review_setup(resolved)
    settings = setup.settings
    workspace = resolved.workspace_root
    project = resolved.project
    review = project.review
    reviewer_config = review.reviewer if review is not None else None
    provider_id = (
        provider
        or settings.provider
        or (reviewer_config.provider if reviewer_config else None)
        or project.agent_provider
    )
    if provider_id == "session":
        provider_id = "simulated"
    execution_id = f"{REVIEW_EXECUTION_PREFIX}-{sha256_bytes(diff_text.encode())[7:19]}"
    cache_settings = settings.effective_cache
    cache = (
        ReviewCache(
            workspace / CACHE_DIRECTORY,
            ttl_seconds=cache_settings.ttl_seconds,
            max_entries=cache_settings.limit,
        )
        if use_cache and cache_settings.on
        else None
    )
    runner = SafeProcessRunner(workspace)
    servers = mcp_servers(workspace, settings.mcp_servers)
    untrusted, notice = panel_context(resolved, workspace)
    return PanelInputs(
        workspace=workspace,
        diff_text=diff_text,
        mode=mode,
        catalog=setup.catalog,
        reviewers=setup.reviewers,
        settings=settings,
        provider=provider_id,
        invoker=_invoker(resolved, services, setup, execution_id),
        route=reviewer_route(resolved),
        runner_version=__version__,
        base=base,
        head=head,
        fallback=fallback or settings.fallback_provider,
        forced_model=model,
        skip=skip,
        packs=setup.packs or None,
        cache=cache,
        consistency=consistency_runner(resolved, workspace, runner, execution_id=execution_id),
        linters=linter_runner(resolved, setup, workspace, runner, execution_id=execution_id),
        mcp={"mcpServers": servers, "digest": mcp_digest(servers)},
        extra=untrusted,
        untrusted_notice=notice,
    )


# ----- evidence refs ----------------------------------------------------------------------------
def report_json(report: dict[str, Any]) -> bytes:
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")


def write_evidence_ref(git: Git, report: PanelReport) -> str | None:
    kind = REF_KINDS.get(report.verdict)
    head = report.head
    if kind is None or not head or head.startswith("tree:"):
        return None
    blob = git.text("hash-object", "-w", "--stdin", stdin=report_json(report.as_dict()))
    ref = f"{REF_PREFIX}/{kind}/{head}"
    git.run("update-ref", ref, blob)
    for other in REF_KINDS.values():
        if other != kind:
            git.run("update-ref", "-d", f"{REF_PREFIX}/{other}/{head}", check=False)
    return ref


def verify_evidence(git: Git, sha: str) -> dict[str, Any]:
    """Check the review evidence of a commit without calling any model: the ref exists, its
    report is bound to the commit, its digest is intact, its verdict matches the ref and its
    diff is the diff between its base and the commit."""
    commit = git.resolve(sha)
    problems: list[str] = []
    result: dict[str, Any] = {"sha": sha, "commit": commit}
    if commit is None:
        return {**result, "valid": False, "problems": [f"{sha} is not a commit"]}
    found: tuple[str, str] | None = None
    for verdict, kind in REF_KINDS.items():
        ref = f"{REF_PREFIX}/{kind}/{commit}"
        if (
            git.resolve(ref) is not None
            or git.run("rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0
        ):
            found = (verdict, ref)
            break
    if found is None:
        return {**result, "valid": False, "problems": ["no review evidence ref for the commit"]}
    verdict, ref = found
    result["ref"] = ref
    try:
        report = json.loads(git.run("cat-file", "blob", ref).stdout.decode("utf-8"))
    except ValueError as error:
        return {**result, "valid": False, "problems": [f"the report is not JSON: {error}"]}
    result["verdict"] = report.get("verdict")
    if report.get("head") != commit:
        problems.append("the report is bound to another commit")
    if report.get("verdict") != verdict:
        problems.append(f"the ref says {verdict} but the report says {report.get('verdict')}")
    try:
        recomputed = PanelReport.from_dict(report).digest
    except (KeyError, TypeError, ValueError) as error:
        recomputed = f"unreadable: {error}"
    if recomputed != report.get("digest"):
        problems.append("the report digest does not match its content")
    base = report.get("base")
    if not isinstance(base, str) or git.resolve(base) is None:
        problems.append("the base of the report is not a commit of this repository")
    else:
        diff = git.run(*_diff_args(), base, commit).stdout.decode("utf-8", "replace")
        if diff_hash(diff) != report.get("diffHash"):
            problems.append("the diff between the base and the commit is not the one reviewed")
    return {**result, "valid": not problems, "problems": problems, "base": base}


# ----- comment ------------------------------------------------------------------------------------
def pull_request_number(explicit: int | None) -> int | None:
    if explicit:
        return explicit
    github = os.environ.get("GITHUB_REF", "")
    if github.startswith("refs/pull/"):
        part = github.split("/")[2]
        if part.isdigit():
            return int(part)
    for name in PULL_REQUEST_NUMBER_ENV:
        value = os.environ.get(name, "").strip()
        if value.isdigit():
            return int(value)
    return None


def render_comment(report: dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    lines = [
        f"### Review panel: {report['verdict']}",
        "",
        f"Head `{report.get('head')}`, base `{report.get('base')}`, report digest "
        f"`{report.get('digest')}`.",
        "",
        f"{counts.get('errors', 0)} error(s), {counts.get('suggestions', 0)} suggestion(s); "
        f"{counts.get('droppedOutside', 0)} finding(s) outside the changed lines dropped.",
        "",
    ]
    findings = report.get("findings") or []
    if findings:
        lines.extend(
            [
                "| Severity | Rule | Location | Issue |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in findings:
            issue = str(item["issue"]).replace("|", "\\|")
            lines.append(
                f"| {item['severity']} | `{item['rule']}` | `{item['file']}:{item['line']}` "
                f"({item['side']}) | {issue} |"
            )
    ran = [item["id"] for item in report.get("reviewers") or [] if item["status"] != "SKIPPED"]
    lines.extend(["", f"Reviewers: {', '.join(ran) or 'none (deterministic rules only)'}."])
    return "\n".join(lines) + "\n"


def comment_on_forge(
    services: Any,
    report: dict[str, Any],
    number: int,
    transport_override: Any = None,
) -> dict[str, Any]:
    from governed_harness.application.forges import forge_settings
    from governed_harness.forges import open_forge, resolve_forge

    resolved = resolve_forge(services.paths.workspace, forge_settings(services))
    forge = open_forge(resolved, transport_override)
    return forge.upsert_comment(number, f"review-{report['head']}", render_comment(report))


# ----- hook ---------------------------------------------------------------------------------------
def install_hook(
    workspace: Path, *, force: bool = False, executable: str | None = None
) -> dict[str, Any]:
    git = Git(workspace)
    if not git.is_repository():
        raise ConfigurationError(f"{workspace} is not a Git repository")
    hooks = Path(git.text("rev-parse", "--git-path", "hooks"))
    if not hooks.is_absolute():
        hooks = workspace / hooks
    target = hooks / "pre-push"
    existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
    if existing is not None and HOOK_MARKER not in existing and not force:
        raise ConfigurationError(
            f"{target} exists and was not written by the harness; pass --force to replace it"
        )
    command = executable or shutil.which("harness") or "harness"
    script = (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        "# Reviews the commits being pushed against their base; a FAIL or UNKNOWN verdict, or a\n"
        "# base that cannot be fetched, stops the push (exit code 6).\n"
        'root="$(git rev-parse --show-toplevel)" || exit 1\n'
        f'exec {shlex.quote(command)} --json review-code --mode hook --path "$root"\n'
    )
    hooks.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"status": "PASSED", "hook": str(target), "command": command}


# ----- variance -------------------------------------------------------------------------------------
def variance_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Verdict stability, finding frequency and pairwise agreement of identical reviews."""
    keys = [
        {
            f"{item['file']}:{item['side']}:{item['line']}:{item['rule']}"
            for item in report.get("findings") or []
        }
        for report in reports
    ]
    verdicts = Counter(str(report.get("verdict")) for report in reports)
    frequency = Counter(key for run in keys for key in run)
    pairs = list(combinations(keys, 2))
    jaccard = [
        (len(left & right) / len(left | right)) if (left | right) else 1.0 for left, right in pairs
    ]
    runs = len(reports)
    return {
        "runs": runs,
        "verdicts": dict(sorted(verdicts.items())),
        "verdictStability": round(max(verdicts.values()) / runs, 4) if runs else None,
        "findings": [
            {"finding": key, "runs": count, "share": round(count / runs, 4)}
            for key, count in sorted(frequency.items(), key=lambda item: (-item[1], item[0]))
        ],
        "meanJaccard": round(sum(jaccard) / len(jaccard), 4) if jaccard else 1.0,
        "tokensPerRun": [int((report.get("tokens") or {}).get("total") or 0) for report in reports],
    }


# ----- the commands -------------------------------------------------------------------------------
class ReviewCodeCommands:
    """The ``harness review-code`` and ``harness review ...`` commands of the application."""

    def _resolved_repository(self, path: Path) -> tuple[ResolvedConfiguration, Git]:
        from governed_harness.configuration.resolver import ConfigurationResolver

        resolved = ConfigurationResolver().resolve(path)
        git = Git(resolved.workspace_root)
        if not git.is_repository():
            raise ConfigurationError(f"{resolved.workspace_root} is not a Git repository")
        return resolved, git

    def review_code(
        self,
        path: Path,
        *,
        mode: str = "manual",
        base: str | None = None,
        head: str = "HEAD",
        provider: str | None = None,
        fallback: str | None = None,
        model: str | None = None,
        skip: tuple[str, ...] = (),
        pull_request: int | None = None,
        comment: bool | None = None,
        cache: bool = True,
        transport_override: Any = None,
    ) -> dict[str, Any]:
        """Review the change of a branch (``manual``), of a push (``hook``) or of the index
        (``staged``) with the review panel, outside a governed run."""
        from governed_harness.delivery.publisher import PublishError
        from governed_harness.orchestration.engine import EngineServices

        if mode not in MODES:
            raise ConfigurationError(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
        resolved, git = self._resolved_repository(path)
        setup = review_setup(resolved)
        if setup.settings.mode == "off":
            raise ConfigurationError("review.panel.mode is off in project.yaml")
        resolution = resolve_base(git, mode, base, setup.settings.base_branches)
        diff_text, head_sha = collect_diff(git, mode, resolution, head)
        services = EngineServices.open(resolved)
        try:
            inputs = panel_inputs(
                resolved,
                services,
                mode=mode,
                diff_text=diff_text,
                base=resolution.sha,
                head=head_sha,
                provider=provider,
                fallback=fallback,
                model=model,
                skip=skip,
                use_cache=cache,
                setup=setup,
            )
            report = run_panel(inputs)
            result = report.as_dict()
            result["baseResolution"] = resolution.as_dict()
            evidence: str | None = None
            if setup.settings.evidence_refs is not False and mode != "staged":
                evidence = write_evidence_ref(git, report)
            result["evidenceRef"] = evidence
            wanted = comment if comment is not None else bool(setup.settings.comment)
            number = pull_request_number(pull_request)
            if not wanted or number is None:
                result["comment"] = {"status": "SKIPPED", "reason": "no comment requested"}
            elif report.verdict not in REF_KINDS:
                result["comment"] = {
                    "status": "SKIPPED",
                    "reason": f"no comment on a {report.verdict} review",
                }
            elif report.cache == "hit":
                result["comment"] = {
                    "status": "SKIPPED",
                    "reason": "cache hit: this result was already commented",
                }
            else:
                try:
                    posted = comment_on_forge(services, result, number, transport_override)
                    result["comment"] = {"status": "PASSED", "pullRequest": number, **posted}
                except (PublishError, ConfigurationError) as error:
                    result["comment"] = {"status": "ERROR", "error": str(error)}
        finally:
            services.close()
        return result

    def review_rules(self, path: Path) -> dict[str, Any]:
        """The effective catalog: the rules by layer, the inactive ones and why, the reviewers
        and their drift."""
        from governed_harness.configuration.resolver import ConfigurationResolver
        from governed_harness.review.reviewers import drift

        resolved = ConfigurationResolver().resolve(path)
        setup = review_setup(resolved)
        return {
            "catalog": setup.catalog.summary(),
            "rules": [
                {
                    "id": rule.rule_id,
                    "domain": rule.domain,
                    "layer": rule.layer,
                    "source": rule.source,
                    "severity": rule.severity,
                    "priority": rule.priority,
                    "verifiedBy": list(rule.verified_by),
                }
                for rule in setup.catalog.rules
            ],
            "reviewers": [
                {
                    "id": item.reviewer_id,
                    "domain": item.domain,
                    "source": item.source,
                    "diffSlice": item.spec.diff_slice
                    if isinstance(item.spec.diff_slice, str)
                    else list(item.spec.diff_slice),
                    "activation": item.spec.activation,
                    "modes": list(item.spec.modes),
                    "rules": len(setup.catalog.for_domain(item.domain, ai_only=True)),
                }
                for item in setup.reviewers
            ],
            "drift": drift(setup.reviewers, setup.catalog),
            "packs": list(setup.packs),
            "tools": sorted(setup.tools),
        }

    def review_rules_sync(self, path: Path, *, check: bool = False) -> dict[str, Any]:
        """Write (or, with ``check``, compare) the rules block of every project reviewer."""
        from governed_harness.configuration.resolver import ConfigurationResolver

        resolved = ConfigurationResolver().resolve(path)
        setup = review_setup(resolved)
        results = sync_blocks(resolved.workspace_root, setup.catalog, check=check)
        drifted = [item.as_dict() for item in results if item.status == "drift"]
        return {
            "status": "FAILED" if drifted else "PASSED",
            "check": check,
            "catalog": setup.catalog.digest,
            "reviewers": [item.as_dict() for item in results],
            "drift": drifted,
        }

    def review_verify(self, path: Path, sha: str) -> dict[str, Any]:
        _resolved, git = self._resolved_repository(path)
        return verify_evidence(git, sha)

    def review_hook_install(self, path: Path, *, force: bool = False) -> dict[str, Any]:
        resolved, _git = self._resolved_repository(path)
        return install_hook(resolved.workspace_root, force=force)

    def review_variance(
        self,
        path: Path,
        *,
        runs: int = 3,
        mode: str = "manual",
        base: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """The same review ``runs`` times without cache: verdict stability and agreement."""
        from governed_harness.orchestration.engine import EngineServices

        if not 2 <= runs <= 10:
            raise ConfigurationError("runs must be between 2 and 10")
        resolved, git = self._resolved_repository(path)
        setup = review_setup(resolved)
        resolution = resolve_base(git, mode, base, setup.settings.base_branches)
        diff_text, head_sha = collect_diff(git, mode, resolution, "HEAD")
        services = EngineServices.open(resolved)
        reports: list[dict[str, Any]] = []
        try:
            for _ in range(runs):
                inputs = panel_inputs(
                    resolved,
                    services,
                    mode=mode,
                    diff_text=diff_text,
                    base=resolution.sha,
                    head=head_sha,
                    provider=provider,
                    fallback=None,
                    model=model,
                    skip=(),
                    use_cache=False,
                    setup=setup,
                )
                reports.append(run_panel(inputs).as_dict())
        finally:
            services.close()
        return {
            "baseResolution": resolution.as_dict(),
            "head": head_sha,
            **variance_summary(reports),
        }


__all__ = [
    "MODES",
    "REF_KINDS",
    "ReviewCodeCommands",
    "REF_PREFIX",
    "BaseResolution",
    "ReviewAborted",
    "collect_diff",
    "comment_on_forge",
    "install_hook",
    "panel_inputs",
    "pull_request_number",
    "render_comment",
    "resolve_base",
    "sync_blocks",
    "variance_summary",
    "verify_evidence",
    "write_evidence_ref",
]
