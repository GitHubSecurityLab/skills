# Running Jazzer

Every run goes through `launcher.py` (`fuzz-harness`'s runs.md) with the
standalone driver. Every command starts with
`. .seclab/fuzz/env.sh; H=<harness>;`, since an agent's commands don't keep
shell variables: the file sets `F` (the absolute path of `.seclab/fuzz`),
`L` (`python3 $F/bin/launcher.py`) and the environment below. `{run}` and
`{copy}` are filled in by the launcher. Commands need absolute paths.

## Settings to flags

| Setting | Flag |
|---|---|
| `memory_mb` | `-Xmx<m>m` for the JVM (in `JOPTS`), plus `-rss_limit_mb=0` (the JVM's own footprint would trip libFuzzer's RSS limit); a heap overflow becomes an `OutOfMemoryError` finding |
| `input_timeout_s` | `-timeout=<s>` |
| run time (the launcher's `--time`) | `-max_total_time=<s>`; with no duration, `-max_total_time=0`. Always pass it: `@FuzzTest`'s `maxDuration` (default 5 minutes) otherwise ends the run |

## Environment (`--env`)

Add this block, filled in, to `.seclab/fuzz/env.sh` below `F` and `L`.
The shell keeps its own `PATH`, which finds Maven or Gradle (often in
Homebrew or user directories), and the exported `JAVA_HOME` picks their
JDK; the commands below pass `RUN_PATH` as the run's `PATH`:
<!-- ci: java-env -->
```sh
export JAVA_HOME=<java_home>
RUN_PATH=<java_home>/bin:/usr/bin:/bin
JOPTS="-Xmx<memory_mb>m -XX:+EnableDynamicAgentLoading -XX:-OmitStackTraceInFastThrow"
CP=$(cat "$F/build/jazzer/<module>.classpath" 2>/dev/null)
```

`<java_home>` is a JDK Jazzer supports (`build.md`, JDK), e.g. macOS's
`/usr/libexec/java_home -v 21`. `JOPTS` are the JVM flags of every
command: the heap, no agent warning, and stack traces even for hot
exceptions (without them, a continuous run reported a hot
`ArrayIndexOutOfBoundsException` again, without frames, as a new
finding). A multi-module Gradle build adds
`-Djazzer.instrument=<packages>` (`build.md`, What gets instrumented).

The launcher points `java.io.tmpdir` and `user.home` into the copy's
scratch through `JAVA_TOOL_OPTIONS` (the log starts with `Picked up
JAVA_TOOL_OPTIONS`); Jazzer writes helper scripts there.
`<fuzz_class>` is the fully qualified test class, `<fuzz_method>` the
`@FuzzTest` method.

## Develop runs (gate, rounds)

One JVM, writing new inputs into the persistent corpus:

<!-- ci: java-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  -max_total_time=<run_s> -timeout=<input_timeout_s> -rss_limit_mb=0 -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="$F/dict/$H.dict" "$F/corpus/$H"
```

- Without `-artifact_prefix`, crash files land in the test's inputs
  directory under `src/test/resources`; always pass it.
- The fuzz test's inputs directory and `.cifuzz-corpus/` are read as extra
  seeds if they exist.
- The run stops at the first finding (`crash-*`, `timeout-*`; exit 77);
  the next round resumes from the corpus.
- Status lines are libFuzzer's (`#<n> NEW cov: <n> ft: <n> corp: ...
  exec/s: <n>`), and the final statistics give executions per second.
- Instrumentation check (`build.md`): a plain `INFO: Instrumented <class>
  (took ...)` line for each target class, not `with custom hooks only`.
- `--list_fuzz_tests=` (empty value) lists the fuzz tests of a class.
- Useful extras: `-use_value_profile=1` after a plateau on comparisons,
  `--instrument=<globs>` (classic targets: `--instrumentation_includes`,
  `build.md`), `--custom_hooks=<classes>`, `--ignore=<tokens>` (queued
  findings, `triage.md`).
- If the target is a classic `fuzzerTestOneInput` class: leave out
  `--target_method`.

## Long runs

N copies of the develop command (`--copies`), with the run's own corpus
first (where every copy writes and re-reads new inputs) and the persistent
corpus second (read only):

<!-- ci: java-long -->
```sh
$L start --prepare --id $H-long<n> --harness $H --copies <copies> --time <run_s> --tree "<tree>" \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  --keep_going=0 -max_total_time=<run_s> -timeout=<input_timeout_s> -rss_limit_mb=0 -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="{run}/$H.dict" "{run}/corpus" "$F/corpus/$H"
```

With no duration, `-max_total_time=0` and no `--time`. `CP` comes from the
worktree's classpath file, `<module>-<sha>.classpath` (`build.md`,
"Classpath for standalone runs"): `env.sh` sets it from the checkout's
`<module>.classpath`, so set `CP` from the worktree's file before this
command.

- Copies share inputs through libFuzzer's `-reload` (on by default, about
  once a second), and each picks its own random seed.
- **`continuous`** (the default): with `--keep_going=0` a copy continues
  past findings, skipping stack traces it has already reported, and exits
  0 at its time limit; crashing inputs with new coverage also enter the
  corpus. **Stop at the first finding** (the user's choice): leave it out;
  each copy then stops at its first finding while the others continue.
- **Memory:** each copy is a JVM with its own heap (`-Xmx`) plus several
  hundred MB for the JVM and Jazzer; cap the copies by it.
- **The run's worktree** (`fuzz-campaign`): build there and write a classpath file
  for it (`build.md`), whose class directories are the worktree's. Each
  copy loads classes lazily, so the checkout's classpath would pick up
  develop's rebuilds.
- No `-fork` or `-jobs`: every job is a new JVM that pays start-up and
  warm-up again, and they share one scratch directory.

## Merging a run's corpus back (analysis, `fuzz-campaign`)

On the **current** build, as one `merge-<h>-<n>` run: first a replay that
reports findings, then `-merge=1` into the merge run's own corpus, which
then replaces the persistent corpus (`fuzz-campaign`):

<!-- ci: java-merge -->
```sh
$L start --id merge-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" \
  --env C="$F/corpus/$H" --env R="$F/runs/<long_run>/corpus" -- \
  sh -c '"$@" --keep_going=0 -runs=0 "$C" "$R" && "$@" -merge=1 "{run}/corpus" "$C" "$R"' sh \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  -timeout=<input_timeout_s> -rss_limit_mb=0 -artifact_prefix="{run}/artifacts/copy-{copy}/"
```

- `-merge=1` reports nothing and keeps crashing inputs; the replay before
  it is what records them as findings (exit 0 with `--keep_going=0`).
- Check the log: `MERGE-OUTER: <n> new files`.
- If the log shows repeated `MERGE-OUTER: attempt` lines: the inner runs
  failed, e.g. on a path with a space (the launcher refuses those).
- If a timeout ended the replay's JVM (exit 70): the merge doesn't run; the
  `timeout-` file names the input, a blocking bug (`fuzz-triage`).

## JUnit runs (regression, CI, hand-off)

Regression mode replays the empty input, the seed sources and the inputs
directories. The run is offline (`-o`, `--offline`): run the project's
tests once online first (a build step, `mvn -q test -Dtest=<fuzz_class>`),
which also fetches Surefire's JUnit provider.

Through the launcher, the working directory and `HOME` are in scratch:
name the build with an absolute path (`-f`, `-p`), and pass the real tool
caches (`<home>`, the user's home) and the shell's `PATH` with the JDK
first, which finds the build tool. `JAVA_TOOL_OPTIONS` carries `JOPTS` to
every JVM of the run, the test JVM too:

<!-- ci: java-junit -->
```sh
$L start --id <run_id> --harness $H --timeout <timeout_s> --env PATH="$JAVA_HOME/bin:$PATH" --env JAVA_HOME="$JAVA_HOME" \
  --env JAVA_TOOL_OPTIONS="$JOPTS" -- \
  mvn -f "<module_dir>/pom.xml" -o -Dmaven.repo.local="<home>/.m2/repository" test -Dtest='<fuzz_class>#<fuzz_method>'
```

If the module's sibling modules in a multi-module project aren't installed:
`mvn -f "<root>/pom.xml" -pl <module> -am ... -Dsurefire.failIfNoSpecifiedTests=false`
(from the module's own POM, the siblings don't resolve).

<!-- ci-skip: for the project's own runs; the campaign and the CI template run the standalone driver -->
```sh
# Gradle (--env GRADLE_USER_HOME=<home>/.gradle): --rerun, or an unchanged test task is skipped as up to date
gradle -p "<abs project>" --no-daemon --offline test --rerun --tests '<package>.<Class>.<method>'
# Fuzzing mode, for the project's own runs (one fuzz test per JVM)
JAZZER_FUZZ=1 JAZZER_MAX_DURATION=2m mvn test -Dtest='<Class>#<method>'
```

No Gradle daemon, so none outlives the run. Fuzzing mode writes its
corpus to `.cifuzz-corpus/` and crash files into the inputs directory, in
the source tree, and takes no libFuzzer flags; neither the campaign nor
the CI template (`fuzz-campaign`'s ci.md) fuzzes with it.

## Seeds from the project

Inputs in `src/test/resources/...Inputs/` already run as seeds; files from
`src/test/resources` of the code under test can be copied into
`corpus/$H/` for harnesses that take a raw file (one `byte[]` or
`String`, `harness.md`).

## Checking a run

<!-- ci: java-check-run -->
```sh
$L status <id>                                          # states, findings, status lines
tail -n 3 .seclab/fuzz/runs/<id>/copy-0/log             # libFuzzer status
```
