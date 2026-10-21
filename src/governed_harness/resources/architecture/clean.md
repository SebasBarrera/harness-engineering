---
id: clean
title: Clean architecture
purpose: Arrange the code in concentric rings where source dependencies point only inwards, towards the business rules.
fits:
  - long-lived systems whose business rules must outlive frameworks and databases
  - teams that want strict, checkable dependency direction
  - applications with several delivery mechanisms over the same use cases
benefits:
  - business rules do not depend on frameworks, UI or storage
  - the dependency direction is simple to check automatically
costs:
  - more layers and data mapping than a small application needs
  - a learning curve for teams new to the rings
layers:
  - name: entities
    paths: ["**/entities/**"]
  - name: usecases
    paths: ["**/usecases/**", "**/use_cases/**"]
  - name: adapters
    paths: ["**/adapters/**", "**/interface_adapters/**"]
  - name: frameworks
    paths: ["**/frameworks/**", "**/infrastructure/**", "**/drivers/**"]
allow:
  usecases: [entities]
  adapters: [usecases, entities]
  frameworks: [adapters, usecases, entities]
---
## Purpose

Protect the business rules from the details around them. The rings, from the inside out, are entities, use cases, interface adapters and frameworks and drivers. A ring may know the rings inside it and none outside.

## When it fits

- Long-lived products where frameworks and databases will be replaced.
- Systems with rules complex enough to need an explicit use-case layer.
- Teams that want the dependency rule enforced by a tool, not by habit.

## Structure

- `entities/`: enterprise-wide business objects and rules.
- `usecases/`: application-specific rules, one class or function per use case, with input and output boundaries.
- `adapters/`: controllers, presenters and gateways that convert data between use cases and the outside.
- `frameworks/` (or `infrastructure/`): web framework, database driver, message client, configuration.

## Best practices

- Cross a boundary with simple data structures, never with entities that belong to another ring.
- Use dependency inversion: a use case declares the gateway interface, an outer ring implements it.
- Keep each use case small and named after what the user wants to do.
- Wire everything in one outermost entry point.
- Test entities and use cases without any framework.

## Dependency rules

- entities depend on nothing.
- usecases may use entities.
- adapters may use usecases and entities.
- frameworks may use every inner ring.

## Expert prompt

You are the expert in clean architecture for this project. Organise the code in four rings: entities, usecases, adapters and frameworks. Entities hold enterprise rules and import nothing. A use case is one small unit named after a user goal; it declares the gateway interfaces it needs and talks to entities only. Adapters convert between use case data and the outside (controllers, presenters, gateway implementations). Frameworks and drivers hold the web framework, storage clients and configuration. Dependencies point inwards only; cross a boundary with plain data structures, not with objects of another ring. Wire the rings in one outermost entry point. For each new feature, start with the entity or the use case and its test, then the adapter. Before finishing, check that no inner ring imports an outer one.
