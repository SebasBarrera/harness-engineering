# Traceability model

The minimal causal chain is:

```text
Task
  -> Requirement / AcceptanceCriterion
  -> SpecificationVersion
  -> PlanVersion
  -> ImplementationAttempt
  -> AgentInvocation / ToolInvocation
  -> ChangeSet
  -> Evidence
  -> ValidationResult / Finding
  -> GateEvaluation
  -> HumanDecision
  -> Closure
  -> RetrospectiveObservation / Recommendation
```

Every edge records relation type, source actor, timestamp and provenance. References point to immutable IDs and digests rather than mutable filenames alone.

## Mandatory relations

- every changed path is owned by one implementation attempt;
- every validation result names the ChangeSet digest it evaluated;
- every finding references evidence and the evaluator identity;
- every gate evaluation references policy, workflow and configuration digests;
- every human decision references the exact gate evaluation and ChangeSet digest;
- every recommendation references observations from closed runs.
