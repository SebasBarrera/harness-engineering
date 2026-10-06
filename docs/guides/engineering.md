# Standards, principles, testing strategy and architecture

Since 2.0 (#56) the harness carries the team's engineering standards into the run: language
standards packs, engineering principles, a testing strategy (TDD or BDD when the team works that
way) and the project's architecture. Every setting is optional; a `project.yaml` without it keeps
the 1.0.0 behaviour and its configuration digest. `harness init` writes them (see
[the file `harness init` writes](../reference/configuration.md#file-written-by-harness-init)) and
`harness config validate` shows the effective values under `engineering`.

The cost rule of the whole wave: **tools verify, models judge only what tools cannot**.

- The implement request carries only the standards cards that apply to the files the call works
  on, compact (id, rule, exceptions), capped by `standards.maxCards`, selected deterministically
  and cached by the digest of the packs and the paths.
- The cards no tool verifies and the principles checklist ride on the existing review call: no
  extra agent call.
- The architecture survey of an existing project is one read-only call per project, cached in
  `.harness/architecture.md` and reused by every run; new-project options are one call, only for
  a new project.
- TDD evidence is measured by running the tests, BDD scenarios by the project's BDD runner.

`scripts/measure_agent_tokens.py` measures it: see [token cost](#token-cost).

## Standards packs

Fifteen packs ship with the package (`src/governed_harness/resources/standards/PACK/`):
`python`, `javascript`, `typescript`, `node`, `react`, `angular`, `vue`, `java`, `kotlin`, `go`,
`rust`, `swift`, `csharp`, `php` and `ruby`. Each has two files:

- `cards.yaml`: short rule cards with an id (`python.no-mutable-defaults`), the rule, its
  rationale, explicit exceptions, the files it applies to (`appliesTo` globs) and what verifies
  it (`verifiedBy`: tool rule ids such as `ruff:B006` or `eslint:eqeqeq`, or `review`);
- `tools.yaml`: how the language is detected (marker files, extensions, manifest dependencies),
  its test and BDD runners, the BDD frameworks it recognizes and its tools (Checkstyle, PMD and
  SpotBugs; ktlint and detekt; ESLint, typescript-eslint, eslint-plugin-react and react-hooks,
  angular-eslint and eslint-plugin-vue, Prettier; golangci-lint; Clippy and rustfmt; SwiftLint;
  the .NET analyzers and `dotnet format`; PHPStan and PHP CS Fixer; RuboCop; Ruff, Mypy, Bandit
  and Vulture) with the configuration files that show a repository uses them, the command with
  machine-readable output, its parser and a recommended configuration.

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `packs` | `[auto]` | `[auto]` | `auto`: the packs of the detected languages (profile technologies, marker files, extensions, dependencies in `package.json`, `pyproject.toml`, `pom.xml` and other manifests); a pack brings the packs it extends (`typescript` and `react` bring `javascript`). Or pack ids. |
| `cards` | `off` | `auto` | The cards that apply to the files of a call go to the implement request (`standards` block); the cards verified by `review` and applying to the ChangeSet files go to the review call's checklist. |
| `maxCards` | `12` | `12` | Cap of the cards of one request. |
| `tools` | `off` | `detect` | Each pack tool whose configuration file is in the repository, and that no selected validator already runs (`coveredBy`), becomes an optional validator `standards.PACK.TOOL` with its parser. Detection only: the harness never installs a tool; a tool that is not installed is `NOT_APPLICABLE`. |
| `path` | `.harness/standards` | none | Repository overrides: `PATH/PACK/cards.yaml` replaces cards with the same id, adds new ones and may list `disabled` ids; `PATH/PACK/tools.yaml` replaces tools by id; a directory with an id no pack has is a repository pack. |
| `disabled` | none | none | Card ids to drop. |

`harness standards show` lists the detected and effective packs, their tools and the validators
added for the repository; `--lang PACK` shows every card of one pack, `--file PATH` the cards an
implement call and the review checklist would get for those files. It works without a project.

The repository's own tool configuration takes precedence over the pack's recommendation: the
pack only detects that the tool is configured and runs it. The parsers added for the packs'
tools are Checkstyle XML, RuboCop JSON, Cargo JSON (Clippy) and MSBuild diagnostics
(see [located findings](../reference/configuration.md#located-findings)).

## Engineering principles

`verification.principles` measures the traces some principles leave, deterministically, in
VERIFICATION (validator `harness.principles`):

| Rule | Proxy | Principle |
|---|---|---|
| `principles.dry.duplication` | A window of `duplicationWindow` normalized lines (default 6) the change adds that already exists in another file or elsewhere in the same file (tests are ignored). | DRY |
| `principles.kiss.nesting` | An added line nested more than five levels deep (indentation, any language). | KISS |
| `principles.kiss.module-size` | A non-Python source file the change grows past `verification.architecture.maxModuleLines` (default 800); a file already past it is LOW. Python uses the #40 limits. | KISS, Clean Code |
| `principles.composition.inheritance` | A class the change adds or edits whose inheritance chain in the workspace is deeper than `maxInheritanceDepth` (default 3): Python AST, `extends` and `class X : Y` declarations, Ruby `<`. | Composition over inheritance |
| `principles.yagni.unused-public` | A public function or class (Python) or export (JavaScript, TypeScript) the change adds that no other file, test included, names; always LOW. | YAGNI |
| `principles.boy-scout.reformat-only` | A file whose every change is whitespace or layout: an unrelated cleanup. Cleanup inside a file the change edits for the task is allowed. | Boy Scout (bounded) |

Dependency direction and layers are the architecture's layer rules below (separation of concerns,
dependency inversion); naming and signatures are verified by the linters of the packs. Under
`mode: enforce` the check is mandatory and its findings have `severity` (default `MEDIUM`: shown,
not blocking under the default `findingBlockSeverities`; `HIGH` makes them block and enter the
correction loop); under `warn` they are `LOW`. `unusedPublic`, `boyScout` and `checklist` can be
turned off one by one.

With `checklist` (default on) the review call (`review.agentReview`) also receives the principles
checklist (single responsibility, open-closed, Liskov substitution, interface segregation,
dependency inversion, DRY, KISS, YAGNI, least astonishment, composition, Boy Scout), each with a
rule id (`principles.srp` and so on) the reviewer uses for its findings.

## Testing strategy

| `testing.strategy` | What happens |
|---|---|
| `auto` (`init`) | Follow the repository: feature files or a BDD framework (behave, pytest-bdd, Cucumber, SpecFlow or Reqnroll, Karate, Behat, godog, Kotest...) give `bdd`, tests give `conventional`; nothing known is asked in INTENT under `intake.projectSetup`, and a person's answer is kept for the project. |
| `conventional` | The earlier behaviour. |
| `tdd` | The implement request asks for test-first work, and VERIFICATION records the evidence (validator `harness.tdd`, artifact `tdd-evidence`): **red**, the tests the change adds or changes run on the workspace with the changed sources reverted to the baseline and must fail there (`tdd.not-red`, HIGH, when they pass; `tdd.no-tests`, HIGH, when code changes without a test); **green**, every mandatory validator passes; **refactor**, the principles, architecture and layer checks pass with everything green. The red run uses `testing.testCommand` with the test files, else the pack's runner (`python -m pytest -q FILES`, `bundle exec rspec FILES`, the whole suite where the runner takes no files). |
| `bdd` | The acceptance call of SPECIFICATION writes the criteria as Gherkin feature files under `featuresDirectory` (default `features`) instead of pytest files; SPECIFICATION waits for a person (`harness acceptance decide`); the approved files are frozen and the BDD runner (`bddCommand`, else the pack's: `python -m behave`, `npx cucumber-js`, `mvn -q test`, `bundle exec cucumber`...) runs before the change and must fail (the steps are not defined); the implement request asks for the step definitions; every VERIFICATION checks the frozen files and runs the runner. |

## Architecture

| Key | Absent | `init` | Effect |
|---|---|---|---|
| `mode` | `off` | `agent` | `agent`: one read-only `architecture` call per project (a survey of an existing project in DISCOVERY, options for a new project in INTENT). |
| `style` | none | none | `ddd`, `hexagonal`, `clean`, `layered`, `modular-monolith`, `microservices`, `mvvm`, `mvi` or `custom`; a configured style or layers skip the call. |
| `layers` | none | none | Each layer: `name`, `paths` (globs) and/or `modules` (dotted prefixes). |
| `allow` | none | none | The layers each layer may depend on; a layer may always use itself, and a layer without an entry may use no other layer. |
| `enforce` | `enforce` | `enforce` | Layer violations are HIGH (`enforce`) or LOW (`warn`). |
| `refresh` | `manual` | `manual` | `auto` also surveys again when the source directory layout changed materially (fewer than 80 % of the directories in common). |
| `agent` | the run's provider | none | Provider, model and effort of the architecture call. |

**Existing project.** DISCOVERY surveys it once: the call reads the layout and a few files and
answers the style, a short Markdown description and the layer rules it can infer. The answer is
stored in `.harness/architecture.md` and `.harness/architecture.json` and reused by every later
run (event `architecture.survey.reused`, no call). When rules were inferred, DISCOVERY waits for a
person, bound to their digest:

```bash
harness architecture show
harness architecture decide --run RUN --decision APPROVE --digest DIGEST --rationale "..."
```

`harness architecture refresh` marks the survey stale; the next run surveys again.

**New project.** INTENT asks the call for two to four options with their trade-offs (benefits,
costs, fit) and one recommendation. A person chooses, bound to the digest of the options; the
choice is recorded as an ADR (an artifact with its digest and `.harness/adr/ADR-0001-architecture.md`)
and its layers become the rules:

```bash
harness architecture decide --run RUN --option hex --digest DIGEST --rationale "..."
```

**Enforcement.** The rules in force (configured layers, else the approved survey or ADR) are the forbidden-dependency
check of issue #40 generalized to every language (validator `harness.layers`, rule
`architecture.layer-violation`): the imports of each changed file are read per language (Python
AST; JavaScript and TypeScript `import`/`require` with relative paths resolved; Java, Kotlin and
Scala `import`; C# `using`; PHP `use`; Go import paths; Rust `use crate::`; Swift `import`; Ruby
`require`) and resolved to a layer by path glob, module prefix or declared package. The implement
request carries the layers and what each may depend on, and `harness check` runs the same check.

## New and existing projects

A project is **new** when its Git repository has no commit, or when it has no source file beyond
scaffolding (tests, configuration, documentation, source files that define nothing, and an entry
point of at most 15 lines); otherwise it is **existing**. `harness project show` reports the
decision and why, with the packs, the testing strategy, the architecture and the forge, without
an agent call.

Under `intake.projectSetup: ask` INTENT asks (rule `P1`, targets `project:architecture`,
`project:testing`, `project:standards`), once per project:

- a new project: the testing strategy and the standards packs, unless the configuration sets them;
  the architecture too when `architecture.mode` is not `agent` (with `agent`, the call proposes
  options instead);
- an existing project: only what detection could not establish (no tests, no detected language).

The person answers with `harness task clarify` like any clarification; the answers become the
project's setup record (`harness project show`, `projectSetup`), which later runs read instead of
asking again, and each answer lands in the task as a constraint (`Project setup (testing): tdd`).

## Token cost

`scripts/measure_agent_tokens.py` runs the quickstart's Python project twice with the same two
tasks through a fixture command provider that calls no model and reports, as usage, an estimate
of the tokens it received (the request's JSON length divided by four; a rule of thumb, not a
tokenizer). The harness records that usage (`harness budget show`). Measured with the
harness of this branch (macOS arm64, Python 3.12.11), the totals per run and the calls by kind
(estimated input tokens):

| Configuration | Run | Agent calls | Recorded tokens | Implement | Review | Architecture |
|---|---|---:|---:|---:|---:|---:|
| before (agent-results defaults, no wave 6 keys) | first task | 4 | 4670 | 1851 | 1076 | none |
| before | second task | 4 | 4735 | 1909 | 1079 | none |
| after (`harness init` with wave 6) | first task | 5 | 6359 | 2350 | 1421 | 814 |
| after | second task | 4 | 5575 | 2407 | 1423 | none |

The first run of a project pays one architecture survey; every later run reuses it. The
implement request grows with the nine cards that apply to `src/sample/pricing.py` and its test
(about 500 estimated tokens) and the review request with the checklist (about 345); the
principles, layer, TDD and BDD checks are tools and add no agent call. Re-run the script after
changing a pack or the checklist.
