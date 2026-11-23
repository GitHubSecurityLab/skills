# Java regression tests

## Seed replay: the harness as a test

In regression mode, the default of `mvn test` and `gradle test`, every
`@FuzzTest` runs on:
- the empty input;
- its JUnit seed sources (`@MethodSource`, `@ValueSource`, ...);
- every file in its inputs directories:
  `src/test/resources/<package path>/<Class>Inputs/<method>/`, or
  `<Class>Inputs/` for all methods of the class.

A fixed bug gets its own test instead of a file in these directories.

## The bug's reproducer (`fuzz-triage`)

Every real bug gets one, written in triage on the build that found it: a
JUnit 5 test class `<repro_class>`, with no package, in
`crashes/<bucket>/<repro_class>.java`. It calls the project's API with the
concrete values the harness derived from the input, not through the harness
and not as a crash file. It also has a `main` that runs the test method, so
the reproducer runs without the build tool. Take the values from the harness;
for typed parameters, decode the crash file (`triage.md`).

- **It includes the check that caught the bug,** so it fails before the
  fix: the exception itself, the harness's property check written out, or
  an explicit assertion of a bug detector's effect (e.g. that no file
  outside the base directory was opened). A plain call often passes.
- **It runs against a tree's classpath file** (`build.md`), without
  rebuilding that tree. The reproducer is compiled into scratch:

<!-- ci: java-reproducer -->
```sh
$L start --id poc-$H-<n> --harness $H --timeout <timeout_s> \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" --env CP="$CP" --env JOPTS="$JOPTS" -- \
  sh -c 'javac -cp "$CP" -d "$0/classes" "$1" && exec java $JOPTS -ea -cp "$0/classes:$CP" "$2"' \
  "{scratch}" "$F/crashes/<bucket>/<repro_class>.java" "<repro_class>"
```

The state `exited 1` with the bug's exception means the bug reproduced;
`finished` means the reproducer passes.

The fix (`fuzz-triage`) moves the reproducer into the project's test
sources, with a package, the project's assertion style and no `main`.
The test, named after the bug, goes in the fix commit.

## Upstreaming (`fuzz-campaign`)

- Fuzz tests run in the project's normal test job in regression mode;
  nothing else is needed for CI to replay their seeds.
- Check that the test dependency and any `junit-platform.properties`
  entries are part of the upstreaming branch.
- Check that hooks used only for the campaign (stubs for local fixes,
  fuzzing-only replacements) are either needed by the fuzz test or left
  out.
- The `.cifuzz-corpus/` directory isn't committed. Seed files worth
  keeping go into the inputs directories: only the project's own data or
  generated inputs, and never inputs that reach a held-back bug.
