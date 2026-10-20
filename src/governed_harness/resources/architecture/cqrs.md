---
id: cqrs
title: Command Query Responsibility Segregation
purpose: Use separate models for changing state (commands) and for reading it (queries), so each can be shaped and scaled for its own job.
fits:
  - reads and writes with very different shapes or load
  - rich write rules next to many read-optimised views
  - systems that already publish events or may add event sourcing
benefits:
  - read models are simple and fast, write models stay focused on rules
  - reads and writes can be optimised and scaled separately
costs:
  - two models to maintain, and possible lag between write and read side
  - unnecessary for plain CRUD
layers:
  - name: domain
    paths: ["**/domain/**"]
  - name: commands
    paths: ["**/commands/**", "**/write/**"]
  - name: queries
    paths: ["**/queries/**", "**/read/**"]
allow:
  commands: [domain]
  queries: []
---
## Purpose

A command changes state and returns nothing but an outcome; a query returns data and changes nothing. Each side has its own model, handlers and, optionally, its own store.

## When it fits

- Many read views that do not match the write model.
- Heavy read load or strict business rules on writes that justify separation.
- Systems that publish events from the write side to build read models.

## Structure

- `domain/`: the write model, aggregates and rules used by commands.
- `commands/` (or `write/`): command types, command handlers and validation.
- `queries/` (or `read/`): query types, query handlers and read models (projections), optimised for display.
- A projection step updates read models from events or from the write store.

## Best practices

- A command handler changes one aggregate and never returns read data.
- A query handler never changes state and never uses the write model.
- Name commands as imperatives (`PlaceOrder`) and queries by what they return.
- Update read models from events and design them to be rebuilt from scratch.
- Accept and document eventual consistency between sides; show it honestly in the interface.

## Dependency rules

- commands may use domain.
- queries depend on read models only and never on commands or domain.
- commands and queries never import each other.

## Expert prompt

You are the expert in CQRS for this project. Organise the code in domain (the write model), commands and queries. A command is an imperative request handled by one handler that changes one aggregate and returns only an outcome. A query is handled by a handler that reads from a read model built for the view and never changes state. Commands and queries never import each other and queries never use the domain model. Update read models through projections driven by events and keep them rebuildable. State where eventual consistency applies and test the projections. When a task adds a screen or report, add a query and its read model; when it adds a rule, change the command side. Before finishing, check that no query handler writes and no command handler returns read data.
