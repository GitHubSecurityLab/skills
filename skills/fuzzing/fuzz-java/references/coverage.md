# Java coverage

Replay the corpus with the standalone driver under the JaCoCo agent, then
convert JaCoCo's XML report to lcov. This needs no change to the project's
build files and no coverage build variant.

- If the build is Gradle: Gradle puts classes in `build/classes/java/main`
  and test classes in `build/classes/java/test`. The blocks in this file
  use Maven's `target/`.

## Replay and export

1. **Set up JaCoCo** once: download the JaCoCo agent and command-line jars
   into `.seclab/fuzz/build/jacoco/`. Both come from the same JaCoCo
   release, one that supports the JDK's class files:
   <!-- ci: jacoco-setup -->
   ```sh
   V=0.8.14 B=https://repo1.maven.org/maven2/org/jacoco J="$F/build/jacoco"
   mkdir -p "$J"
   curl -fsSL -o "$J/jacocoagent.jar" "$B/org.jacoco.agent/$V/org.jacoco.agent-$V-runtime.jar"
   curl -fsSL -o "$J/jacococli.jar" "$B/org.jacoco.cli/$V/org.jacoco.cli-$V-nodeps.jar"
   ```
2. **Replay** the corpus under the agent, through the launcher, with run
   id `cov-<h>-<n>`. `L`, `F`, `H` and the environment are as in `run.md`
   next to this file:
   <!-- ci: java-cov-replay -->
   ```sh
   $L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
     "$JAVA_HOME/bin/java" $JOPTS \
     -javaagent:"$F/build/jacoco/jacocoagent.jar=destfile={run}/jacoco.exec,append=false" -cp "$CP" \
     com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
     --keep_going=0 -runs=0 -timeout=<input_timeout_s> -rss_limit_mb=0 \
     -artifact_prefix="{run}/artifacts/copy-{copy}/" "$F/corpus/$H"
   ```
   - `-runs=0` runs every corpus input once, without fuzzing. It needs
     no `-max_total_time` (`run.md`, "Settings to flags").
   - `--keep_going=0` continues past inputs that throw: the finding is
     recorded in `{run}/artifacts/copy-0/`, and the replay goes on. The
     agent writes the `.exec` file when the JVM exits.
   - `append=false` gives every replay a fresh `.exec` file. By default,
     JaCoCo appends to an existing one.
   - If other corpora count too, add their directories at the end. For
     example, add a long run's `corpus` to measure what that run found.
3. **Check** the result line (`finished`) and that the log ends with `Done
   <n> runs`.
   - If the state is `exited 70`: a timeout ended the JVM, and the `.exec`
     file stays empty. The `timeout-` file names the input; the timeout is
     a blocking bug (`fuzz-triage`). Replay a copy of the corpus without
     that input, and mark the snapshot partial.
4. **Export** the `.exec` file to lcov (reads files only). `--classfiles`
   decides what the report covers. List the modules and library JARs in
   scope, with one `--classfiles` and one `--source-root` for each.
   Classes not listed don't appear in the lcov file.
   <!-- ci: java-cov-export -->
   ```sh
   C="$F/coverage/$H"; mkdir -p "$C"
   cp "$F/runs/cov-$H-<n>/jacoco.exec" "$C/r<round>.exec"
   "$JAVA_HOME/bin/java" -jar "$F/build/jacoco/jacococli.jar" report "$C/r<round>.exec" --classfiles <module>/target/classes \
     --xml "$C/r<round>.xml" 2>&1 | tee "$C/r<round>.report.log" &&
     ! grep -q 'not match' "$C/r<round>.report.log" &&
     python3 "$F/bin/coverage.py" from-jacoco "$C/r<round>.xml" --source-root <module>/src/main/java \
       -o "$C/r<round>.lcov" &&
     rm "$C/r<round>.xml" "$C/r<round>.report.log"
   ```
   - **"does not match" is an error.** If the class files differ from
     the ones that ran (a rebuild in between), `jacococli` warns
     "Execution data for class … does not match", still exits 0, and
     reports those classes as uncovered. The block stops there and keeps
     the report log. Replay again on the current build (step 2).
   - **Keep the `.exec` files.** They're the only data that can be
     combined correctly.
   - The lcov file has line and branch records per source file. Each path
     is a source root joined with the file's package path, e.g.
     `record/src/main/java/toy/Record.java`. So give the source roots
     relative to the repository root, as the block does; then
     `coverage.py --root .` matches the paths against the scope files.
   - JaCoCo counts branches at `if` and `switch` only, not exception
     edges. A `catch` block shows only as lines, so a branch plateau can
     hide uncovered error handling. Check the lines too.

**Exact subset** (`reach.py`, the gate's state-leak check): to run exactly
the inputs in `{files}`, use step 2's command with `{files}` instead of
the corpus directories. A directory replay would also run the test's
inputs directory and `.cifuzz-corpus`.
- If the fuzz test has JUnit argument sources (`@MethodSource`,
  `@ValueSource`): they run first in every JVM. They hide state that leaks
  from the first input, and add their own coverage. Give such a harness
  its seeds as files or `@ValuePool` values instead.

**The gate's state-leak check** is the block below, for `fuzz-harness`'s
gate.md, "No state leaking between inputs". It replays a sample in one
JVM, then each input of the sample in a fresh JVM, and compares the two
sides. The fresh JVMs append to one `.exec` file. JaCoCo records whether
code ran, not how often. So the check compares covered sets, and a leak
that only changes counts stays hidden:

<!-- ci: java-leak-check -->
```sh
G="$F/runs/gate-$H-<n>"; S="$G/sample"; C="$F/coverage/$H"; J="$F/build/jacoco"; mkdir -p "$S" "$C"
find "$F/corpus/$H" -type f -size +0c | sort -R | awk 'NR <= <sample_size>' | while IFS= read -r f; do i=$((i+1)); cp "$f" "$S/$i-${f##*/}"; done
for side in one fresh; do
  $L start --id "gate-$H-<n>-$side" --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JOPTS="$JOPTS" -- sh -c '
      java=$1 agent=$2 cp=$3 run=$4 side=$5; shift 5
      j() { "$java" $JOPTS -javaagent:"$agent=destfile=$run/jacoco.exec" \
        -cp "$cp" com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
        --keep_going=0 -runs=0 -timeout=<input_timeout_s> -rss_limit_mb=0 "$@"; }
      if [ "$side" = one ]; then j "$@"; exit; fi
      for f; do j "$f" || exit; done' sh "$JAVA_HOME/bin/java" "$J/jacocoagent.jar" "$CP" "{run}" "$side" "$S"/* > /dev/null
  cp "$F/runs/gate-$H-<n>-$side/jacoco.exec" "$G/$side.exec"
done
"$JAVA_HOME/bin/java" -jar "$J/jacococli.jar" merge "$G/one.exec" "$G/fresh.exec" --destfile "$G/union.exec" > /dev/null
for p in one fresh union; do
  "$JAVA_HOME/bin/java" -jar "$J/jacococli.jar" report "$G/$p.exec" --classfiles <module>/target/classes \
    --classfiles <module>/target/test-classes --xml "$G/$p.xml" > /dev/null
  python3 "$F/bin/coverage.py" from-jacoco "$G/$p.xml" --source-root <module>/src/main/java \
    --source-root <module>/src/test/java -o "$C/gate-$p.lcov"
done
python3 "$F/bin/coverage.py" overlap "$C/gate-one.lcov" "$C/gate-fresh.lcov" --union "$C/gate-union.lcov" --root .
```

- The test classes are included: state often leaks in the harness.

**Inputs that reach a line**: `reach.py` finds them, for targeted inputs
(`fuzz-coverage`'s corpus.md):

<!-- ci: java-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" --timeout <timeout_s> --ok-exit 0,77 \
  --replay "$JAVA_HOME/bin/java $JOPTS -javaagent:$F/build/jacoco/jacocoagent.jar=destfile={out}/jacoco.exec -cp $CP com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> --keep_going=1 -runs=0 -timeout=<input_timeout_s> -rss_limit_mb=0 {files}" \
  --export "sh -c '$JAVA_HOME/bin/java -jar \$0 report \$1/jacoco.exec --classfiles \$2 --xml \$1/r.xml > /dev/null && python3 \$3 from-jacoco \$1/r.xml --source-root \$4 -o \$5' $F/build/jacoco/jacococli.jar {out} <module>/target/classes $F/bin/coverage.py <module>/src/main/java {lcov}"
```

Jazzer writes no finding file for inputs given as files. With
`--keep_going=1` it still runs them all, then exits 77 if one threw, which
`reach.py` reports.

## Combining Java coverage

Combine Java snapshots through their `.exec` files, not their lcov files.
JaCoCo's XML has branch counts per line, not branch identities. So
`from-jacoco` numbers a line's branches itself, and tags its output as
JaCoCo-derived. Two such lcov files can't be combined. On a line with two
branches, a harness that takes the true arm and another that takes the
false arm both read "1 of 2". `coverage.py` refuses to merge them.

- **Merge** the `.exec` files instead, only from replays of the same
  build. Then report and convert the merged file as in step 4:

  <!-- ci-skip: the java-leak-check block runs the same merge -->
  ```sh
  "$JAVA_HOME/bin/java" -jar .seclab/fuzz/build/jacoco/jacococli.jar merge <a>.exec <b>.exec \
    --destfile .seclab/fuzz/coverage/merged/<n>.exec
  ```

- **Overlap** of two Java snapshots needs that merged report as `--union`.
  `coverage.py` then computes what only A covers as (A∪B) − B, per line:

  <!-- ci-skip: the java-leak-check block runs the same overlap -->
  ```sh
  python3 .seclab/fuzz/bin/coverage.py overlap <a>.lcov <b>.lcov --union <a∪b>.lcov \
    --scope .seclab/fuzz/targets/<id>.scope --root .
  ```
