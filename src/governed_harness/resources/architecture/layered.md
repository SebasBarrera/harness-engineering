---
id: layered
title: Layered architecture
purpose: Separate presentation, business logic and data access into stacked layers where each layer uses only the one below it.
fits:
  - a small or medium team that wants a structure everyone already knows
  - CRUD-heavy or form-driven applications with modest business rules
  - a single deployable unit with one data store
benefits:
  - familiar to almost every developer, cheap to start
  - clear place for every kind of code
costs:
  - business rules tend to leak into the data or presentation layer over time
  - a change to one feature crosses every layer
layers:
  - name: presentation
    paths: ["**/presentation/**", "**/api/**", "**/web/**"]
  - name: services
    paths: ["**/services/**", "**/service/**"]
  - name: data
    paths: ["**/data/**", "**/repositories/**", "**/persistence/**"]
allow:
  presentation: [services]
  services: [data]
---
## Purpose

Organise the code in horizontal layers (presentation, services, data) so that each concern has one home and dependencies point in one direction only: downwards.

## When it fits

- Small to medium applications with a single team and one deployment.
- Business rules that are simple enough to live in a service layer without a rich domain model.
- Projects where speed of the first release matters more than long-term modularity.

## Structure

- `presentation/` (or `api/`, `web/`): controllers, request and response models, input validation. No business rules.
- `services/`: use cases and business rules, one module per capability. Transactions are decided here.
- `data/` (or `repositories/`, `persistence/`): storage access, queries, mappings. No business rules.
- Shared value types and errors live in a small `common/` module that every layer may read.

## Best practices

- Dependencies point down only; a lower layer never imports an upper one.
- Pass plain data objects between layers, never framework request objects or ORM entities upwards.
- Keep services free of HTTP and SQL details so they can be tested without a server or database.
- Split a layer by feature (`services/orders.py`, `services/billing.py`) before it becomes a catch-all.
- Do not skip layers: presentation talks to services, not to data.

## Dependency rules

- presentation may use services.
- services may use data.
- data uses no other layer.

## Expert prompt

You are the expert in layered architecture for this project. Organise the code in presentation, services and data layers under the project's source root, one module per feature in each layer. Controllers only translate requests and responses and call services; services hold the business rules and call data access; data access holds queries and mappings only. A layer imports only the layer directly below it; never import upwards and never skip a layer. Do not return ORM entities or framework request objects across a layer boundary. When a task needs code that does not fit a layer, say which layer you chose and why. Before finishing, check that every new import follows the allowed direction.
