# ADR 0004: Trust and capabilities

- Status: Accepted provisionally
- Date: 2026-08-30

## Decision

Use explicit, scoped and expiring capability grants. Default posture is deny.

Initial capability names:

- `filesystem.read`
- `filesystem.write`
- `process.execute`
- `network.connect`
- `git.read`
- `git.stage`
- `git.commit`
- `git.push`
- `secrets.use`
- `mcp.invoke`
- `artifact.publish`
- `approval.request`

Every privileged invocation records actor, scope, grant ID, decision, timestamp and result. Project configuration may narrow grants; it cannot broaden locked organizational or core restrictions.

## Consequences

The process runner accepts only structured command specifications. Secrets are referenced by handles and must not appear in prompts, command lines, events or unencrypted artifacts.
