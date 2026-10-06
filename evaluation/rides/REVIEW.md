# Blind code review of the rides implementations

Instructions given to the reviewer of every group of cells (the same instruction level with and without
the harness). The implementations are copied without their run metadata and named A, B, C in a random
order; the reviewer is not told which condition produced which. `prepare_review.py` builds the review
folder and keeps the key apart.

## What to read

- `SPEC.md` (the product scope; the one-line and paragraph levels did not receive it, so judge them
  against it as the product that was asked for, and say what is missing).
- `INTERFACE.md` (public API every implementation had to follow).
- For each implementation: `src/` and `tests/`. Do not run the agents; you may run their tests and
  small scripts against them.

## Rubric (score 1 to 5 each, with evidence as file:line)

1. Architecture: layers and boundaries (API facade, domain, services, persistence), dependency
   direction, separation of concerns.
2. Modularity and cohesion: module sizes, single responsibility, god classes or god files.
3. Domain model: explicit entities, state machines for rides and orders, invariants enforced in one place.
4. Persistence: schema design, transactions, what is stored, how state is restored.
5. Money and time: Decimal with half-up rounding where required, no float money, virtual clock use,
   timers.
6. Errors and validation: exception types per the spec, validation centralised or scattered, error
   precedence.
7. Security: password hashing (algorithm, salt, iterations), card data (no number or CVC stored),
   lockout.
8. Performance design: indexes or spatial structures for dispatch, complexity of hot paths.
9. Tests: what they cover, boundary cases, independence, readability; tests that assert nothing.
10. Maintainability: naming, duplication, comments, type hints, consistency.
11. Functional coverage: which spec sections are implemented, partial or missing.

## Output

A JSON file with, per implementation, the eleven scores and a one-sentence justification each, the
list of spec sections missing or partial, and a list of the most important differences between the
implementations (each with evidence). Then a short Markdown summary: which implementation is
stronger in what, and why, without guessing the condition.
