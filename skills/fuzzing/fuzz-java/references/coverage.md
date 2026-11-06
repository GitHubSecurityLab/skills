# Java coverage

Replay the corpus with the standalone driver under the JaCoCo agent, then
convert JaCoCo's XML report to lcov. Neither the project's build files nor
a coverage build variant change.

- If the build is Gradle: it puts classes in `build/classes/java/main` and
  test classes in `build/classes/java/test` (the blocks in this file use
  Maven's `target/`).

## Replay and export

1. **Set up JaCoCo** once: download the JaCoCo agent and command-line jars
   (same version) into `.seclab/fuzz/build/jacoco/`, from a JaCoCo release
   that supports the JDK's class files:
   <!-- ci: jacoco-setup -->
   ```sh
   V=0.8.14 B=https://repo1.maven.org/maven2/org/jacoco J="$F/build/jacoco"
   mkdir -p "$J"
   curl -fsSL -o "$J/jacocoagent.jar" "$B/org.jacoco.agent/$V/org.jacoco.agent-$V-runtime.jar"
   curl -fsSL -o "$J/jacococli.jar" "$B/org.jacoco.cli/$V/org.jacoco.cli-$V-nodeps.jar"
   ```
2. **Replay** the corpus under the agent, through the launcher (run id
   `cov-<h>-<n>`), with `L`, `F`, `H` and the environment as in `run.md`
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
   - `-runs=0` runs every corpus input once without fuzzing; `maxDuration`
     doesn't cut it short.
   - `--keep_going=0` continues past inputs that throw: the finding is
     recorded (`{run}/artifacts/copy-0/`), and the replay goes on. The
     agent writes the `.exec` file when the JVM exits.
   - `append=false` gives every replay a fresh `.exec` file; by default
     JaCoCo appends to an existing one.
   - If other corpora count too (e.g. a long run's `corpus`, to measure what
     it found): add their directories at the end.
3. **Check** the result line (`finished`) and that the log ends with `Done
   <n> runs`.
   - If the state is `exited 70`: a timeout ended the JVM, and the `.exec`
     file stays empty. The `timeout-` file names the input, a blocking bug
     (`fuzz-triage`): replay a copy of the corpus without it, and mark the
     snapshot partial.
4. **Export** the `.exec` file to lcov (reads files only). `--classfiles`
   decides what the report covers: list the modules and library JARs in
   scope, one `--classfiles` and `--source-root` each. Classes not listed
   don't appear in the lcov file.
   <!-- ci: java-cov-export -->
   ```sh
   C="$F/coverage/$H"; mkdir -p "$C"
   cp "$F/runs/cov-$H-<n>/jacoco.exec" "$C/r<round>.exec"
   "$JAVA_HOME/bin/java" -jar "$F/build/jacoco/jacococli.jar" report "$C/r<round>.exec" --classfiles <module>/target/classes \
     --xml "$C/r<round>.xml" 2>&1 | tee "$C/r<round>.report.log"
   ! grep -q 'not match' "$C/r<round>.report.log"
   python3 "$F/bin/coverage.py" from-jacoco "$C/r<round>.xml" --source-root <module>/src/main/java \
     -o "$C/r<round>.lcov"
   rm "$C/r<round>.xml" "$C/r<round>.report.log"
   ```
   - **"does not match" is an error.** If the class files differ from the
     ones that ran (a rebuild in between), `jacococli` warns "Execution
     data for class … does not match", still exits 0, and reports those
     classes as uncovered. Rebuild or replay again with the same classes.
   - **Keep the `.exec` files.** They're the only data that can be combined
     correctly.
   - The lcov file has line and branch records per source file, with paths
     relative to the source roots given; `coverage.py --root .` then
     matches them against the scope files.
   - JaCoCo counts branches at `if` and `switch` only, not exception edges:
     a `catch` block shows only as lines, so a branch plateau can hide
     uncovered error handling. Check the lines too.

**Exact subset** (`reach.py`, the gate's state-leak check): to run exactly
the inputs in `{files}`, use the same replay command with `{files}` instead
of the corpus directories. A directory replay would also run the test's
inputs directory and `.cifuzz-corpus`.
- If the fuzz test has JUnit argument sources (`@MethodSource`,
  `@ValueSource`): they run first in every JVM, hide state that leaks from
  the first input and add their own coverage. Give such a harness its
  seeds as files or `@ValuePool` values instead.

**The gate's state-leak check** (`fuzz-harness`'s gate.md): a sample
replayed in one JVM, then each input in a fresh JVM (appending to one
`.exec` file), and the two compared. JaCoCo records whether code ran, not
how often, so the check compares covered sets, and a leak that only
changes counts stays hidden:

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

**Inputs that reach a line** (`reach.py`, directed inputs in
`fuzz-coverage`):

<!-- ci: java-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" --timeout <timeout_s> --ok-exit 0,77 \
  --replay "$JAVA_HOME/bin/java $JOPTS -javaagent:$F/build/jacoco/jacocoagent.jar=destfile={out}/jacoco.exec -cp $CP com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> --keep_going=1 -runs=0 -timeout=<input_timeout_s> -rss_limit_mb=0 {files}" \
  --export "sh -c '$JAVA_HOME/bin/java -jar \$0 report \$1/jacoco.exec --classfiles \$2 --xml \$1/r.xml > /dev/null && python3 \$3 from-jacoco \$1/r.xml --source-root \$4 -o \$5' $F/build/jacoco/jacococli.jar {out} <module>/target/classes $F/bin/coverage.py <module>/src/main/java {lcov}"
```

Jazzer writes no finding file for inputs given as files: with
`--keep_going=1` it still runs them all, then exits 77 if one threw, which
`reach.py` reports.

## Combining Java coverage

Combine Java snapshots through their `.exec` files, not their lcov files.
JaCoCo's XML has branch counts per line, not branch identities, so
`from-jacoco` numbers a line's branches itself and tags its output as
JaCoCo-derived. Two such lcov files can't be combined: on a line with two
branches, one harness taking the true arm and another the false arm both
read "1 of 2". `coverage.py` refuses to merge them.

- **Merge** the `.exec` files instead, only from replays of the same build,
  then report and convert the merged file as above:

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
