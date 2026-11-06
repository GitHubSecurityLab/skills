---
name: fuzz-java
description: >-
  Java fuzzing reference for Jazzer: @FuzzTest harnesses, Maven, Gradle,
  runs, JaCoCo coverage, detectors, hooks, triage and regression tests.
  Not a starting point: use fuzz-harness or fuzz-campaign.
license: MIT
---

# Fuzzing Java with Jazzer

Fuzz tests are JUnit 5 `@FuzzTest` methods in the project's test
sources. The campaign runs them in two ways:

- **Standalone driver** (`com.code_intelligence.jazzer.Jazzer` with
  `--target_class` and `--target_method`) for every fuzzing run, replay
  and reproduction: it takes all libFuzzer flags and explicit corpus and
  crash paths, and needs no build tool at run time.
- **JUnit** (`mvn test`, `gradle test`) for regression replays, regression
  tests and the project's CI.

**Status:** the skills pin Jazzer 0.30.0 and Jazzer.js 4.0.0.

When unsure, check the docs: https://github.com/CodeIntelligenceTesting/jazzer
(README, `docs/advanced.md`, `docs/arguments-and-configuration-options.md`,
`docs/mutation-framework.md`).

## When to use

- A workflow skill needs a Java command.
- Questions such as "how do I run Jazzer on 8 cores?"

Not for deciding what to do next; that's the workflow skills.

## Reads

- `targets/<id>.md`: Harness (test class and method, build tool, module).
- `CAMPAIGN.md`: Settings (`memory_mb`, `input_timeout_s`).

## Steps

1. **Choose the harness format,** first match wins:

   | The project has | Format |
   |---|---|
   | `@FuzzTest` methods | keep them |
   | Classic `fuzzerTestOneInput` classes (often for OSS-Fuzz) | keep them; run them with the standalone driver |
   | No fuzz tests, and the user names a format | that one |
   | JUnit 5, and the target parses a file format (binary or text) | `@FuzzTest` with one `byte[]` or `@NotNull @WithUtf8Length(max = …) String` parameter; a parser with options also gets a typed one for them (`harness.md`) |
   | JUnit 5 | `@FuzzTest` with typed parameters |
   | JUnit 4 or TestNG | add JUnit 5 and `jazzer-junit` in test scope (`build.md`) |

2. **Check the toolchain** and propose installs for what's missing
   ([references/install.md](references/install.md)): JDK, build tool; then
   dependencies and the standalone classpath ([references/build.md](references/build.md)).
   Record versions.
3. **Look up the command** for the step:

   | Step | Reference |
   |---|---|
   | Write or extend a fuzz test, hooks, detectors | [references/harness.md](references/harness.md) |
   | Dependencies, classpath, instrumentation checks | [references/build.md](references/build.md) |
   | Develop and long runs, environment, corpus merge | [references/run.md](references/run.md) |
   | Coverage to lcov | [references/coverage.md](references/coverage.md) |
   | Reproduce, minimize, decode typed inputs, read findings | [references/triage.md](references/triage.md) |
   | Regression tests | [references/regression.md](references/regression.md) |

## Tested versions (checked 2026-10-29)

| Tool | Version | How it was checked |
|---|---|---|
| Jazzer (`jazzer-junit`) | 0.30.0, pinned exactly | CI on Linux; ran on macOS arm64 |
| JDK | 21 and 25 | CI on Linux with both; JDK 27 broke instrumentation of JDK classes |
| JUnit | 5.11 through `junit-bom` | CI |
| Maven / Surefire | 3.10 / 3.5.2 | CI |
| Gradle | 9.8.2 | CI (JDK 21) |
| JaCoCo | 0.8.14 (the first with Java 25 support) | CI |

Pin these versions exactly, unless the project already pins Jazzer or
JUnit: then keep its versions and say so. A newer release only when the
user asks.

## Engine output locations in the project tree

For the gate's working-tree comparison:
- `.cifuzz-corpus/<fully.qualified.Class>/<method>/` in the module
  directory, from JUnit fuzzing runs;
- `src/test/resources/<package path>/<Class>Inputs/<method>/`: crash files
  from JUnit fuzzing runs, and from standalone runs without
  `-artifact_prefix` (every launcher command passes it);
- `target/` or `build/` (build output the project already ignores).

## Done when

- The step's command ran with the flags from the reference, and its check
  passed (e.g. the instrumentation check in `build.md`).

## Pitfalls and shortcuts to reject

- **A JDK newer than Jazzer supports:** the log shows `Failed to instrument
  java/...` and the detectors on JDK classes silently stop working. Run on
  a supported JDK (21 or 25).
- **Gradle skips the test task as up to date:** a repeated `gradle test`
  fuzzes nothing and exits 0. Add `--rerun`.
- **Runs end after 5 minutes:** `@FuzzTest`'s default `maxDuration` also
  applies to standalone runs. Always pass `-max_total_time` (`0` for no
  limit).
- **`-fork` or `-jobs` for parallel runs:** every job is a new JVM, and
  they share one scratch directory. Run N copies through the launcher.
- **Only one fuzz test fuzzes per JVM;** select exactly one.
- **Detectors run real code** except for their trigger values (e.g. the
  command `jazze`): other commands run and files are written for real
  (`fuzz-harness`'s input-and-side-effects.md).
- Committing a crash file into the inputs directory: the project's CI
  replays it (`regression.md`).

## Writes

- `targets/<id>.md`: tool versions, classpath file, commands (through the
  calling skill).

## Next

- Back to the workflow skill that asked.
