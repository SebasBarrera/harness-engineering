---
id: mvi
title: Model-View-Intent
purpose: Make the user interface a function of one immutable state, changed only by intents that go through a single, unidirectional cycle.
fits:
  - interfaces with complex, frequently changing state where bugs come from inconsistent state
  - reactive front ends or mobile apps that already use unidirectional data flow
  - teams that want reproducible screen states for debugging and tests
benefits:
  - one source of truth for the screen and a predictable, replayable flow
  - state transitions are easy to test as pure functions
costs:
  - more ceremony (intents, reducers, states) than MVVM for simple screens
  - large states can be costly to copy and reason about
layers:
  - name: model
    paths: ["**/model/**", "**/state/**"]
  - name: intent
    paths: ["**/intent/**", "**/intents/**", "**/reducer/**"]
  - name: view
    paths: ["**/view/**", "**/ui/**"]
allow:
  intent: [model]
  view: [intent, model]
---
## Purpose

The cycle is: the view emits intents; a reducer or handler turns an intent and the current state into a new state; the view renders the new state. State is immutable and there is one per screen.

## When it fits

- Screens with many interacting events and asynchronous results.
- Reactive stacks (Kotlin flows, RxJS, Redux-like stores, SwiftUI with a store).
- Debugging needs that benefit from replaying the sequence of intents.

## Structure

- `model/` (or `state/`): the immutable state type of each screen and the data it holds.
- `intent/`: the intents (user actions and system events) and the reducer or handler that produces the next state, including side-effect handlers.
- `view/` (or `ui/`): renders the state and emits intents; no logic.

## Best practices

- One immutable state object per screen; never mutate it in place.
- Reducers are pure functions; run side effects in separate handlers that emit result intents.
- Model every outcome (loading, content, error) as an explicit state, not as flags.
- The view never calls a repository; it only emits intents.
- Test reducers with a table of (state, intent) to expected state.

## Dependency rules

- model depends on nothing.
- intent may use model.
- view may use intent and model (to read state types).

## Expert prompt

You are the expert in Model-View-Intent for this project. Organise each screen around one immutable state type, a closed set of intents and a pure reducer that maps the current state and an intent to the next state. Side effects (network, storage) live in separate handlers that emit result intents. The view renders the state and emits intents; it holds no logic and never calls repositories. Model every outcome as an explicit state value (loading, content, error), not as loose flags. Keep the flow unidirectional: view, intent, reducer, state, view. Test every reducer with a table of state and intent pairs and the expected state. Before finishing, check that the view calls no data source and that no code mutates a state in place.
