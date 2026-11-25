# Running Jazzer

Every run goes through `launcher.py` (`fuzz-harness`'s runs.md explains `F`,
`L` and placeholders like `{run}`), and every command starts with
`. .seclab/fuzz/env.sh; H=<harness>;`. A harness in another module also
sets `CP` there ("Environment", below). Fuzzing runs, replays and
reproductions use the standalone driver; JUnit runs (below) use the build
tool.

## Settings to flags

| Setting | Flag |
|---|---|
| `memory_mb` | `-Xmx<m>m` for the JVM (in `JOPTS`), plus `-rss_limit_mb=0` |
| `input_timeout_s` | `-timeout=<s>` |
| run time (the launcher's `--time`) | `-max_total_time=<s>` |

- `memory_mb`: a heap overflow becomes an `OutOfMemoryError` finding.
  `-rss_limit_mb=0` keeps the JVM's own footprint from tripping
  libFuzzer's RSS limit.
- Run time: pass `-max_total_time` on every fuzzing run. Otherwise
  `@FuzzTest`'s `maxDuration` (default 5 minutes) ends the run. It doesn't
  cut replays and merges short, so they need no `-max_total_time`.

## Environment (`--env`)

Fill in this block and add it to `.seclab/fuzz/env.sh`, below `F` and
`L`. The shell keeps its own `PATH`, which finds Maven or Gradle (often in
Homebrew or user directories). The exported `JAVA_HOME` picks the JDK that
runs Maven or Gradle (`build.md`, "JDK", says when another one compiles
and tests). The commands below pass `RUN_PATH` as the run's `PATH`:
<!-- ci: java-env -->
```sh
export JAVA_HOME=<java_home>
RUN_PATH=<java_home>/bin:/usr/bin:/bin
JOPTS="-Xmx<memory_mb>m -XX:+EnableDynamicAgentLoading -XX:-OmitStackTraceInFastThrow"
CP=$(cat "$F/build/jazzer/<module>.classpath" 2>/dev/null)
```

- `<java_home>`: a JDK Jazzer supports (`build.md`, "JDK"), e.g. macOS's
  `/usr/libexec/java_home -v 21`.
- `CP`: the test classpath of `<module>`, the first harness's module,
  from its classpath file (`build.md`, "Classpath for standalone runs").
  A command for a harness in another module sets `CP` from that module's
  file, right after `H=<harness>;`:
  `CP=$(cat "$F/build/jazzer/<module>.classpath");`. The target file's
  Harness block names each harness's module.
- `JOPTS`: the JVM flags of every command. They set the heap, turn off
  the agent warning, and keep stack traces even for hot exceptions.
  Without `-XX:-OmitStackTraceInFastThrow`, a continuous run reported a
  hot `ArrayIndexOutOfBoundsException` again, without frames, as a new
  finding.
- If the build is a multi-module Gradle build: add
  `-Djazzer.instrument=<packages>` to `JOPTS` (`build.md`, "What gets
  instrumented").

The launcher points `java.io.tmpdir` and `user.home` into the copy's
scratch through `JAVA_TOOL_OPTIONS` (the log starts with `Picked up
JAVA_TOOL_OPTIONS`). Jazzer writes helper scripts into that scratch.

`<fuzz_class>` is the fully qualified test class, and `<fuzz_method>` the
`@FuzzTest` method.

## Develop runs (gate, rounds)

A develop run is one JVM, which writes new inputs into the persistent
corpus:

<!-- ci: java-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  -max_total_time=<run_s> -timeout=<input_timeout_s> -rss_limit_mb=0 -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="$F/dict/$H.dict" "$F/corpus/$H"
```

- Always pass `-artifact_prefix`. Without it, crash files land in the
  test's inputs directory under `src/test/resources`.
- The fuzz test's inputs directory and `.cifuzz-corpus/` are read as extra
  seeds if they exist.
- The run stops at the first finding: a `crash-*` file with exit 77, or a
  `timeout-*` file with exit 70. The next round resumes from the corpus.
- Status lines are libFuzzer's: `#<n> NEW cov: <n> ft: <n> corp: ...
  exec/s: <n>`. The final statistics give executions per second.
- Instrumentation check (`build.md`): each target class needs a plain
  `INFO: Instrumented <class> (took ...)` line, not
  `with custom hooks only`.
- `--list_fuzz_tests=` (empty value) lists the fuzz tests of a class.
- Useful extras:
  - `-use_value_profile=1` after a plateau on comparisons;
  - `--instrument=<globs>`, or `--instrumentation_includes` for classic
    targets (`build.md`);
  - `--custom_hooks=<classes>`;
  - `--ignore=<tokens>` for findings whose fix is queued (`triage.md`).
- If the target is a classic `fuzzerTestOneInput` class: leave out
  `--target_method`.

## Long runs

A long run is N copies of the develop command (`--copies`). List the
run's own corpus first and the persistent corpus second. Every copy writes
new inputs into the run's corpus and re-reads them from there; the
persistent corpus is only read.

**The run's worktree** (`fuzz-campaign`) is `<tree>`, and `<tree_build>`
its `.seclab/fuzz/build` directory. Build there, and write a classpath
file there too (`build.md`, "Classpath for standalone runs"). That file's
class directories are the worktree's. Then set `CP` from it:
`CP=$(cat "<tree_build>/jazzer/<module>.classpath")`. The `CP` that
`env.sh` sets comes from the checkout's classpath file, and develop keeps
rebuilding the checkout's classes. Each copy loads classes lazily, so on
the checkout's classpath it would pick up those rebuilds.

<!-- ci: java-long -->
```sh
$L start --prepare --id $H-long<n> --harness $H --copies <copies> --time <run_s> --tree "<tree>" \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  --keep_going=0 -max_total_time=<run_s> -timeout=<input_timeout_s> -rss_limit_mb=0 -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="{run}/$H.dict" "{run}/corpus" "$F/corpus/$H"
```

- If the run has no duration: use `-max_total_time=0`, and leave out
  `--time`.
- Copies share inputs through libFuzzer's `-reload`, which is on by
  default and reloads about once a second. Each copy picks its own random
  seed.
- **`continuous`** (the default): with `--keep_going=0`, a copy continues
  past findings and skips stack traces it has already reported. It exits
  0 at its time limit. Crashing inputs with new coverage also enter the
  corpus.
- **Stop at the first finding** (the user's choice): leave out
  `--keep_going=0`. Each copy then stops at its first finding, while the
  others continue.
- **Memory:** each copy is a JVM with its own heap (`-Xmx`), plus several
  hundred MB for the JVM and Jazzer. Cap the number of copies by that
  memory.
- No `-fork` or `-jobs`: every job is a new JVM that pays start-up and
  warm-up again, and the jobs share one scratch directory.

## Merging a run's corpus back (analysis, `fuzz-campaign`)

Merge on the **current** build, as one `merge-<h>-<n>` run. It first runs
a replay that reports findings, then `-merge=1` into the merge run's own
corpus. That corpus then replaces the persistent corpus (`fuzz-campaign`):

<!-- ci: java-merge -->
```sh
$L start --id merge-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" \
  --env C="$F/corpus/$H" --env R="$F/runs/<long_run>/corpus" -- \
  sh -c '"$@" --keep_going=0 -runs=0 "$C" "$R" && "$@" -merge=1 "{run}/corpus" "$C" "$R"' sh \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  -timeout=<input_timeout_s> -rss_limit_mb=0 -artifact_prefix="{run}/artifacts/copy-{copy}/"
```

- `-merge=1` reports nothing and keeps crashing inputs. The replay
  before it records them as findings, still exiting 0 with
  `--keep_going=0`.
- Check the log: `MERGE-OUTER: <n> new files`.
- If the log shows repeated `MERGE-OUTER: attempt` lines: the inner runs
  failed, e.g. on a path with a space. The launcher refuses such paths.
- If a timeout ended the replay's JVM (exit 70): the merge doesn't run.
  The `timeout-` file names the input; the timeout is a blocking bug
  (`fuzz-triage`).

## JUnit runs (regression, CI, upstreaming)

Regression mode replays the empty input, the seed sources and the inputs
directories. The run is offline (`-o`, `--offline`), so first run the
project's tests once online, as a build step. For Maven, use
`mvn -q test -Dtest=<fuzz_class>`, which also fetches Surefire's JUnit
provider.

Through the launcher, the working directory, `HOME` and `user.home` are
in scratch. So:
- Name the build with an absolute path (`-f`, `-p`).
- Run a wrapper from the build's root: in place of `mvn`, use
  `sh -c 'cd "$0" && exec ./mvnw "$@"' "<root>"`, and likewise for
  `./gradlew`. An older `mvnw` (Takari's 0.5) looks for `.mvn` from the
  working directory up, not next to itself.
- Pass the real tool caches. `<home>` is the user's home. `mvnw` keeps
  its Maven in `MAVEN_USER_HOME`: add `--env MAVEN_USER_HOME=<home>/.m2`.
  `gradlew` keeps its Gradle in `GRADLE_USER_HOME`, which the Gradle
  block passes.
- Pass the user's Maven files, which Maven reads from `.m2` under
  `user.home`: if `<home>/.m2/settings.xml` exists, add
  `-s "<home>/.m2/settings.xml"`, and if `<home>/.m2/toolchains.xml`
  exists, `-t "<home>/.m2/toolchains.xml"`. A missing file is an error.
  If `settings.xml` sets `<localRepository>`, use that path in place of
  `<home>/.m2/repository`.
- If the Gradle build sets a toolchain (`build.md`, "JDK"), pass its JDK:
  Gradle misses the JDKs it looks up in the user's home, such as SDKMAN's,
  asdf's or those in `<home>/.m2/toolchains.xml`. In the shell,
  `gradle -q javaToolchains` lists the JDKs with their locations. Add the
  one matching the toolchain:
  `-Porg.gradle.java.installations.paths=<jdk_dir>`.
- Pass the shell's `PATH` with the JDK first. That `PATH` finds the build
  tool.

`JAVA_TOOL_OPTIONS` carries `JOPTS` to every JVM of the run, the test JVM
too. Maven:

<!-- ci: java-junit -->
```sh
$L start --id <run_id> --harness $H --timeout <timeout_s> --env PATH="$JAVA_HOME/bin:$PATH" --env JAVA_HOME="$JAVA_HOME" \
  --env JAVA_TOOL_OPTIONS="$JOPTS" -- \
  mvn -f "<module_dir>/pom.xml" -o -Dmaven.repo.local="<home>/.m2/repository" test -Dtest='<fuzz_class>#<fuzz_method>'
```

If the module's sibling modules in a multi-module project aren't
installed, use
`mvn -f "<root>/pom.xml" -pl <module> -am ... -Dsurefire.failIfNoSpecifiedTests=false`.
From the module's own POM, uninstalled siblings don't resolve.

Gradle finds its caches through `GRADLE_USER_HOME`, and the
`mavenLocal()` repository through `maven.repo.local`. In the block,
`<module_dir>` is the fuzz test's project directory, which is the root in
a single-project build:

<!-- ci: java-gradle-junit -->
```sh
$L start --id <run_id> --harness $H --timeout <timeout_s> --env PATH="$JAVA_HOME/bin:$PATH" --env JAVA_HOME="$JAVA_HOME" \
  --env JAVA_TOOL_OPTIONS="$JOPTS" --env GRADLE_USER_HOME="<home>/.gradle" -- \
  gradle -p "<module_dir>" --no-daemon --offline -Dmaven.repo.local="<home>/.m2/repository" \
    test --rerun --tests '<fuzz_class>.<fuzz_method>'
```

- `--rerun`: without it, an unchanged test task is skipped as up to date.
- `--no-daemon`: no daemon outlives the run.
- Gradle gives the test JVM the test task's `maxHeapSize` (512 MB by
  default) as `-Xmx`, which overrides the one in `JOPTS`.

<!-- ci-skip: for the project's own runs; the campaign and the CI template run the standalone driver -->
```sh
# Fuzzing mode, for the project's own runs (one fuzz test per JVM)
JAZZER_FUZZ=1 JAZZER_MAX_DURATION=2m mvn test -Dtest='<Class>#<method>'
```

Fuzzing mode writes its corpus to `.cifuzz-corpus/` and crash files into
the inputs directory, in the source tree. It takes no libFuzzer flags.
Neither the campaign nor the CI template (`fuzz-campaign`'s ci.md) fuzzes
with it.

## Seeds from the project

Inputs in `src/test/resources/...Inputs/` already run as seeds. For
harnesses that take a raw file (one `byte[]` or `String`, `harness.md`),
files from `src/test/resources` of the code under test can be copied into
`corpus/$H/`.

## Checking a run

<!-- ci: java-check-run -->
```sh
$L status <id>                                          # states, findings, status lines
tail -n 3 .seclab/fuzz/runs/<id>/copy-0/log             # libFuzzer status
```
