"""Complete delivery at CLOSURE (#55, item 13).

After the closure commit (``delivery.closureCommit``), what leaves the machine follows the
operational contract (the task's ``contract``, else ``delivery``):

* **push** (``push``): the closure commit's branch is pushed to the remote with ``git push`` and
  the repository's hooks run (never ``--no-verify``, never a forced push). A refusal stops
  CLOSURE with the reason; ``run continue`` tries again;
* **pull request** (``pullRequest.create``): after the push, the forge
  (``delivery.publisher.kind``) creates the pull request into ``pullRequest.base`` with the
  repository's template (``pullRequest.template``) followed by the decision brief, the labels
  and ``draft``; an open pull request of the branch is reused;
* **comment** (``comment``): the brief is commented on that pull request when the run is not
  clean (``notClean``: a gate that did not pass, an exception, a blocking finding, a
  certification that is not ``CERTIFIED``), always, or never;
* **stage** (``stage``): when the change is not pushed, only the run's files are staged in the
  workspace's index (``git add -- <paths>``, ``git rm --cached`` for deleted ones; never
  ``add -A``), so the change waits there for the person.

Each step is recorded as CLOSURE evidence and an event; nothing is repeated on a retry."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from governed_harness.configuration.models import PublisherConfig
from governed_harness.delivery.closure import ClosureCommit
from governed_harness.delivery.publisher import forge_for, render_brief_markdown
from governed_harness.delivery.vcs import Git, VcsError, repository_from_remote
from governed_harness.domain.enums import DecisionKind, PhaseId, ResultStatus
from governed_harness.domain.models import ChangeSet, Execution, HumanDecision, PhaseExecution, Task

if TYPE_CHECKING:
    from governed_harness.delivery.publisher import Transport
    from governed_harness.orchestration.engine import PhaseOutcome
    from governed_harness.orchestration.ladder import VerificationLadder

TRANSPORT: dict[str, Transport | None] = {"override": None}
"""A transport that replaces the configured one (tests talk to a fake forge through it)."""


class LadderDelivery:
    def __init__(self, ladder: VerificationLadder) -> None:
        self.ladder = ladder

    def authorisation(self, task: Task) -> dict[str, Any]:
        delivery = self.ladder.project.delivery_settings
        contract = task.contract
        pull = delivery.pull_request

        def chosen(name: str, configured: Any) -> Any:
            value = getattr(contract, name) if contract is not None else None
            return configured if value is None else value

        comment_policy = delivery.comment or "never"
        comment = chosen("comment", None)
        return {
            "push": bool(chosen("push", delivery.push)),
            "pullRequest": bool(chosen("create_pull_request", pull.create if pull else None)),
            "comment": comment_policy if comment is None else ("always" if comment else "never"),
            "stage": bool(delivery.stage),
            "branch": contract.branch if contract and contract.branch else None,
        }

    def deliver(
        self,
        execution: Execution,
        phase: PhaseExecution,
        decision: HumanDecision,
        commit: ClosureCommit,
        change_set: ChangeSet,
    ) -> PhaseOutcome | None:
        from governed_harness.orchestration.engine import PhaseOutcome

        hub = self.ladder
        results = hub.engine.results
        task = hub.engine.run_task(execution)
        allowed = self.authorisation(task)
        state_key = f"delivered:{execution.execution_id}"
        state: dict[str, Any] = results.flag_json(state_key) or {}
        workspace = hub.s.paths.workspace
        git = Git(workspace)
        report: dict[str, Any] = {"authorisation": allowed, "commit": commit.commit}
        branch = commit.branch
        if commit.commit and branch is None and git.is_repository():
            current = git.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
            branch = current.stdout.decode("utf-8", "replace").strip() or None
        if not allowed["push"] or not commit.commit or not branch:
            if allowed["stage"] and git.is_repository() and not state.get("staged"):
                staged = self._stage(git, workspace, change_set)
                state["staged"] = staged
                report["staged"] = staged
                hub.s.events.append(
                    execution.execution_id,
                    "delivery.staged",
                    {"paths": staged, "reason": "the contract does not authorise a push"},
                    phase_execution_id=phase.phase_execution_id,
                )
            results.set_flag_json(state_key, state)
            self._record(execution, report, "Delivery: change staged, not pushed")
            return None
        remote = self._remote()
        if not state.get("pushed"):
            try:
                git.run("push", remote, f"{branch}:{branch}")
            except VcsError as error:
                hub.s.events.append(
                    execution.execution_id,
                    "delivery.push.failed",
                    {"remote": remote, "branch": branch, "reason": str(error)[:500]},
                    phase_execution_id=phase.phase_execution_id,
                )
                return PhaseOutcome(ResultStatus.BLOCKED, f"Push of {branch} refused: {error}")
            state["pushed"] = {"remote": remote, "branch": branch, "commit": commit.commit}
            results.set_flag_json(state_key, state)
            hub.s.events.append(
                execution.execution_id,
                "delivery.pushed",
                state["pushed"],
                phase_execution_id=phase.phase_execution_id,
            )
        report["pushed"] = state["pushed"]
        if allowed["pullRequest"] and not state.get("pullRequest"):
            try:
                forge = self._forge()
                pull = hub.project.delivery_settings.pull_request
                base = (pull.base if pull and pull.base else None) or self._base(git, remote)
                body = self._template(pull.template if pull else None) + self._brief(execution)
                created = forge.create_pull_request(
                    head=branch,
                    base=base,
                    title=task.title,
                    body=body,
                    labels=tuple(pull.labels or ()) if pull else (),
                    draft=bool(pull.draft) if pull else False,
                )
            except Exception as error:  # noqa: BLE001 - the reason is reported, CLOSURE waits
                return PhaseOutcome(
                    ResultStatus.BLOCKED, f"The pull request of {branch} was not created: {error}"
                )
            state["pullRequest"] = created
            results.set_flag_json(state_key, state)
            hub.s.events.append(
                execution.execution_id,
                "delivery.pull-request.created",
                created,
                phase_execution_id=phase.phase_execution_id,
            )
        report["pullRequest"] = state.get("pullRequest")
        number = (state.get("pullRequest") or {}).get("number")
        if isinstance(number, int) and not state.get("commented"):
            policy = allowed["comment"]
            reasons = self.not_clean(execution, decision)
            if policy == "always" or (policy == "notClean" and reasons):
                try:
                    posted = self._forge().comment(
                        pull_request=number,
                        run_id=execution.execution_id,
                        body=self._brief(execution),
                    )
                except Exception as error:  # noqa: BLE001
                    return PhaseOutcome(
                        ResultStatus.BLOCKED, f"The comment on #{number} was not posted: {error}"
                    )
                state["commented"] = {**posted, "reasons": reasons}
                results.set_flag_json(state_key, state)
                hub.s.events.append(
                    execution.execution_id,
                    "delivery.comment.posted",
                    state["commented"],
                    phase_execution_id=phase.phase_execution_id,
                )
        report["comment"] = state.get("commented")
        self._record(execution, report, f"Delivery: {branch} pushed to {remote}")
        return None

    # ----- helpers --------------------------------------------------------------------------------
    def _stage(self, git: Git, workspace: Path, change_set: ChangeSet) -> list[str]:
        present = [item.path for item in change_set.files if (workspace / item.path).exists()]
        gone = [item.path for item in change_set.files if not (workspace / item.path).exists()]
        if present:
            git.run("add", "--", *present)
        if gone:
            git.run("rm", "--cached", "--quiet", "--ignore-unmatch", "--", *gone)
        return sorted([*present, *gone])

    def _remote(self) -> str:
        isolation = self.ladder.project.workspace.isolation
        return (isolation.remote if isolation and isolation.remote else None) or "origin"

    @staticmethod
    def _base(git: Git, remote: str) -> str:
        value = git.run(
            "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD", check=False
        )
        head = value.stdout.decode("utf-8", "replace").strip()
        return head.split("/", 1)[1] if "/" in head else "main"

    def _forge(self) -> Any:
        hub = self.ladder
        settings = hub.project.delivery_settings.publisher or PublisherConfig()
        repository = settings.repository or repository_from_remote(hub.s.paths.workspace)
        if repository is None:
            raise VcsError(
                "no repository: set delivery.publisher.repository (no GitHub remote named "
                "origin was found)"
            )
        return forge_for(settings, repository, TRANSPORT["override"])

    def _template(self, template: str | None) -> str:
        if not template:
            return ""
        path = self.ladder.s.paths.workspace / template
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").rstrip() + "\n\n"

    def _brief(self, execution: Execution) -> str:
        from governed_harness.application.exceptions import brief_exceptions
        from governed_harness.application.review import build_brief

        services = self.ladder.s
        brief = build_brief(
            services,
            execution.execution_id,
            exceptions=brief_exceptions(
                services, self.ladder.engine.get_execution(execution.execution_id)
            ),
        )
        return render_brief_markdown(brief)

    def not_clean(self, execution: Execution, decision: HumanDecision) -> list[str]:
        """Why a run is not clean: what a reviewer of the pull request should look at."""
        from governed_harness.domain.models import GateEvaluation

        reasons: list[str] = []
        if decision.decision is DecisionKind.APPROVE_EXCEPTION:
            reasons.append("approved with an exception")
        latest = self.ladder.engine.get_execution(execution.execution_id)
        if latest.gate_evaluation_id:
            gate = self.ladder.s.state.get("gate", latest.gate_evaluation_id, GateEvaluation)
            if gate.status is not ResultStatus.PASSED:
                reasons.append(f"gate {gate.status.value}")
        certification = self.ladder.latest_certification(
            execution.execution_id, decision.change_set_digest
        )
        if certification is not None and certification.status != "CERTIFIED":
            reasons.append(f"certification {certification.status}")
        return reasons

    def _record(self, execution: Execution, report: dict[str, Any], summary: str) -> None:
        self.ladder.engine.results.record_json(
            execution, PhaseId.CLOSURE, report, kind="delivery-report", summary=summary
        )


__all__ = ["TRANSPORT", "LadderDelivery"]
