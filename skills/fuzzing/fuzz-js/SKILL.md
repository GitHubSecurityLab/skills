---
name: fuzz-js
description: >-
  JS/TS fuzzing reference for Jazzer.js: CLI targets and Jest tests,
  setup, runs, coverage, detectors, hooks, triage and regression tests.
  Not a starting point: use fuzz-harness or fuzz-campaign.
license: MIT
---

# Fuzzing JavaScript and TypeScript with Jazzer.js

Jazzer.js runs libFuzzer in the Node process and
instruments the code under test as it loads. Two formats exist:

- **CLI fuzz targets** (`module.exports.fuzz = function (data) {...}`, or
  `export function fuzz` in ESM), run with `npx jazzer`. The default: they
  work with any test runner, and a plain test in the project's own runner
  replays their corpus (`regression.md`).
- **Jest fuzz tests** (`it.fuzz` in `*.fuzz.js` or `*.fuzz.ts` files), run
  through the `@jazzer.js/jest-runner` test runner, if it passes a probe
  with the project's Jest. Everything about them is in [references/jest.md](references/jest.md),
  read only on that path; the other references describe CLI targets.

**Status:** the skills pin Jazzer 0.30.0 and Jazzer.js 4.0.0.

Docs: https://github.com/CodeIntelligenceTesting/jazzer.js/tree/main/docs
(`jest-integration.md`, `fuzz-settings.md`, `fuzz-targets.md`,
`bug-detectors.md`, `custom-hooks.md`). When unsure, check
`npx jazzer --help` first.

## When to use

- A workflow skill needs a JS/TS command.
- Questions such as "how do I fuzz this Express handler with Jazzer.js?"

Not for deciding what to do next; that's the workflow skills.

## Reads

- `targets/<id>.md`: Harness (the target file, or a Jest test's file,
  describe and test name; package directory).
- `CAMPAIGN.md`: Settings (`memory_mb`, `input_timeout_s`).

## Steps

1. **Choose the harness format,** first match wins; the format decides
   the commands of every later step:

   | The project has | Format |
   |---|---|
   | Jazzer.js CLI targets | keep them |
   | Jazzer.js Jest fuzz tests, or the user asks for them | Jest fuzz tests if the runner passes a probe with the project's Jest (`jest.md`), else CLI targets calling the same code; tell the user why |
   | Anything else | CLI targets, plus a replay test in the project's runner |

   - If the project is TypeScript: a CLI target fuzzes the compiled
     JavaScript (`build.md`).
2. **Check the toolchain** and propose installs for what's missing
   ([references/install.md](references/install.md)): Node, the package
   manager; then `@jazzer.js/core` ([references/build.md](references/build.md));
   for Jest fuzz tests, `@jazzer.js/jest-runner`, whose probe checks the
   project's own Jest (`jest.md`, "Setup"). Record versions.
3. **Look up the command** for the step:

   | Step | Reference |
   |---|---|
   | Write or extend a fuzz test, hooks, detectors | [references/harness.md](references/harness.md) |
   | Install and configure | [references/build.md](references/build.md) |
   | Develop and long runs, environment, corpus | [references/run.md](references/run.md) |
   | Coverage to lcov | [references/coverage.md](references/coverage.md) |
   | Reproduce, minimize, read findings | [references/triage.md](references/triage.md) |
   | Regression tests | [references/regression.md](references/regression.md) |
   | Any step for Jest fuzz tests | [references/jest.md](references/jest.md) |

## Tested versions (checked 2026-10-29)

| Tool | Version | How it was checked |
|---|---|---|
| Jazzer.js (`@jazzer.js/core`, `@jazzer.js/jest-runner`) | 4.0.0, pinned exactly | CI on Linux; ran on macOS arm64 |
| Node.js | 24 LTS | CI on Linux; ran on macOS arm64. Node 26 also ran, but deprecates `module.register()`, which Jazzer.js uses to instrument ES modules |
| Jest | 29.7 and 30.3 | ran; 30.4 and later fail with `@jazzer.js/jest-runner` 4.0.0 (`Cannot read properties of undefined (reading '_buildTransformResult')`). The probe (`jest.md`) checks the project's |
| TypeScript through `ts-jest` | 29 | ran |

Pin these versions exactly (`--save-exact`), all `@jazzer.js/*` packages
alike, and record the installed versions. If the project already pins
Jazzer.js: keep its version and say so. A newer release only when the
user asks. Jazzer.js is Apache-2.0.

## Engine output locations in the project tree

CLI targets write only where their arguments say. Jest fuzz tests:
`jest.md`, "Engine output locations in the project tree".

## Done when

- The step's command ran with the flags from the reference, and its check
  passed (e.g. the gate's lcov has `SF:` lines for the target's files).

## Pitfalls and shortcuts to reject

- **Detectors run real code:** they throw only when the call carries
  their trigger value (`jaz_zer`); every other input reaches the real
  `child_process`, `fs` or network call. A fuzzed `execSync` runs random
  shell commands (`fuzz-harness`'s input-and-side-effects.md).
- **The run stops at the first finding.**
- `--sync` for synchronous targets, the common case; never for async code
  (`run.md`).
- Committing a crash file into a directory the project's tests replay:
  the project's CI replays it (`regression.md`).

## Writes

- `targets/<id>.md`: tool versions, target or test file, corpus paths
  (through the calling skill).

## Next

- Back to the workflow skill that asked.
