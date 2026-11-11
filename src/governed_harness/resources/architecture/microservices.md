---
id: microservices
title: Microservices
purpose: Split the system in small services that are deployed, scaled and owned independently and talk over the network.
fits:
  - several teams that must release independently
  - parts of the system with very different scaling or availability needs
  - an organisation with the operational maturity for distributed systems
benefits:
  - independent deployment and scaling of each service
  - technology and ownership can differ per service
costs:
  - network failures, versioned contracts and distributed data to handle
  - heavy operational and testing overhead for a small team
layers: []
allow: {}
---
## Purpose

Each service owns one business capability and its data, exposes a versioned contract and can be built, deployed and scaled alone.

## When it fits

- Multiple teams that need autonomy.
- Components with different load or availability requirements.
- Organisations with automated delivery, monitoring and tracing in place.

## Structure

- One directory (or repository) per service, with its own build, tests and configuration.
- Inside a service, a simple internal layering (api, domain, storage) is enough.
- `contracts/` holds the versioned API or event schemas shared between services.
- No shared database: a service reads another's data only through its API or events.

## Best practices

- Keep services small in responsibility, not in lines; split by business capability.
- Version contracts and keep them backward compatible; test them with consumer-driven contract tests.
- Make every call resilient: timeouts, retries with backoff, circuit breakers, idempotency.
- Prefer asynchronous events for reactions and synchronous calls only for queries that need an answer.
- Trace every request with a correlation identifier and expose health checks.

## Dependency rules

Services never import each other's code: the only dependency is through a contract in `contracts/` (an API or an event schema). A service's internals are private to it.

## Expert prompt

You are the expert in microservices architecture for this project. Organise the code with one directory per service, each owning one business capability, its data and its tests. A service never imports another service's code and never reads another service's database; services communicate only through versioned contracts kept in a contracts directory (HTTP APIs or event schemas). Every remote call has a timeout, a bounded retry with backoff and an idempotency key where it changes data. Prefer asynchronous events for reactions. Each service exposes a health check and propagates a correlation identifier. When a task touches two services, change the contract first, keep it backward compatible and then change both sides. Before finishing, check that no service imports another service's internals.
