#!/usr/bin/env python3
"""H16: identity and declared attribution of whoever decides, by the CLI and by the local API,
``CATALOGO.md`` §2.2 and ``PREREGISTRO.md`` (third freeze). No model is called.

Twenty-five decision attempts (19 undue, 6 legitimate) on runs of the sample project waiting at
DECISION, over six scenarios:

* ``cli-git`` (``governance.deciderIdentity: git``, as ``harness init`` writes it): agent,
  validator and harness actor ids, an upper-case agent id, a stale digest (the ChangeSet of an
  earlier run of the workspace), ``REQUEST_CHANGES`` by an agent, then the person's ``APPROVE``;
* ``cli-git-identity``: ``gate decide`` without ``--actor`` and a local Git identity;
* ``cli-exception``: a gate failed by an untraced requirement (amendment 3): ``APPROVE``, ``APPROVE_EXCEPTION`` with a
  blank rationale and with an expiry in the past, a legitimate exception for 1 h recorded with
  ``--no-continue``, then ``run continue`` with the clock 2 h ahead (the exception has expired);
* ``cli-default`` (``deciderIdentity: default``): without ``--actor`` (``human.local``) and an
  agent id;
* ``api-token`` (the ``api`` section of ``init`` plus a viewer and a reviewer): no token, an
  invalid token, the viewer's token, the reviewer's token with an agent id, with another person's
  id and with a stale digest, the token in the query string, then the reviewer's and the admin's
  legitimate decisions;
* ``api-off`` (``api.auth: off``, the 1.0.0 behaviour): an agent id.

The API listens on 127.0.0.1 on a free port; every token is generated at run time
(``secrets.token_urlsafe``) and only reaches the server through environment variables. Each
attempt records its exit code or HTTP status and whether a ``human.decision.recorded`` event was
added to the run; afterwards ``.harness/`` and the state directory are searched for the tokens.

Usage:
  python evaluation/deterministic/h16_decider_identity.py --out DIR [--work DIR]
      [--project-venv DIR] [--harness PATH] [--wheel WHEEL] [--only cli-git,...]
      [--conditions H] [--reps 1] [--summarize-only] [--keep-work]

Writes ``DIR/h16-decider-identity.jsonl``, ``DIR/h16-decider-identity-summary.json``,
``DIR/environment.json`` and ``DIR/configs/``. Exits 0 when the suite ran, whatever it found.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_support import (  # noqa: E402
    decide,
    decisions,
    feature_spec,
    feature_task,
    git_as,
    harness_python,
    reset_tree,
    shifted,
)
from resource_support import (  # noqa: E402
    Workspace,
    cleanup,
    governed,
    main,
    proportion,
    run_state,
    run_task,
    saved_config,
    stamp_dir,
)

STALE_N = 1  # the earlier run of a scenario, whose digest becomes the stale one
VIEWER_ENV = "H16_VIEWER_TOKEN"
REVIEWER_ENV = "H16_REVIEWER_TOKEN"
START_ENV = "HARNESS_API_TOKEN"  # the api.tokenEnv harness init writes
GIT_PERSON = ("Dana Example", "dana@example.invalid")

SCENARIOS: list[dict[str, Any]] = [
    {"id": "cli-git", "condition": "H-cli-git"},
    {"id": "cli-git-identity", "condition": "H-cli-git"},
    {"id": "cli-exception", "condition": "H-cli-git"},
    {"id": "cli-default", "condition": "H-cli-default"},
    {"id": "api-token", "condition": "H-api-token"},
    {"id": "api-off", "condition": "H-api-off"},
]


def conditions_of(scenario: dict[str, Any]) -> list[str]:
    return [scenario["condition"]]


def attempt(
    attempt_id: str,
    channel: str,
    description: str,
    legitimate: bool,
    expected_code: int,
    observed: dict[str, Any],
    *,
    expected_actor: str | None = None,
) -> dict[str, Any]:
    recorded = observed.get("recorded") or []
    actor = ((recorded[-1].get("actor") or {}).get("actor_id")) if recorded else None
    source = recorded[-1].get("identity_source") if recorded else None
    registered = len(recorded) > 0
    return {
        "attempt": attempt_id,
        "channel": channel,
        "description": description,
        "legitimate": legitimate,
        "expectedCode": expected_code,
        "observedCode": observed.get("code"),
        "codeMatches": observed.get("code") == expected_code,
        "decisionRecorded": registered,
        "recordedActor": actor,
        "identitySource": source,
        "expectedActor": expected_actor,
        "undue": (not legitimate) and registered,
        "legitimateAccepted": legitimate
        and registered
        and (expected_actor is None or actor == expected_actor),
        "detail": observed.get("detail"),
    }


def cli(
    workspace: Workspace,
    run_id: str,
    decision: str,
    digest: str,
    *,
    actor: str | None,
    rationale: str = "evaluation decision",
    extra: tuple[str, ...] = (),
    label: str = "",
) -> dict[str, Any]:
    result = decide(
        workspace.h,
        workspace.ws,
        workspace.database,
        run_id,
        decision,
        digest,
        rationale=rationale,
        actor=actor,
        extra=extra,
        label=label,
    )
    output = result["output"]
    detail = output.get("error") or output.get("message") or output.get("stderr") or ""
    return {"code": result["exitCode"], "recorded": result["recorded"], "detail": str(detail)[:300]}


def to_decision(workspace: Workspace, n: int, *, untraced: bool = False) -> tuple[str, str]:
    """A fresh run of case ``n`` to DECISION: its run id and ChangeSet digest."""
    reset_tree(workspace.ws)
    workspace.write_spec(feature_spec(n), f"calls-{n:02d}.jsonl")
    started = run_task(workspace, feature_task("h16", n, untraced=untraced))
    state = run_state(workspace, started["runId"])
    if state.get("phase") != "DECISION":
        raise RuntimeError(
            f"run {n} did not reach DECISION: {state} (exit {started['runExitCode']})"
        )
    return started["runId"], str(state.get("changeSetDigest"))


def stale_digest(workspace: Workspace) -> str:
    """The digest of an earlier run of the workspace, rejected by the person afterwards."""
    run_id, digest = to_decision(workspace, STALE_N)
    cli(workspace, run_id, "REJECT", digest, actor="human.reviewer", label="REJECT earlier run")
    return digest


# ----- the API ---------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Api:
    """``harness api serve`` of the workspace on 127.0.0.1, with the tokens in its environment."""

    def __init__(self, workspace: Workspace, args: argparse.Namespace, tokens: dict[str, str]):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = workspace.run_dir / "api-serve.log"
        env = {**workspace.h.env, **tokens}
        command = [
            harness_python(args),
            "-m",
            "governed_harness",
            "api",
            "serve",
            "--path",
            str(workspace.ws),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        self.handle = self.log.open("wb")
        self.proc = subprocess.Popen(  # noqa: S603 - fixed command, loopback only
            command, cwd=workspace.ws, env=env, stdout=self.handle, stderr=subprocess.STDOUT
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
        query: str = "",
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        url = self.base + path + (f"?{query}" if query else "")
        request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - loopback
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=900) as response:  # noqa: S310
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            payload = raw.decode("utf-8", "replace")[:300]
        return status, payload

    def wait_ready(self, token: str | None, seconds: float = 120) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            with contextlib.suppress(OSError):
                status, _ = self.request("GET", "/api/session", token=token)
                if status in {200, 404}:
                    return True
            time.sleep(1)
        return False

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=20)
        self.handle.close()


def api_decide(
    api: Api,
    workspace: Workspace,
    run_id: str,
    digest: str,
    *,
    token: str | None,
    actor: str | None = None,
    query: str = "",
) -> dict[str, Any]:
    before = len(decisions(workspace.database, run_id))
    body: dict[str, Any] = {
        "decision": "APPROVE",
        "change_set_digest": digest,
        "rationale": "evaluation decision through the API",
    }
    if actor is not None:
        body["actor_id"] = actor
    status, payload = api.request(
        "POST", f"/api/runs/{run_id}/decision", token=token, body=body, query=query
    )
    recorded = decisions(workspace.database, run_id)[before:]
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    return {"code": status, "recorded": recorded, "detail": str(detail)[:300]}


def tokens_found(workspace: Workspace, tokens: dict[str, str]) -> dict[str, list[str]]:
    """Files of ``.harness/`` and of the state directory that contain a token (none expected)."""
    found: dict[str, list[str]] = {}
    roots = [workspace.ws / ".harness", workspace.run_dir / "harness-state"]
    for root in roots:
        for path in root.rglob("*") if root.exists() else []:
            if not path.is_file():
                continue
            with contextlib.suppress(OSError):
                data = path.read_bytes()
                for name, value in tokens.items():
                    if value.encode() in data:
                        found.setdefault(name, []).append(path.name)
    return found


def audit_log(workspace: Workspace) -> list[dict[str, Any]]:
    path = workspace.ws / ".harness" / "audit" / "api-decisions.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(ValueError):
            row = json.loads(line)
            rows.append(
                {
                    k: row.get(k)
                    for k in ("outcome", "status", "userId", "role", "requestedActorId", "actorId")
                }
            )
    return rows


# ----- scenarios -------------------------------------------------------------------------------
def scenario_cli_git(workspace: Workspace, args: argparse.Namespace) -> list[dict[str, Any]]:
    del args
    stale = stale_digest(workspace)
    run_id, digest = to_decision(workspace, 2)
    out = []
    for attempt_id, actor in (
        ("A01", "agent.claude-code"),
        ("A02", "validator.tests"),
        ("A03", "harness.core"),
        ("A04", "AGENT.fixture"),
    ):
        observed = cli(workspace, run_id, "APPROVE", digest, actor=actor, label=f"{attempt_id}")
        out.append(attempt(attempt_id, "cli", f"APPROVE as {actor}", False, 5, observed))
    observed = cli(workspace, run_id, "APPROVE", stale, actor="human.reviewer", label="A05")
    out.append(attempt("A05", "cli", "APPROVE with a stale digest", False, 5, observed))
    observed = cli(
        workspace, run_id, "REQUEST_CHANGES", digest, actor="agent.claude-code", label="A06"
    )
    out.append(attempt("A06", "cli", "REQUEST_CHANGES as an agent", False, 5, observed))
    observed = cli(workspace, run_id, "APPROVE", digest, actor="human.reviewer", label="A07")
    out.append(
        attempt(
            "A07",
            "cli",
            "APPROVE by the person",
            True,
            0,
            observed,
            expected_actor="human.reviewer",
        )
    )
    return out


def scenario_cli_git_identity(
    workspace: Workspace, args: argparse.Namespace
) -> list[dict[str, Any]]:
    del args
    git_as(workspace.ws, "config", "user.name", GIT_PERSON[0])
    git_as(workspace.ws, "config", "user.email", GIT_PERSON[1])
    run_id, digest = to_decision(workspace, 3)
    observed = cli(workspace, run_id, "APPROVE", digest, actor=None, label="A08")
    return [
        attempt(
            "A08",
            "cli",
            "APPROVE without --actor, the Git user decides",
            True,
            0,
            observed,
            expected_actor="dana-example.invalid",
        )
    ]


def scenario_cli_exception(workspace: Workspace, args: argparse.Namespace) -> list[dict[str, Any]]:
    run_id, digest = to_decision(workspace, 4, untraced=True)
    gate = run_state(workspace, run_id).get("gate")
    if gate == "PASSED":
        # Without a failed gate the undue attempts below would be legitimate: not evaluable.
        raise RuntimeError("the untraced requirement did not fail the gate")
    out = []
    observed = cli(workspace, run_id, "APPROVE", digest, actor="human.reviewer", label="A09")
    out.append(attempt("A09", "cli", f"APPROVE over the {gate} gate", False, 5, observed))
    observed = cli(
        workspace,
        run_id,
        "APPROVE_EXCEPTION",
        digest,
        actor="human.reviewer",
        rationale=" ",
        label="A10",
    )
    out.append(
        attempt("A10", "cli", "APPROVE_EXCEPTION with a blank rationale", False, 5, observed)
    )
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    observed = cli(
        workspace,
        run_id,
        "APPROVE_EXCEPTION",
        digest,
        actor="human.reviewer",
        rationale="accepted until the loader is replaced",
        extra=("--expires-at", past),
        label="A11",
    )
    out.append(attempt("A11", "cli", "APPROVE_EXCEPTION expiring in the past", False, 2, observed))
    observed = cli(
        workspace,
        run_id,
        "APPROVE_EXCEPTION",
        digest,
        actor="human.reviewer",
        rationale="accepted for one hour while the loader is replaced",
        extra=("--expires-in", "1h", "--no-continue"),
        label="A12",
    )
    out.append(
        attempt(
            "A12",
            "cli",
            "APPROVE_EXCEPTION for 1 h, --no-continue",
            True,
            0,
            observed,
            expected_actor="human.reviewer",
        )
    )
    before = len(decisions(workspace.database, run_id))
    code, _, _ = shifted(workspace.h, args, 2).run(
        workspace.ws, "run", "continue", "--path", ".", "--run", run_id, label="A13"
    )
    state = run_state(workspace, run_id)
    closed = state.get("runStatus") == "PASSED"
    row = attempt(
        "A13",
        "cli",
        "run continue 2 h later: the exception has expired",
        False,
        6,
        {
            "code": code,
            "recorded": decisions(workspace.database, run_id)[before:],
            "detail": "; ".join(state.get("blockedPhases") or [])[:300],
        },
    )
    # Undue here means the run closed on the expired exception (PREREGISTRO.md, H16).
    row["runClosed"] = closed
    row["undue"] = closed or row["undue"]
    row["finalState"] = {k: state.get(k) for k in ("runStatus", "phase", "gate")}
    out.append(row)
    return out


def scenario_cli_default(workspace: Workspace, args: argparse.Namespace) -> list[dict[str, Any]]:
    del args
    run_id, digest = to_decision(workspace, 5)
    out = []
    observed = cli(workspace, run_id, "APPROVE", digest, actor="agent.x", label="A15")
    out.append(attempt("A15", "cli", "APPROVE as agent.x", False, 5, observed))
    observed = cli(workspace, run_id, "APPROVE", digest, actor=None, label="A14")
    out.append(
        attempt(
            "A14",
            "cli",
            "APPROVE without --actor (deciderIdentity: default)",
            True,
            0,
            observed,
            expected_actor="human.local",
        )
    )
    return out


def scenario_api_token(
    workspace: Workspace, args: argparse.Namespace
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tokens = {
        START_ENV: secrets.token_urlsafe(32),
        VIEWER_ENV: secrets.token_urlsafe(32),
        REVIEWER_ENV: secrets.token_urlsafe(32),
    }
    stale = stale_digest(workspace)
    run_id, digest = to_decision(workspace, 6)
    api = Api(workspace, args, tokens)
    out: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    try:
        extra["apiReady"] = api.wait_ready(tokens[START_ENV])
        if not extra["apiReady"]:
            raise RuntimeError("harness api serve did not answer")
        start, viewer, reviewer = tokens[START_ENV], tokens[VIEWER_ENV], tokens[REVIEWER_ENV]
        invalid = secrets.token_urlsafe(32)
        for attempt_id, description, kwargs, code in (
            ("A16", "no Authorization header", {"token": None}, 401),
            ("A17", "an invalid token", {"token": invalid}, 401),
            ("A18", "the viewer's token", {"token": viewer}, 403),
            (
                "A19",
                "the reviewer's token with an agent id",
                {"token": reviewer, "actor": "agent.claude-code"},
                403,
            ),
            (
                "A20",
                "the reviewer's token with another person's id",
                {"token": reviewer, "actor": "human.web"},
                403,
            ),
        ):
            observed = api_decide(api, workspace, run_id, digest, **kwargs)
            out.append(attempt(attempt_id, "api", description, False, code, observed))
        observed = api_decide(api, workspace, run_id, stale, token=reviewer)
        out.append(
            attempt("A21", "api", "the reviewer's token, stale digest", False, 409, observed)
        )
        observed = api_decide(api, workspace, run_id, digest, token=None, query=f"token={reviewer}")
        out.append(attempt("A22", "api", "the token in the query string", False, 401, observed))
        observed = api_decide(api, workspace, run_id, digest, token=reviewer)
        out.append(
            attempt(
                "A23",
                "api",
                "the reviewer decides",
                True,
                200,
                observed,
                expected_actor="human.reviewer",
            )
        )
        second, second_digest = to_decision(workspace, 7)
        observed = api_decide(api, workspace, second, second_digest, token=start)
        out.append(
            attempt(
                "A24",
                "api",
                "the admin (start token) decides",
                True,
                200,
                observed,
                expected_actor="human.web",
            )
        )
    finally:
        api.stop()
    extra["tokensFound"] = tokens_found(workspace, tokens)
    log = api.log.read_bytes()
    extra["tokensInServerLog"] = sorted(n for n, v in tokens.items() if v.encode() in log)
    extra["auditLog"] = audit_log(workspace)
    return out, extra


def scenario_api_off(
    workspace: Workspace, args: argparse.Namespace
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_id, digest = to_decision(workspace, 8)
    api = Api(workspace, args, {})
    out: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}
    try:
        extra["apiReady"] = api.wait_ready(None)
        if not extra["apiReady"]:
            raise RuntimeError("harness api serve did not answer")
        observed = api_decide(api, workspace, run_id, digest, token=None, actor="agent.claude-code")
        out.append(attempt("A25", "api", "no authentication, an agent id", False, 403, observed))
    finally:
        api.stop()
    return out, extra


def mutation(condition: str) -> Any:
    def apply(config: dict[str, Any]) -> None:
        governance = config.setdefault("governance", {})
        api = config.setdefault("api", {})
        if condition == "H-cli-default":
            governance["deciderIdentity"] = "default"
        if condition == "H-api-token":
            api["users"] = [
                {"id": "human.viewer", "role": "viewer", "tokenEnv": VIEWER_ENV},
                {"id": "human.reviewer", "role": "reviewer", "tokenEnv": REVIEWER_ENV},
            ]
        if condition == "H-api-off":
            api["auth"] = "off"

    return apply


RUNNERS = {
    "cli-git": scenario_cli_git,
    "cli-git-identity": scenario_cli_git_identity,
    "cli-exception": scenario_cli_exception,
    "cli-default": scenario_cli_default,
    "api-token": scenario_api_token,
    "api-off": scenario_api_off,
}


def runner(
    scenario: dict[str, Any], condition: str, args: argparse.Namespace, rep: int
) -> dict[str, Any]:
    del rep
    run_dir = stamp_dir(args, "h16", scenario["id"])
    workspace = governed(run_dir, args, feature_spec(STALE_N), mutate=mutation(condition))
    saved_config(args, condition, workspace)
    result = RUNNERS[scenario["id"]](workspace, args)
    attempts, extra = result if isinstance(result, tuple) else (result, {})
    record = {
        "attempts": attempts,
        "deviations": workspace.deviations,
        "governance": {
            k: (workspace.config.get("governance") or {}).get(k) for k in ("deciderIdentity",)
        },
        "apiConfig": {k: v for k, v in (workspace.config.get("api") or {}).items() if k != "users"}
        | {"users": [u.get("id") for u in (workspace.config.get("api") or {}).get("users") or []]},
        **extra,
        "brief": f"{sum(a['undue'] for a in attempts)} undue, "
        f"{sum(a['legitimateAccepted'] for a in attempts)} legitimate accepted",
        "steps": list(workspace.h.steps),
    }
    cleanup(run_dir, args.keep_work)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "apparatusError" not in r and r["rep"] == 1]
    attempts = [
        a | {"scenario": r["case"], "condition": r["condition"]}
        for r in ok
        for a in r.get("attempts") or []
    ]
    undue = [a for a in attempts if not a["legitimate"]]
    legit = [a for a in attempts if a["legitimate"]]
    summary: dict[str, Any] = {
        "suite": "H16",
        "title": "identity and declared attribution of whoever decides (CLI and API)",
        "simulatedPerson": "the deciding people are simulated by the script (declared)",
        "attempts": {
            a["attempt"]: {
                k: a.get(k)
                for k in (
                    "scenario",
                    "condition",
                    "channel",
                    "description",
                    "legitimate",
                    "expectedCode",
                    "observedCode",
                    "codeMatches",
                    "decisionRecorded",
                    "recordedActor",
                    "identitySource",
                    "expectedActor",
                    "undue",
                    "legitimateAccepted",
                    "detail",
                    "finalState",
                )
            }
            for a in attempts
        },
        "undueAttempts": len(undue),
        "undueRecorded": proportion(sum(1 for a in undue if a["undue"]), len(undue)),
        "legitimateAccepted": proportion(
            sum(1 for a in legit if a["legitimateAccepted"]), len(legit)
        ),
        "codesMatchDocumented": proportion(
            sum(1 for a in attempts if a["codeMatches"]), len(attempts)
        ),
        "codeMismatches": sorted(a["attempt"] for a in attempts if not a["codeMatches"]),
        "tokens": {
            r["case"]: {
                "tokensFound": r.get("tokensFound"),
                "tokensInServerLog": r.get("tokensInServerLog"),
                "auditLog": r.get("auditLog"),
            }
            for r in ok
            if "tokensFound" in r
        },
    }
    expected_attempts = 25
    summary["criteria"] = {
        "zeroUndueDecisions": {
            "value": f"{sum(1 for a in undue if a['undue'])} undue of {len(undue)} undue attempts",
            "met": len(attempts) == expected_attempts and not any(a["undue"] for a in undue),
        },
        "allLegitimateAccepted": {
            "value": f"{sum(1 for a in legit if a['legitimateAccepted'])}/{len(legit)}",
            "met": len(legit) == 6 and all(a["legitimateAccepted"] for a in legit),
        },
        "note": "Pre-registered in PREREGISTRO.md (H16); evaluated as is. A scenario whose "
        "apparatus failed leaves its attempts out and the criterion unmet.",
    }
    return summary


if __name__ == "__main__":
    raise SystemExit(
        main(
            doc=__doc__ or "",
            suite_id="H16",
            title="identity and declared attribution of whoever decides",
            stem="h16-decider-identity",
            cases=SCENARIOS,
            conditions_of=conditions_of,
            all_conditions=["H-cli-git", "H-cli-default", "H-api-token", "H-api-off"],
            runner=runner,
            summarize=summarize,
        )
    )
