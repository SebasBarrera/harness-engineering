from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, NoReturn

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from governed_harness import __version__
from governed_harness.api.auth import (
    PRINCIPAL_SCOPE_KEY,
    AuthenticationMiddleware,
    DecisionAuditLog,
    Principal,
    StartToken,
    TokenAuthenticator,
    api_settings,
)
from governed_harness.application import HarnessApplication
from governed_harness.application.exceptions import ExceptionOptions
from governed_harness.configuration import ConfigurationResolver
from governed_harness.configuration.api import ApiConfig, ApiRole
from governed_harness.domain.actors import DEFAULT_API_ACTOR
from governed_harness.domain.enums import DecisionKind
from governed_harness.domain.errors import HarnessError, IntegrityError, NonHumanActorError


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionKind
    change_set_digest: str
    actor_id: str | None = None
    """The person deciding; without it, the Git user under ``governance.deciderIdentity: git``,
    otherwise ``human.web``. Agent, validator and harness ids are refused (403)."""
    rationale: str
    continue_after: bool = True
    expires_in: str | None = None
    expires_at: str | None = None
    scope: tuple[str, ...] = ()
    alternative_evidence: str | None = None
    follow_up: str | None = None
    checked_items: tuple[str, ...] = ()
    """Checklist items the person verified (``review.manualChecklist``, #55)."""


def trusted_hosts(workspace: Path) -> tuple[str, ...] | None:
    """``governance.trustedHosts`` of the project, ``None`` when it is not set (or there is no
    valid configuration, which every endpoint then reports)."""
    try:
        project = ConfigurationResolver().resolve(workspace).project
    except (HarnessError, OSError):
        return None
    return project.governance_settings.trusted_hosts


def create_app(
    workspace: Path,
    *,
    start_token: StartToken | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> FastAPI:
    """The API of ``workspace``. Under the ``api`` section of ``project.yaml`` (#18) every
    route requires a bearer token: ``start_token`` (``harness api serve`` passes the one it
    took from ``api.tokenEnv`` or generated; without it, the value of ``api.tokenEnv`` when
    set) and the tokens of ``api.users``, read from ``environ`` (default ``os.environ``).
    The notices of the authentication setup (users who cannot sign in) are in
    ``app.state.auth_notices``."""
    root = workspace.resolve(strict=True)
    settings = api_settings(root)
    application = HarnessApplication()
    api = FastAPI(
        title="Governed Agent Harness",
        version=__version__,
        description="Local observability and human-decision API for a governed harness workspace.",
    )
    notices: list[str] = []
    api.state.auth_notices = notices
    audit: DecisionAuditLog | None = None
    if settings.enabled:
        source = os.environ if environ is None else environ
        token = start_token if start_token is not None else source.get(settings.start_token_env)
        authenticator = TokenAuthenticator.from_settings(settings, token, source, notices)
        if not authenticator.users:
            notices.append("no API token is available: every request will be refused (401)")
        api.add_middleware(
            AuthenticationMiddleware, authenticator=authenticator, sign_in_page=_SIGN_IN
        )
        audit = DecisionAuditLog.for_workspace(root)
    hosts = trusted_hosts(root)
    if hosts is not None:
        # A page on another origin can reach a loopback server through DNS rebinding; requests
        # whose Host header is not a configured name are answered with 400. Added last, it runs
        # before the authentication.
        api.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))

    @api.get("/api/session")
    def session(request: Request) -> dict[str, object]:
        """Who this request is authenticated as (``authentication: off`` without the ``api``
        section, or with ``api.auth: off``)."""
        principal = _authorize(settings, request, "viewer")
        if principal is None:
            return {"authentication": "off"}
        return {"authentication": "token", "userId": principal.user_id, "role": principal.role}

    @api.get("/api/config")
    def config(request: Request) -> dict[str, object]:
        """``harness config validate`` of the workspace (role ``admin``)."""
        _authorize(settings, request, "admin")
        try:
            return application.validate_config(root)
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.get("/api/health")
    def health() -> dict[str, object]:
        return application.doctor(root)

    @api.get("/api/runs")
    def runs() -> list[dict[str, object]]:
        try:
            return [
                item.model_dump(mode="json", by_alias=True) for item in application.list_runs(root)
            ]
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.get("/api/runs/{execution_id}")
    def run_status(execution_id: str) -> dict[str, object]:
        try:
            return application.status(root, execution_id)
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.get("/api/runs/{execution_id}/review")
    def run_review(execution_id: str, diff: bool = False) -> dict[str, object]:
        try:
            return application.review(root, execution_id, include_diff=diff)
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.get("/api/runs/{execution_id}/trace")
    def run_trace(
        execution_id: str, format: Literal["json", "markdown", "jsonl", "sarif"] = "json"
    ) -> Response:
        try:
            data = application.trace(root, execution_id, format)
        except IntegrityError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        media = {
            "json": "application/json",
            "markdown": "text/markdown",
            "jsonl": "application/x-ndjson",
            "sarif": "application/sarif+json",
        }[format]
        return Response(data, media_type=media)

    @api.get("/api/runs/{execution_id}/evidence")
    def run_evidence(execution_id: str) -> list[dict[str, object]]:
        try:
            return application.list_evidence(root, execution_id)
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.get("/api/runs/{execution_id}/retrospective")
    def run_retrospective(execution_id: str) -> dict[str, object]:
        try:
            return application.retrospect(root, execution_id).model_dump(mode="json", by_alias=True)
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.get("/api/runs/{execution_id}/findings")
    def run_findings(execution_id: str) -> list[dict[str, object]]:
        try:
            return [
                item.model_dump(mode="json", by_alias=True)
                for item in application.list_findings(root, execution_id)
            ]
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.post("/api/runs/{execution_id}/decision")
    def decide(execution_id: str, request: DecisionRequest, http: Request) -> dict[str, object]:
        actor_id = request.actor_id
        audited = _Audited(audit, http, execution_id, request)
        principal = _authorize(settings, http, "viewer")
        if principal is not None:
            # The authenticated person is the decider; the audit attempt is written before
            # anything is decided (if it cannot be written, nothing is).
            audited.attempt(principal)
            if not principal.allows("reviewer"):
                audited.refuse(
                    403,
                    f"user {principal.user_id} has the role {principal.role}; recording a "
                    "decision needs the role reviewer or admin",
                )
            if request.actor_id and request.actor_id != principal.user_id:
                audited.refuse(
                    403,
                    f"actor_id {request.actor_id!r} is not the authenticated user "
                    f"{principal.user_id!r}; leave it out or send {principal.user_id!r}",
                )
            actor_id = principal.user_id
        try:
            acting = HarnessApplication()  # its notices belong to this request only
            decision, execution = acting.decide_gate(
                root,
                execution_id=execution_id,
                decision=request.decision,
                change_set_digest=request.change_set_digest,
                actor_id=actor_id,
                rationale=request.rationale,
                continue_after=request.continue_after,
                default_actor=DEFAULT_API_ACTOR,
                exception=ExceptionOptions(
                    expires_in=request.expires_in,
                    expires_at=request.expires_at,
                    scope=request.scope,
                    alternative_evidence=request.alternative_evidence,
                    follow_up=request.follow_up,
                ),
                checked_items=request.checked_items,
            )
            body: dict[str, object] = {
                "decision": decision.model_dump(mode="json", by_alias=True),
                "execution": execution.model_dump(mode="json", by_alias=True),
            }
            if acting.notices:
                body["warnings"] = list(acting.notices)
        except NonHumanActorError as error:
            audited.refuse(403, str(error), error)
        except Exception as error:
            audited.refuse(409, str(error), error)
        audited.recorded(decision.decision_id, decision.actor.actor_id)
        return body

    @api.get("/api/runs/{execution_id}/verification")
    def run_verification(execution_id: str) -> dict[str, object]:
        try:
            return application.verification(root, execution_id)
        except Exception as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.get("/api/registry")
    def registry() -> dict[str, object]:
        """The projects of the shared run registry (``runtime.stateDir``, #55)."""
        try:
            return application.registry()
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.get("/api/inbox")
    def pending() -> list[dict[str, object]]:
        try:
            return application.inbox(root)
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.get("/api/exceptions")
    def exceptions(status: Literal["all", "active", "expired"] = "all") -> list[dict[str, object]]:
        try:
            return application.list_exceptions(root, status=status)
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _dashboard_html()

    return api


def _authorize(settings: ApiConfig, request: Request, role: ApiRole) -> Principal | None:
    """The authenticated person when they hold ``role`` (403 otherwise); ``None`` when the
    API runs without authentication (no ``api`` section, or ``api.auth: off``)."""
    if not settings.enabled:
        return None
    principal = request.scope.get(PRINCIPAL_SCOPE_KEY)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="authentication required")
    if not principal.allows(role):
        raise HTTPException(
            status_code=403,
            detail=f"user {principal.user_id} has the role {principal.role}; this needs {role}",
        )
    return principal


class _Audited:
    """The audit records of one API decision, written only when authentication is on: an
    attempt before anything is decided and its outcome. Never a token."""

    def __init__(
        self,
        log: DecisionAuditLog | None,
        http: Request,
        execution_id: str,
        request: DecisionRequest,
    ) -> None:
        self.log = log
        self.record: dict[str, object] = {
            "route": f"{http.method} {http.url.path}",
            "executionId": execution_id,
            "decision": request.decision.value,
            "changeSetDigest": request.change_set_digest,
            "requestedActorId": request.actor_id or None,
            "clientHost": http.client.host if http.client else None,
        }

    def _append(self, **fields: object) -> None:
        if self.log is None:
            return
        try:
            self.log.append({**self.record, **fields})
        except OSError as error:
            raise HTTPException(
                status_code=500,
                detail=f"cannot write the API audit log {self.log.path}: {error.strerror}",
            ) from error

    def attempt(self, principal: Principal) -> None:
        self.record["userId"] = principal.user_id
        self.record["role"] = principal.role
        self._append(outcome="attempted")

    def refuse(self, status: int, detail: str, cause: BaseException | None = None) -> NoReturn:
        self._append(outcome="refused", status=status, reason=detail)
        raise HTTPException(status_code=status, detail=detail) from cause

    def recorded(self, decision_id: str, actor_id: str) -> None:
        self._append(outcome="recorded", status=200, decisionId=decision_id, actorId=actor_id)


def _dashboard_html() -> str:
    return _DASHBOARD


_DASHBOARD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Governed Agent Harness</title>
<style>
:root { font-family: Inter, ui-sans-serif, system-ui, sans-serif; color-scheme: dark; }
body { margin: 0; background: #111827; color: #e5e7eb; }
header { padding: 24px 32px; background: #0f172a; border-bottom: 1px solid #334155; }
main { display: grid; grid-template-columns: minmax(300px, 34%) 1fr; gap: 20px; padding: 24px 32px; }
section { background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 18px; min-width: 0; }
h1,h2,h3 { margin: 0 0 12px; } h3 { margin-top: 18px; font-size: 15px; }
button,input,textarea { box-sizing: border-box; border: 1px solid #475569; border-radius: 8px; background: #111827; color: inherit; padding: 10px; }
button { cursor: pointer; } .run { width: 100%; text-align: left; margin: 6px 0; }
.actions { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin-top: 12px; }
label { display: block; margin-top: 10px; font-size: 13px; } input,textarea { width: 100%; margin-top: 4px; }
textarea { min-height: 80px; resize: vertical; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; max-height: 50vh; overflow: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
td,th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #374151; vertical-align: top; }
ul { margin: 4px 0; padding-left: 20px; } li { margin: 2px 0; } code { overflow-wrap: anywhere; }
.status { font-weight: 700; } .PASSED { color: #34d399; } .BLOCKED,.FAILED,.INCONCLUSIVE { color: #fbbf24; } .ERROR { color: #fb7185; }
.blocks { color: #fb7185; font-weight: 700; }
.notice { margin-top: 10px; padding: 9px; border-radius: 8px; background: #0f172a; font-size: 13px; }
@media (max-width: 850px) { main { grid-template-columns: 1fr; padding: 16px; } .actions { grid-template-columns: 1fr 1fr; } }
</style>
</head>
<body>
<header><h1>Governed Agent Harness</h1><div>Local observability and explicit, digest-bound human decisions.</div><div id="session" class="notice"></div></header>
<main>
<section><h2>Waiting for a person</h2><div id="inbox">Loading...</div>
<h2 style="margin-top:20px">Executions</h2><div id="runs">Loading...</div>
<h2 style="margin-top:20px">Repositories</h2><div id="registry">Loading...</div>
<div class="notice">Refreshes every 5 seconds.</div></section>
<section><h2>Selected execution</h2><div id="decision"></div><div id="brief">Select an execution.</div>
<details><summary>Status (JSON)</summary><pre id="detail"></pre></details></section>
</main>
<script>
let selected = null; let brief = null; let selectedId = null; let decisionKey = null; let session = {authentication:'off'};
const TOKEN_KEY='governed-harness.api-token';
function esc(value){ return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function apiToken(){ try { return sessionStorage.getItem(TOKEN_KEY); } catch(error){ return null; } }
function signOut(){ try { sessionStorage.removeItem(TOKEN_KEY); } catch(error){} location.reload(); }
// Every call of the dashboard goes through request(): it sends the token kept in this tab
// (api.auth: token) as Authorization: Bearer, and signs out when the server refuses it.
async function request(url, options){
 const init={...(options||{})}; const headers=new Headers(init.headers||{}); const token=apiToken();
 if(token) headers.set('Authorization','Bearer '+token); init.headers=headers;
 const response=await fetch(url, init);
 if(response.status===401 && token){ signOut(); throw new Error('The API token was refused; sign in again.'); }
 const body=await response.json();
 if(!response.ok) throw new Error(body.detail || JSON.stringify(body)); return body;
}
async function loadSession(){
 session=await request('/api/session'); const target=document.getElementById('session');
 if(session.authentication==='token'){ target.innerHTML=`Signed in as <code>${esc(session.userId)}</code> (${esc(session.role)}) <button id="sign-out">Sign out</button>`; document.getElementById('sign-out').onclick=signOut; }
 else { target.textContent='No authentication (no api section in project.yaml): keep the server on 127.0.0.1.'; }
}
async function loadRuns(){
 const runs=await request('/api/runs'); const target=document.getElementById('runs'); target.innerHTML='';
 for(const run of runs){ const button=document.createElement('button'); button.className='run';
  button.innerHTML=`<span class="status ${esc(run.status)}">${esc(run.status)}</span> ${esc(run.executionId)}<br><small>${esc(run.currentPhase)}</small>`;
  button.onclick=()=>loadRun(run.executionId); target.appendChild(button); }
 if(!runs.length) target.textContent='No executions recorded.';
}
async function loadRegistry(){
 const value=await request('/api/registry'); const target=document.getElementById('registry');
 target.innerHTML=value.projects.length?list(value.projects, p=>`<li><code>${esc(p.projectId)}</code> ${esc(p.workspace)}: ${p.runs.length} run(s)${p.runs.length?`, latest ${esc(p.runs[0].status)} in ${esc(p.runs[0].currentPhase)}`:''}</li>`):'No registry outside the workspaces (runtime.stateDir).';
}
async function loadInbox(){
 const items=await request('/api/inbox'); const target=document.getElementById('inbox'); target.innerHTML='';
 for(const item of items){ const button=document.createElement('button'); button.className='run';
  const state=item.kind==='decision'?`gate ${esc(item.gateStatus)}, ${item.blockingFindings} blocking`:item.kind==='deferred'?`${esc(item.itemId)} ${esc(item.status)}${item.warning?', '+esc(item.warning):''}`:item.kind==='preflight'?'preflight UNAVAILABLE':`${item.questions} question(s)`;
  button.innerHTML=`<b>${esc(item.kind)}</b> ${esc(item.taskTitle)}<br><small>${esc(item.executionId)} - ${state} - ${item.waitingHours} h</small>`;
  button.onclick=()=>loadRun(item.executionId); target.appendChild(button); }
 if(!items.length) target.textContent='Nothing waits for a person.';
}
async function loadRun(id){
 selectedId=id;
 selected=await request('/api/runs/'+encodeURIComponent(id));
 brief=await request('/api/runs/'+encodeURIComponent(id)+'/review');
 document.getElementById('detail').textContent=JSON.stringify(selected,null,2);
 renderBrief(brief);
 const key=`${id}|${brief.run.awaitingDecision}|${brief.run.changeSetDigest}`;
 if(key!==decisionKey){ decisionKey=key; renderDecision(id, brief); }
}
async function refresh(){
 try { await loadInbox(); await loadRuns(); await loadRegistry(); if(selectedId){ await loadRun(selectedId); } }
 catch(error){ document.getElementById('runs').textContent=error.message; }
}
function list(items, render){ return items.length ? '<ul>'+items.map(render).join('')+'</ul>' : '<div>none</div>'; }
function renderBrief(b){
 const files=b.changed.files.map(f=>`<tr><td>${esc(f.status)}</td><td><code>${esc(f.path)}</code></td><td>+${f.additions} -${f.deletions}</td></tr>`).join('');
 const risks=b.risks.map(r=>`<tr><td class="${r.blocking?'blocks':''}">${r.blocking?'blocks':'info'}</td><td>${esc(r.severity)}</td><td><code>${esc(r.ruleId)}</code></td><td><code>${esc(r.location)}</code></td><td>${esc(r.message)}</td></tr>`).join('');
 const checks=b.verified.validations.map(v=>`<tr><td class="${esc(v.status)}">${esc(v.status)}</td><td><code>${esc(v.validatorId)}</code></td><td>${v.mandatory?'mandatory':'optional'}</td><td>${v.attempts}</td></tr>`).join('');
 let delta='';
 if(b.delta){ const d=b.delta; delta=`<h3>Since the last decision (${esc(d.sinceDecision.decision)} by ${esc(d.sinceDecision.actorId)})</h3>`+
  (d.unchanged?'<div>Same ChangeSet as that decision.</div>':
  `<div>Files added: ${esc(d.files.added.join(', ')||'none')}; changed: ${esc(d.files.changed.join(', ')||'none')}; removed: ${esc(d.files.removed.join(', ')||'none')}</div>`+
  `<div>Findings resolved: ${d.findingsResolved.length}, new: ${d.findingsNew.length}</div>`); }
 const exceptions=(b.exceptions||[]).map(e=>`<li><code>${esc(e.exceptionId)}</code> until ${esc(e.expiresAt)}: ${esc(e.rationale)}</li>`).join('');
 document.getElementById('brief').innerHTML=
  `<div>Run <code>${esc(b.run.executionId)}</code>: <span class="status ${esc(b.run.status)}">${esc(b.run.status)}</span> in ${esc(b.run.currentPhase)}</div>`+
  `<h3>What was asked</h3><div><b>${esc(b.asked.title)}</b></div><div>${esc(b.asked.intent)}</div>`+
  list(b.asked.acceptanceCriteria, c=>`<li><code>${esc(c.criterionId)}</code> ${esc(c.text)}</li>`)+
  `<h3>What changed</h3><table>${files||'<tr><td>No ChangeSet yet.</td></tr>'}</table>`+
  `<h3>Gate ${b.gate?esc(b.gate.status):'not evaluated'}</h3>`+(b.gate?list(b.gate.reasons, r=>`<li>${esc(r.explanation)}</li>`):'')+
  `<h3>Risks (${b.risks.length})</h3><table>${risks||'<tr><td>No current finding.</td></tr>'}</table>`+
  `<h3>Verified on <code>${esc(b.verified.changeSetDigest)}</code></h3><table>${checks}</table>`+
  `<h3>Not verified</h3>`+list(b.notVerified, t=>`<li>${esc(t)}</li>`)+
  (exceptions?`<h3>Exceptions in force</h3><ul>${exceptions}</ul>`:'')+
  (b.certification?`<h3>Certification ${esc(b.certification.status)}</h3>`+list(b.certification.criteria, c=>`<li><code>${esc(c.criterionId)}</code> ${esc(c.status)}: requires ${esc(c.required)}, reached ${esc(c.achieved||'no rung')}</li>`):'')+
  (b.deferred?`<h3>Deferred verification</h3>`+list(b.deferred, d=>`<li><code>${esc(d.itemId)}</code> ${esc(d.status)} (${esc(d.where)})</li>`):'')+
  `<h3>History</h3><div>${b.history.implementationAttempts} implementation and ${b.history.verificationAttempts} verification attempt(s), ${b.history.automaticCorrections} automatic correction(s), ${b.history.requestedChanges} requested change(s), ${b.history.providerRetries} provider retry(ies).</div>`+delta;
}
function renderDecision(id, b){
 const target=document.getElementById('decision'); target.innerHTML='';
 if(!b.run.awaitingDecision || !b.run.changeSetDigest){ return; }
 target.innerHTML=`<h3>Human gate</h3>
 <div class="notice">The decision will be bound to <code>${esc(b.run.changeSetDigest)}</code> (${b.changed.files.length} file(s)). Any later change invalidates it.</div>
 ${session.authentication==='token'?`<label>Actor<input id="actor" value="${esc(session.userId)}" readonly></label>`:'<label>Actor<input id="actor" value="" placeholder="Git user or human.web"></label>'}
 <label>Rationale<textarea id="rationale" placeholder="Explain the evidence considered and the reason for the decision. For REQUEST_CHANGES, say what must change."></textarea></label>
 ${(b.checklist||[]).map(c=>`<label><input type="checkbox" class="check" value="${esc(c.itemId)}" style="width:auto"> ${esc(c.itemId)}: ${esc(c.text)}</label>`).join('')}
 <div class="actions">
  <button data-decision="APPROVE">Approve</button>
  <button data-decision="REQUEST_CHANGES">Request changes</button>
  <button data-decision="APPROVE_EXCEPTION">Approve exception</button>
  <button data-decision="REJECT">Reject</button>
 </div>`;
 for(const button of target.querySelectorAll('button[data-decision]')){ button.onclick=()=>decide(id, button.dataset.decision); }
}
async function decide(id, decision){
 const rationale=document.getElementById('rationale').value.trim();
 if(!rationale){ alert('A rationale is required.'); return; }
 const digest=brief.run.changeSetDigest;
 const files=brief.changed.files.map(f=>f.path).join('\\n');
 if(!confirm(`${decision} for\\n${digest}\\n\\nFiles:\\n${files}\\n\\nRecord this decision?`)) return;
 try {
  await request('/api/runs/'+encodeURIComponent(id)+'/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
   decision,change_set_digest:digest,actor_id:document.getElementById('actor').value,rationale,continue_after:true,
   checked_items:[...document.querySelectorAll('input.check:checked')].map(i=>i.value)
  })});
  decisionKey=null; await refresh();
 } catch(error){ alert(error.message); }
}
loadSession().catch(error => { document.getElementById('session').textContent=error.message; });
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>"""

_SIGN_IN = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Governed Agent Harness: sign in</title>
<style>
:root { font-family: Inter, ui-sans-serif, system-ui, sans-serif; color-scheme: dark; }
body { margin: 0; background: #111827; color: #e5e7eb; display: grid; place-items: center; min-height: 100vh; }
form { background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 24px; width: min(420px, 90vw); }
input,button { box-sizing: border-box; width: 100%; margin-top: 8px; border: 1px solid #475569; border-radius: 8px; background: #111827; color: inherit; padding: 10px; }
#error { color: #fb7185; min-height: 1.2em; margin-top: 8px; font-size: 13px; }
</style>
</head>
<body>
<form id="sign-in">
<h1>Governed Agent Harness</h1>
<label>API token<input id="token" type="password" autocomplete="off" required></label>
<button type="submit">Sign in</button>
<div id="error"></div>
<p style="font-size:13px">The token is kept in this tab only (sessionStorage) and sent as Authorization: Bearer.</p>
</form>
<script>
(() => {
 const key='governed-harness.api-token';
 async function open(token){
  const response=await fetch('/', {headers:{'Authorization':'Bearer '+token}, cache:'no-store'});
  if(!response.ok){
   try { sessionStorage.removeItem(key); } catch(error){}
   document.getElementById('error').textContent=response.status===401?'The token was refused.':'The server answered '+response.status+'.';
   return;
  }
  const page=await response.text();
  try { sessionStorage.setItem(key, token); } catch(error){}
  document.open(); document.write(page); document.close();
 }
 let kept=null; try { kept=sessionStorage.getItem(key); } catch(error){}
 if(kept) open(kept);
 document.getElementById('sign-in').onsubmit=(event)=>{ event.preventDefault(); open(document.getElementById('token').value.trim()); };
})();
</script>
</body>
</html>"""
