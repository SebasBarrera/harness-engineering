from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict

from governed_harness import __version__
from governed_harness.application import HarnessApplication
from governed_harness.domain.enums import DecisionKind


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionKind
    change_set_digest: str
    actor_id: str = "human.web"
    rationale: str
    continue_after: bool = True


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
            )
            return {
                "decision": decision.model_dump(mode="json", by_alias=True),
                "execution": execution.model_dump(mode="json", by_alias=True),
            }
        except Exception as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _dashboard_html()

    return api


def _dashboard_html() -> str:
    return """<!doctype html>
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
section { background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 18px; }
h1,h2,h3 { margin: 0 0 12px; }
button,input,textarea { box-sizing: border-box; border: 1px solid #475569; border-radius: 8px; background: #111827; color: inherit; padding: 10px; }
button { cursor: pointer; } .run { width: 100%; text-align: left; margin: 6px 0; }
.actions { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; margin-top: 12px; }
label { display: block; margin-top: 10px; font-size: 13px; } input,textarea { width: 100%; margin-top: 4px; }
textarea { min-height: 80px; resize: vertical; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; max-height: 65vh; overflow: auto; }
.status { font-weight: 700; } .PASSED { color: #34d399; } .BLOCKED,.FAILED,.INCONCLUSIVE { color: #fbbf24; } .ERROR { color: #fb7185; }
.notice { margin-top: 10px; padding: 9px; border-radius: 8px; background: #0f172a; font-size: 13px; }
@media (max-width: 850px) { main { grid-template-columns: 1fr; padding: 16px; } .actions { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header><h1>Governed Agent Harness</h1><div>Local observability and explicit, digest-bound human decisions.</div></header>
<main>
<section><h2>Executions</h2><div id="runs">Loading...</div></section>
<section><h2>Selected execution</h2><div id="decision"></div><pre id="detail">Select an execution.</pre></section>
</main>
<script>
let selected = null;
async function request(url, options){
 const response=await fetch(url, options); const body=await response.json();
 if(!response.ok) throw new Error(body.detail || JSON.stringify(body)); return body;
}
async function loadRuns(){
 const runs=await request('/api/runs'); const target=document.getElementById('runs'); target.innerHTML='';
 for(const run of runs){ const button=document.createElement('button'); button.className='run';
  button.innerHTML=`<span class="status ${run.status}">${run.status}</span> ${run.executionId}<br><small>${run.currentPhase}</small>`;
  button.onclick=()=>loadRun(run.executionId); target.appendChild(button); }
 if(!runs.length) target.textContent='No executions recorded.';
}
async function loadRun(id){
 selected=await request('/api/runs/'+id);
 document.getElementById('detail').textContent=JSON.stringify(selected,null,2);
 renderDecision(id, selected);
}
function renderDecision(id, status){
 const target=document.getElementById('decision'); target.innerHTML='';
 const run=status.execution;
 if(run.currentPhase !== 'DECISION' || !run.changeSetDigest){ return; }
 target.innerHTML=`<h3>Human gate</h3>
 <div class="notice">The decision will be bound to <code>${run.changeSetDigest}</code>. Any later change invalidates it.</div>
 <label>Actor<input id="actor" value="human.web"></label>
 <label>Rationale<textarea id="rationale" placeholder="Explain the evidence considered and the reason for the decision."></textarea></label>
 <div class="actions">
  <button onclick="decide('${id}','APPROVE')">Approve</button>
  <button onclick="decide('${id}','APPROVE_EXCEPTION')">Approve exception</button>
  <button onclick="decide('${id}','REJECT')">Reject</button>
 </div>`;
}
async function decide(id, decision){
 const rationale=document.getElementById('rationale').value.trim();
 if(!rationale){ alert('A rationale is required.'); return; }
 try {
  await request('/api/runs/'+id+'/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
   decision,change_set_digest:selected.execution.changeSetDigest,actor_id:document.getElementById('actor').value,rationale,continue_after:true
  })});
  await loadRuns(); await loadRun(id);
 } catch(error){ alert(error.message); }
}
loadRuns().catch(error=>document.getElementById('runs').textContent=error.message);
</script>
</body>
</html>"""
