---
id: ddd
title: Domain-driven design
purpose: Model a complex business domain with a shared language, bounded contexts and rich domain objects, and keep that model free of technical concerns.
fits:
  - a complex domain with many rules that domain experts can describe
  - a team that can work with domain experts and agree on a ubiquitous language
  - a system that will grow into several bounded contexts
benefits:
  - the code speaks the language of the business
  - rules live in one model, not scattered across services
costs:
  - high modelling effort that does not pay off in simple CRUD
  - requires access to domain knowledge and discipline in naming
layers:
  - name: domain
    paths: ["**/domain/**"]
  - name: application
    paths: ["**/application/**"]
  - name: infrastructure
    paths: ["**/infrastructure/**"]
  - name: interfaces
    paths: ["**/interfaces/**", "**/api/**"]
allow:
  application: [domain]
  infrastructure: [application, domain]
  interfaces: [application, domain]
---
## Purpose

Put the business model at the centre. Aggregates, entities, value objects and domain events express the rules in the language of the domain; each bounded context has its own model.

## When it fits

- Domains with intricate rules, states and invariants.
- Products where domain experts and developers collaborate closely.
- Systems that will be split in contexts that talk through explicit contracts.

## Structure

- One top-level package per bounded context (`billing/`, `shipping/`), each with its own layers.
- `domain/`: aggregates, entities, value objects, domain services, domain events, repository interfaces.
- `application/`: use cases that load aggregates, call their behaviour and persist them in one transaction.
- `infrastructure/`: repository implementations, messaging, external clients.
- `interfaces/` (or `api/`): controllers, command line, consumers.

## Best practices

- Name types and methods with the ubiquitous language; avoid generic names such as `Manager`.
- One aggregate per transaction; reference other aggregates by identifier.
- Put invariants inside the aggregate, not in application services.
- Prefer value objects to primitive types for domain concepts.
- Contexts talk through published events or explicit contracts, never through each other's internals.

## Dependency rules

- domain depends on nothing.
- application may use domain.
- infrastructure and interfaces may use application and domain.

## Expert prompt

You are the expert in domain-driven design for this project. Identify the bounded contexts and give each its own package with domain, application, infrastructure and interfaces layers. In the domain layer model aggregates, entities, value objects and domain events with the names the business uses, and keep every invariant inside the aggregate; it imports nothing technical. Application services orchestrate one use case and one transaction per aggregate and hold no business rules. Repository interfaces belong to the domain, their implementations to infrastructure. Reference other aggregates by identifier and let contexts talk through events or explicit contracts. Record every new domain term in the code names and in the module documentation. Before finishing, check that the domain layer has no technical imports.
