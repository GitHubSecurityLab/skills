# Java builds

## JDK

Use a JDK the installed Jazzer can instrument: Jazzer 0.30.0 works on JDK
21 and 25. On JDK 27 it logged `Failed to instrument java/...` for JDK
classes (e.g. `java/lang/ProcessBuilder`), which silently disables the detectors hooking them.

- Maven and Gradle may use another JDK than `java` on `PATH`: check
  `mvn -v` and `gradle -v`, and set `JAVA_HOME` (Gradle projects with a
  toolchain pick their own JDK).

## Dependencies (test scope only)

Maven:

```xml
<dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>org.junit</groupId><artifactId>junit-bom</artifactId>
      <version>5.11.4</version><type>pom</type><scope>import</scope>
    </dependency>
  </dependencies>
</dependencyManagement>
<!-- in <dependencies>: -->
<dependency>
  <groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><scope>test</scope>
</dependency>
<dependency>
  <groupId>com.code-intelligence</groupId><artifactId>jazzer-junit</artifactId>
  <version>0.30.0</version><scope>test</scope>
</dependency>
```

- Without the BOM (or the project's own JUnit BOM), Maven's nearest-wins
  rule picks `jazzer-junit`'s older `junit-platform-*` 1.9 jars, and test
  discovery fails with `NoSuchMethodError`.
- `junit-jupiter` brings the engine that standalone runs need, even when
  the project declares only `junit-jupiter-api`: `jazzer-junit` has none,
  and Surefire 3.x adds one to `mvn test` runs only.

Gradle:

```groovy
dependencies {
  testImplementation platform('org.junit:junit-bom:5.11.4')
  testImplementation 'org.junit.jupiter:junit-jupiter'
  testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
  testImplementation 'com.code-intelligence:jazzer-junit:0.30.0'
}
test { useJUnitPlatform() }
```

Jazzer's README shows `implementation`; it belongs in test scope.

If the project uses JUnit 4 or TestNG: add JUnit 5 (`junit-jupiter`) plus
`org.junit.vintage:junit-vintage-engine` (JUnit 4) or
`org.junit.support:testng-engine` (TestNG), all in test scope, so existing
tests keep running next to the fuzz tests. JUnit 6 with Jazzer 0.30.0 is
untested.

## Classpath for standalone runs

Write it once per build into `.seclab/fuzz/build/jazzer/<module>.classpath`;
rebuild it when dependencies change. A long run's worktree gets its own,
built there and written as `<module>-<sha>.classpath`, so its class
directories are the worktree's. Both commands below run project build
code: the first-build notice applies.

Maven, from the root (multi-module: add `-pl <module> -am`; in the module
directory, uninstalled sibling modules don't resolve); `<module_dir>` is
the module's absolute path, since runs start in their scratch directory:
<!-- ci: java-maven-classpath -->
```sh
mvn -q test-compile dependency:build-classpath -Dmdep.includeScope=test -Dmdep.outputFile=target/seclab.deps
mkdir -p "$F/build/jazzer" && echo "<module_dir>/target/test-classes:<module_dir>/target/classes:$(cat "<module_dir>/target/seclab.deps")" \
  > "$F/build/jazzer/<module>.classpath"
```

Gradle, through an init script; `<gradle>` is `./gradlew`, or `gradle`
without a wrapper, and `<task>` is `:seclabTestClasspath` in a
single-project build, `:<module>:seclabTestClasspath` in a multi-project
one:
<!-- ci: java-gradle-classpath -->
```sh
mkdir -p "$F/build/jazzer" && cat > "$F/build/jazzer/classpath.gradle" <<'EOF'
allprojects {
  plugins.withId('java') {
    tasks.register('seclabTestClasspath') {
      def cp = sourceSets.test.runtimeClasspath
      dependsOn(cp)  // builds the classes, and the sibling modules' JARs it lists
      doLast { println cp.asPath }
    }
  }
}
EOF
<gradle> -q --init-script "$F/build/jazzer/classpath.gradle" <task> > "$F/build/jazzer/<module>.classpath"
```

## What gets instrumented

The fuzzer gets coverage feedback only from instrumented classes.

- **`@FuzzTest`:** by default, the packages of classes found in classpath
  **directories** (the project's own `classes` output), not classes in
  JARs. Maven's sibling modules built with `-am` arrive as their
  `target/classes` directories; Gradle's arrive as JARs, like libraries and
  modules installed in `~/.m2`, and get no coverage feedback. Widen or
  narrow it with `-Djazzer.instrument=com.example.**,org.lib.**` (commas)
  in `JOPTS` (`run.md`), which every command and JUnit run takes:
  - for a multi-module Gradle build, list every module's packages;
  - add a library's package too when scope code extends it or passes it
    the input to parse (Commons CSV's lexer reads through a commons-io
    class).
- **Classic `fuzzerTestOneInput` targets:** every class, JARs included,
  and `--instrument` is ignored. Narrow it with
  `--instrumentation_includes=com.example.**:org.lib.**` (colons; with
  commas nothing matches).
- Exclude large dependencies the fuzzer gets lost in with
  `--instrumentation_excludes=` (colons).

## Instrumentation checks

The gate's first check, on a run's log: each
target class needs a plain `INFO: Instrumented <class> (took ...)` line.
With `F` as in `run.md`:

<!-- ci: java-instrumented -->
```sh
log="$F/runs/<run_id>/copy-0/log" bad=0
for c in <target_classes>; do grep -qF "INFO: Instrumented $c (took" "$log" || { echo "no coverage feedback: $c"; bad=1; }; done
! grep -E 'Failed to instrument|Error retransforming|VerifyError' "$log" && [ $bad = 0 ]
```

- `INFO: Instrumented <class> with custom hooks only` means hooks but no
  coverage feedback: the fuzzer is blind there, while JaCoCo still shows
  the class covered.
- `Error retransforming` with a `VerifyError` means a hook broke a class,
  which then runs uninstrumented and unhooked (`harness.md`, hooks).

## `junit-platform.properties` keys

Check a project's own `src/test/resources/junit-platform.properties` for
`jazzer.*` keys that hide findings, such as `jazzer.ignore` or
`jazzer.disabled_hooks`: the file configures JUnit runs, and standalone
runs of the module's fuzz tests too (command-line flags override it).

Useful keys: `jazzer.instrument`, `jazzer.custom_hooks`,
`jazzer.disabled_hooks`, `jazzer.keep_going`, `jazzer.max_duration`;
standalone runs take them as `--instrument=` and so on (lists separated
by `:`, except `--instrument`'s `,`). `jazzer.valueprofile=true` has no
such flag (`--valueprofile` fails): standalone runs pass libFuzzer's
`-use_value_profile=1`. Full list:
https://github.com/CodeIntelligenceTesting/jazzer/blob/main/docs/arguments-and-configuration-options.md

## Fuzzing-only changes

Prefer a `REPLACE` hook (`harness.md`) over editing sources: e.g. return
`true` from a signature check or a fixed value from `SecureRandom`. Hooks
live in the test sources and never reach production code.

## Common failures

| Symptom | Cause and fix |
|---|---|
| `NoSuchMethodError` in JUnit discovery | Mixed JUnit Platform versions (Maven's nearest-wins mediation picks Jazzer's older one); import `junit-bom` |
| The standalone driver: `must define exactly one function of this form: public static void fuzzerTestOneInput` | The classpath lacks the JUnit engine; add test-scope `junit-jupiter`. Surefire supplies it to `mvn test` only, so `mvn test` passing proves nothing here |
| `Tests run: 0` | Surefire too old for JUnit 5, or the test class isn't in the test source set |
| `Failed to instrument java/...` | JDK too new for the installed Jazzer |
| `UnsupportedClassVersionError` | The run's JDK is older than the compiled classes |
| Dependencies can't download | Offline sandbox (Codex): build once with network access |
