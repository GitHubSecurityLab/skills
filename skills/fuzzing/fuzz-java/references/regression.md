# Java regression tests

## Seed replay: the harness as a test

In regression mode (the default `mvn test` / `gradle test`), every
`@FuzzTest` runs on the empty input, on its JUnit seed sources
(`@MethodSource`, `@ValueSource`, ...), and on every file in its inputs
directories (`src/test/resources/<package path>/<Class>Inputs/<method>/`,
or `<Class>Inputs/` for all methods of the class). A fixed bug gets its
own test instead.

## The bug's reproducer (`fuzz-triage`)

Every real bug gets one, written in triage on the build that found it: a
JUnit 5 test class in `crashes/<bucket>/<Name>.java` (no package) that
calls the project's API with the concrete values the harness derived from
the input, not through the harness and not as a crash file, plus a `main`
that runs the test method, so it runs without the build tool. Take the
values from the harness (typed parameters: decode the crash file,
`triage.md`).

- **It includes the check that caught the bug,** so it fails before the
  fix: the exception itself, the harness's property check written out, or
  an explicit assertion of a bug detector's effect (e.g. that no file
  outside the base directory was opened). A plain call often passes.
- **It runs against a tree's classpath file** (`build.md`), compiled into
  scratch, without rebuilding that tree:

<!-- ci: java-reproducer -->
```sh
$L start --id poc-$H-<n> --harness $H --timeout <timeout_s> \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" --env CP="$CP" --env JOPTS="$JOPTS" -- \
  sh -c 'javac -cp "$CP" -d "$0/classes" "$1" && exec java $JOPTS -ea -cp "$0/classes:$CP" "$2"' \
  "{scratch}" "$F/crashes/<bucket>/<repro_class>.java" "<repro_class>"
```

The state `exited 1` with the bug's exception means it reproduced;
`finished` means it passes. The fix (`fuzz-triage`) moves it into the project's test
sources (package, the project's assertion style, no `main`), in the fix
commit, named after the bug.

## Hand-off (`fuzz-campaign`)

- Fuzz tests run in the project's normal test job in regression mode;
  nothing else is needed for CI to replay their seeds.
- Check that the test dependency and any `junit-platform.properties`
  entries are part of the hand-off branch, and that hooks used only for
  the campaign (stubs for local fixes, fuzzing-only replacements) are
  either needed by the fuzz test or left out.
- The `.cifuzz-corpus/` directory isn't committed; seed files worth keeping
  go into the inputs directories (only the project's own data or generated
  inputs, and never inputs reaching a held-back bug).

## Keep it private until disclosure

Keep a held-back bug's test (`fuzz-campaign`, safety rules) in the
`fuzz(fix)` commit, which is never pushed, until the bug is public and
fixed upstream: the test reveals the bug as soon as it's pushed. Never put
a crash file into an inputs directory: the project's CI replays it,
failing until the fix lands and revealing a held-back bug.
