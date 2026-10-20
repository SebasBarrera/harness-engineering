---
id: hexagonal
title: Hexagonal architecture (ports and adapters)
purpose: Keep the business core independent of delivery mechanisms and infrastructure by talking to the outside only through ports implemented by adapters.
fits:
  - a core with real business rules that must be tested without a database or a server
  - several entry points (HTTP, CLI, queue) or several infrastructure providers over the same core
  - infrastructure that is likely to change (database, payment provider, message broker)
benefits:
  - the core is testable in memory with fake adapters
  - infrastructure can be replaced without touching business rules
costs:
  - more interfaces and mapping code than a layered design
  - overkill for a thin CRUD application
layers:
  - name: domain
    paths: ["**/domain/**", "**/core/**"]
  - name: application
    paths: ["**/application/**", "**/ports/**"]
  - name: adapters
    paths: ["**/adapters/**", "**/infrastructure/**"]
allow:
  application: [domain]
  adapters: [application, domain]
---
## Purpose

Put the business logic in a core that knows nothing about the outside world. The core declares ports (interfaces) for what it needs and what it offers; adapters implement or drive them.

## When it fits

- Business rules that deserve fast, isolated tests.
- Multiple ways to reach the same use cases (HTTP, CLI, events).
- Infrastructure choices that may change or that you want to fake in tests.

## Structure

- `domain/`: entities, value objects and domain services. Pure code, no I/O.
- `application/`: use cases and the ports they need (`ports/` for outbound interfaces, input ports for the use cases themselves).
- `adapters/`: inbound adapters (HTTP controllers, CLI commands, consumers) and outbound adapters (repositories, API clients, publishers), one module per technology.
- A composition root (`main` or `bootstrap`) wires adapters to ports; it is the only place that knows both sides.

## Best practices

- Ports belong to the core and speak the language of the domain, not of the technology.
- Adapters depend on the core, never the other way around.
- Map between external data shapes and domain objects inside the adapter.
- Test use cases with in-memory adapters; test each adapter against the real technology separately.
- Keep framework annotations and ORM mappings out of `domain/`.

## Dependency rules

- domain depends on nothing.
- application may use domain.
- adapters may use application and domain.

## Expert prompt

You are the expert in hexagonal architecture (ports and adapters) for this project. Organise the code in domain, application and adapters. The domain holds entities and value objects with no I/O. The application layer holds the use cases and declares the ports (interfaces) they need, named in the language of the domain. Adapters implement the outbound ports and drive the inbound ones, one module per technology, and map external data to domain objects. Only a composition root may import both sides. Never import adapters or frameworks from the domain or the application layer. For each new capability, add the use case and its port first, then the adapter, and test the use case with an in-memory adapter. Before finishing, check that no import points from the core outwards.
