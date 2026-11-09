---
id: event-driven
title: Event-driven architecture
purpose: Let components react to events that other components publish, so producers and consumers are decoupled in time and knowledge.
fits:
  - workflows where several parts of the system must react to the same fact
  - integrations and pipelines with bursts of load or asynchronous work
  - systems where an audit trail of what happened is valuable
benefits:
  - producers do not know their consumers, so adding a reaction changes no existing code
  - natural fit for asynchronous work, retries and audit trails
costs:
  - the flow of a business operation is harder to follow and debug
  - eventual consistency, duplicates and ordering must be handled on purpose
layers: []
allow: {}
---
## Purpose

State changes are announced as immutable events on a broker or in-process bus. Handlers subscribe to the events they care about and react independently.

## When it fits

- One action triggers many independent reactions (notify, index, bill, audit).
- Work is naturally asynchronous or spiky.
- Integration between components that should not call each other directly.

## Structure

- `events/`: the event types and their versioned schemas; the shared contract.
- `producers/` (or the domain code that publishes): emit events after a state change commits.
- `handlers/` (or `consumers/`): one handler per event and reaction, small and idempotent.
- `bus/` (or `broker/`): the publishing and subscription adapter; the only code that knows the broker.

## Best practices

- Name events in the past tense after business facts (`OrderPlaced`), not commands.
- Make handlers idempotent; assume at-least-once delivery and duplicates.
- Version event schemas and keep them backward compatible.
- Publish after the state change is committed (outbox pattern when a broker is used).
- Keep events small, carry identifiers and the data consumers need, and attach a correlation identifier.
- Handle failures with retries, a dead-letter queue and alerting.

## Dependency rules

Handlers and producers depend on the event contract in `events/` and on the bus adapter; they never import each other. A producer does not know which handlers exist.

## Expert prompt

You are the expert in event-driven architecture for this project. Define events as immutable, past-tense facts with versioned schemas in one events package. Producers publish an event after the state change is committed and never call consumers. Each handler reacts to one event, is small and idempotent, and assumes at-least-once delivery. Producers and handlers depend on the event contract and on a bus adapter, never on each other. Every event carries identifiers and a correlation identifier; failures go to retries and a dead-letter path. Keep the schema backward compatible. When a task adds a reaction, add a handler and its test without changing the producer. Before finishing, check that no handler imports a producer and no producer imports a handler.
