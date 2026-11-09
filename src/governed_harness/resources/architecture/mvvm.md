---
id: mvvm
title: Model-View-ViewModel
purpose: Separate the screen (view) from its presentation logic (view model) and from the data and business rules (model), with the view bound to observable view model state.
fits:
  - applications with a user interface that reacts to state (mobile, desktop, rich web front ends)
  - screens whose logic should be unit-tested without the UI
  - teams where designers and developers work on the view and its logic separately
benefits:
  - presentation logic is testable without a UI
  - the view stays thin and declarative
costs:
  - binding and state plumbing add code for small screens
  - view models can grow into large catch-all classes
layers:
  - name: model
    paths: ["**/model/**", "**/models/**", "**/data/**"]
  - name: viewmodel
    paths: ["**/viewmodel/**", "**/viewmodels/**"]
  - name: view
    paths: ["**/view/**", "**/views/**", "**/ui/**"]
allow:
  viewmodel: [model]
  view: [viewmodel]
---
## Purpose

The view renders state and forwards user actions; the view model exposes observable state and commands for one screen; the model holds data and business rules.

## When it fits

- Screens with non-trivial presentation state (loading, errors, validation).
- Platforms that offer data binding or reactive state (Android, WPF, SwiftUI, Vue, Angular).
- Projects that want fast tests of screen behaviour.

## Structure

- `model/` (or `data/`): entities, repositories, services; no knowledge of any screen.
- `viewmodel/`: one view model per screen or component, exposing state and commands.
- `view/` (or `ui/`): layouts and components that bind to view model state.

## Best practices

- The view model knows nothing of the view: no references to widgets, contexts or navigation classes.
- Expose immutable state and explicit commands; avoid two-way binding of business data.
- Keep the view free of logic beyond rendering and event forwarding.
- Inject repositories into the view model; do not create them there.
- Test view models with fake models and assert on emitted state.

## Dependency rules

- model depends on nothing.
- viewmodel may use model.
- view may use viewmodel and must not use model directly.

## Expert prompt

You are the expert in the Model-View-ViewModel pattern for this project. Organise the code in model, viewmodel and view. The model holds data and business rules and knows nothing of screens. Each screen or component has one view model that exposes immutable state and explicit commands, receives its repositories by injection and never references views, widgets, contexts or navigation classes. The view only renders the view model state and forwards user events; it contains no business logic and never imports the model directly. Keep view models small, one per screen, and test them with fake models by asserting on the state they emit. Before finishing, check that the view imports only view models and that view models import no view.
