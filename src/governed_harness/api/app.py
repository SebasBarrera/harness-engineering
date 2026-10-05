from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict

from governed_harness import __version__
from governed_harness.application import HarnessApplication
from governed_harness.application.exceptions import ExceptionOptions
from governed_harness.domain.enums import DecisionKind


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionKind
    change_set_digest: str
    actor_id: str = "human.web"
    rationale: str
    continue_after: bool = True
    expires_in: str | None = None
    expires_at: str | None = None
    scope: tuple[str, ...] = ()
    alternative_evidence: str | None = None
    follow_up: str | None = None


def create_app(workspace: Path) -> FastAPI:
    root = workspace.resolve(strict=True)
    application = HarnessApplication()
    api = FastAPI(
        title="Governed Agent Harness",
        version=__version__,
        description="Local observability and human-decision API for a governed harness workspace.",
    )

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
    def decide(execution_id: str, request: DecisionRequest) -> dict[str, object]:
        try:
            decision, execution = application.decide_gate(
                root,
                execution_id=execution_id,
                decision=request.decision,
                change_set_digest=request.change_set_digest,
                actor_id=request.actor_id,
                rationale=request.rationale,
                continue_after=request.continue_after,
                exception=ExceptionOptions(
                    expires_in=request.expires_in,
                    expires_at=request.expires_at,
                    scope=request.scope,
                    alternative_evidence=request.alternative_evidence,
                    follow_up=request.follow_up,
                ),
            )
            return {
                "decision": decision.model_dump(mode="json", by_alias=True),
                "execution": execution.model_dump(mode="json", by_alias=True),
            }
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

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
<header><h1>Governed Agent Harness</h1><div>Local observability and explicit, digest-bound human decisions.</div></header>
<main>
<section><h2>Executions</h2><div id="runs">Loading...</div></section>
<section><h2>Selected execution</h2><div id="decision"></div><div id="brief">Select an execution.</div>
<details><summary>Status (JSON)</summary><pre id="detail"></pre></details></section>
</main>
<script>
let selected = null; let brief = null;
function esc(value){ return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function request(url, options){
 const response=await fetch(url, options); const body=await response.json();
 if(!response.ok) throw new Error(body.detail || JSON.stringify(body)); return body;
}
async function loadRuns(){
 const runs=await request('/api/runs'); const target=document.getElementById('runs'); target.innerHTML='';
 for(const run of runs){ const button=document.createElement('button'); button.className='run';
  button.innerHTML=`<span class="status ${esc(run.status)}">${esc(run.status)}</span> ${esc(run.executionId)}<br><small>${esc(run.currentPhase)}</small>`;
  button.onclick=()=>loadRun(run.executionId); target.appendChild(button); }
 if(!runs.length) target.textContent='No executions recorded.';
}
async function loadRun(id){
 selected=await request('/api/runs/'+encodeURIComponent(id));
 brief=await request('/api/runs/'+encodeURIComponent(id)+'/review');
 document.getElementById('detail').textContent=JSON.stringify(selected,null,2);
 renderBrief(brief); renderDecision(id, brief);
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
  `<h3>History</h3><div>${b.history.implementationAttempts} implementation and ${b.history.verificationAttempts} verification attempt(s), ${b.history.automaticCorrections} automatic correction(s), ${b.history.requestedChanges} requested change(s), ${b.history.providerRetries} provider retry(ies).</div>`+delta;
}
function renderDecision(id, b){
 const target=document.getElementById('decision'); target.innerHTML='';
 if(!b.run.awaitingDecision || !b.run.changeSetDigest){ return; }
 target.innerHTML=`<h3>Human gate</h3>
 <div class="notice">The decision will be bound to <code>${esc(b.run.changeSetDigest)}</code> (${b.changed.files.length} file(s)). Any later change invalidates it.</div>
 <label>Actor<input id="actor" value="human.web"></label>
 <label>Rationale<textarea id="rationale" placeholder="Explain the evidence considered and the reason for the decision. For REQUEST_CHANGES, say what must change."></textarea></label>
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
   decision,change_set_digest:digest,actor_id:document.getElementById('actor').value,rationale,continue_after:true
  })});
  await loadRuns(); await loadRun(id);
 } catch(error){ alert(error.message); }
}
loadRuns().catch(error=>document.getElementById('runs').textContent=error.message);
</script>
</body>
</html>"""
