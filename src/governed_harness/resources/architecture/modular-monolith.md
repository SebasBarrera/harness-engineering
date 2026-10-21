---
id: modular-monolith
title: Modular monolith
purpose: Build one deployable application out of independent modules with explicit public interfaces, so it stays easy to change and could be split later.
fits:
  - a team that wants one deployment but needs clear boundaries between features
  - a product that is not yet large enough to pay the cost of distributed services
  - a domain that is expected to be split later if some part needs to scale on its own
benefits:
  - one deployment and one database to operate, with module boundaries enforced in code
  - modules can become services later with little rework
costs:
  - needs discipline and tooling to keep module boundaries from eroding
  - a shared database invites hidden coupling
layers: []
allow: {}
---
## Purpose

Split the code by business capability into modules that expose a small public interface and hide the rest. All modules run in one process and one deployment.

## When it fits

- Early and mid-stage products with one team or a few.
- Systems whose boundaries are still being discovered.
- Teams that want most of the benefits of microservices without distributed operations.

## Structure

- One package per module (`orders/`, `catalog/`, `billing/`), each with a public entry point (`api` or `__init__`) and private internals.
- Each module owns its data: its own tables or schema, and no other module reads them directly.
- A thin `shared/` kernel holds only truly cross-cutting types (identifiers, money, time).
- Modules talk through their public interface or through in-process events.

## Best practices

- Import only another module's public interface, never its internals.
- Keep module dependencies acyclic; a cycle means the boundary is wrong.
- Give each module its own tests and let its public interface be the contract tested.
- Use in-process events for reactions between modules to avoid direct calls.
- Review the dependency graph regularly and record the allowed module dependencies as layers in the project configuration.

## Dependency rules

Modules and their allowed dependencies are specific to the project: the harness records them as layers once the modules are known. A module may use another module only through its public interface, and the graph of modules has no cycle.

## Expert prompt

You are the expert in modular monolith architecture for this project. Organise the code in one package per business capability, each with a small public interface and private internals. A module imports another module only through that module's public interface, never its internals, and module dependencies never form a cycle. Each module owns its data and no other module reads it directly. Keep a small shared kernel for truly cross-cutting types only. Use in-process events for reactions between modules. For each task, say which module it belongs to; if it touches two modules, go through the public interface of the second. When a new module is created, list its allowed dependencies so they can be recorded as layer rules. Before finishing, check that no import reaches into another module's internals.
