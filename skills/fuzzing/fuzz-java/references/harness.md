# Java fuzz tests

## `@FuzzTest`

Place the fuzz test next to the project's unit tests, named like them
(often `<Thing>FuzzTest`), in the test source set:

```java
package com.example.parser;

import com.code_intelligence.jazzer.junit.FuzzTest;
import com.code_intelligence.jazzer.mutation.annotation.InRange;
import com.code_intelligence.jazzer.mutation.annotation.NotNull;
import com.code_intelligence.jazzer.mutation.annotation.WithUtf8Length;

class HeaderParserFuzzTest {
  @FuzzTest
  void parse(@NotNull @WithUtf8Length(max = 4096) String header, @InRange(min = 0, max = 2) int mode) {
    try {
      HeaderParser.parse(header, Mode.values()[mode]);
    } catch (MalformedHeaderException expected) {
      // documented for invalid input
    }
  }
}
```

- Parameter styles:
  - **typed parameters**: strings, numbers, enums, arrays, `List`, `Map`,
    records, beans, builders, protobuf messages. They're the default for
    new tests of APIs. Constrain them with annotations from
    `com.code_intelligence.jazzer.mutation.annotation`: `@NotNull`,
    `@InRange`, `@WithUtf8Length`, `@WithLength`, `@WithSize`, `@Ascii`,
    `@ElementOf`, `@Positive`, `@Finite`
    (https://github.com/CodeIntelligenceTesting/jazzer/blob/main/docs/mutation-framework.md).
  - **one `byte[]` (the fastest) or
    `@NotNull @WithUtf8Length(max = <above the largest seed>) String`
    parameter**, for a parser of a file format, binary or text. Only these
    take a raw file as the input one to one, so test files, generators and
    `corpus/<h>/` work as seeds. `(String s)` or `(String s, int mode)`,
    by contrast, decode a file into other values.
    - Without `@WithUtf8Length`, a `String` is silently cut to 1,000
      UTF-8 bytes, seeds included.
    - In a `String`, invalid UTF-8 is always repaired; a `byte[]` keeps
      it.
    - A parser with options (a dialect, flags, limits) gets a second,
      typed fuzz test for them, from the start, not after a plateau. That
      test takes the options as parameters, and the document as a
      `byte[]` with the seed files as `@ValuePool` values.
  - **a typed structure written by the project's own serializer**, when
    byte mutations keep breaking a format's validity (`fuzz-coverage`'s
    diagnosis.md). The test takes model objects or a builder as
    parameters, and runs the project's writer, then its parser: the
    parser gets valid syntax by construction. The raw `byte[]` test
    stays, for malformed input.
  - `FuzzedDataProvider` (`com.code_intelligence.jazzer.api`):
    `consumeInt`, `consumeString(maxLength)`, `consumeRemainingAsBytes()`
    and similar. Its input layout: `fuzz-harness`, "Design the harness".
- Catch only exceptions the API documents for invalid input. Everything
  else is a finding (`unexpected_exception` in triage).
- Property checks: JUnit assertions (`assertEquals`) or
  `throw new AssertionError(...)`.
- **Steering** (`com.code_intelligence.jazzer.api.Jazzer`):
  - For DoS targets, `Jazzer.maximize(cost, 0, <max>)` rewards inputs
    that raise a deterministic cost, such as loop iterations or an
    allocation size, but not wall time.
  - For state machines, `Jazzer.exploreState(state)` counts each value of
    a small state variable (a `byte`) as new coverage.
- `@FuzzTest(maxDuration = "...")` defaults to 5 minutes in fuzzing mode.
  The campaign overrides it per run (`run.md`).
- If you change a typed test's parameter types, a record's or bean's
  fields included: the change reinterprets the test's corpus. Replay the
  corpus for a new baseline (`fuzz-coverage`). Bugs keep their
  harness-independent reproducers.

## Seeds and dictionaries

- Seeds for typed parameters are `@ValuePool` values (below). They aren't
  files, so a 10-second develop run first fills `corpus/<h>/` for the
  gate's seeds-only replay (`fuzz-harness`'s gate.md, "Reaches the
  target").
- `@ValuePool` (Jazzer 0.28+) feeds values into the typed mutators while
  fuzzing, such as magic strings, boundary numbers, or sample files as
  `byte[]`. It's the typed form of dictionary entries and trigger values.
  - Example: `@ValuePool("tokens") @NotNull String s`, with
    `static Stream<?> tokens()` in the test class.
  - `p` (default 0.1) sets how often a value comes from the pool.
  - `files` globs resolve against the working directory. Under the
    launcher, that's the scratch directory.
- Files in `src/test/resources/<package path>/<Class>Inputs/<method>/`
  (or `<Class>Inputs/` for all methods) seed fuzzing, and run in every
  regression run. Commit only regression inputs there; campaign seeds go
  to `corpus/<h>/`.
- Dictionaries: `@DictionaryEntries({"<tag", "xmlns"})` or
  `@DictionaryFile(resourcePath = "header.dict")` on the method, or
  `-dict` in standalone runs.
- If the fuzz test has JUnit argument sources (`@MethodSource`,
  `@ValueSource`): they seed fuzzing too. But they run in every replay
  JVM, which skews the gate's state-leak check and `reach.py`.
  **`@FuzzTest` must be the last annotation,** after the argument
  sources. Otherwise fuzzing fails at start
  (`FuzzTestConfigurationError`), while regression mode still passes,
  and so does the project's CI.

## Setup and lifecycle

- Expensive one-time setup: a `static` field or `@BeforeAll`.
- `@FuzzTest(lifecycle = Lifecycle.PER_EXECUTION)` runs `@BeforeEach` and
  `@AfterEach` around every input: fresh state, but slower. The default,
  `PER_TEST`, runs them once.

## Bug detectors

Bug detectors are on by default. They hook calls in instrumented
(project) code. A sink that a detector sees only partly needs a
custom check (`fuzz-coverage`, "Check the sinks"). What each detector
checks, as of Jazzer 0.30.0:

| Detector | Reports when | Doesn't see |
|---|---|---|
| OS command injection: `ProcessBuilder.start` | the executable (`argv[0]`) is `jazze` | the arguments: `sh -c <input>`, option injection. **Partly**: any other command runs for real |
| SQL injection: `Statement.execute*`, JPA native queries | the driver throws a syntax error for the query | queries a mock or a stub driver never parses: test with in-memory H2 or SQLite. A failing `PreparedStatement` shows as an `ArrayIndexOutOfBoundsException` (`triage.md`) |
| Path traversal: `java.io` streams, readers and writers, `Files`, `FileChannel`, `Scanner` | the normalized path is the target `../jazzer-traversal` | other files outside the directory the API may touch. **Partly** |
| Reflection, expression languages (EL, SpEL, OGNL, JEXL 2, MVEL, Bean Validation messages), FreeMarker templates, script engines | the honeypot class `jaz.Zer` loads, or the script runs `jaz`+`zer` | `Method.invoke` and `getMethod` on existing classes; other expression and template engines |
| Deserialization: `ObjectInputStream` | a gadget chain reaches the honeypot | other deserializers (Jackson, XStream, Kryo, YAML), unless they construct or call the honeypot (below) |
| LDAP, XPath injection | the query or filter is invalid syntax | injections that stay valid syntax |
| JNDI lookups: `Context.lookup` | the name starts with `ldap://g.co/` or `rmi://g.co/` | |
| SSRF: sockets, `SocketChannel` | any connection not allowed (`allowNetworkConnections`) | |
| Regex injection: `Pattern`, `String.matches` and friends | the *pattern* comes from the input and is invalid or explosive | a fixed pattern on input text (ReDoS: `fuzz-coverage`, "Check the sinks") |

Some detectors only guide the fuzzer, towards XML external entities or
huge `BigDecimal` exponents. They report through another detector or as
a timeout.

- Disable a detector only with a recorded reason:
  `jazzer.disabled_hooks=<detector class>` in `junit-platform.properties`
  (`build.md`), or `--disabled_hooks=` in standalone runs.
- Scope the detectors at run time with `BugDetectors`
  (`com.code_intelligence.jazzer.api`):
  ```java
  try (SilentCloseable ignored = BugDetectors.allowNetworkConnections(
           (host, port) -> host.equals("localhost") && port == 8080)) {
    client.fetch(url);
  }
  ```
  `setFilePathTraversalTarget(() -> path)` changes the path the traversal
  detector looks for.
- An in-process fake must keep the oracle: a stub that stands in for the
  database and never rejects a query hides SQL injection.
- If a deserializer takes a class name from its input: Jazzer reports
  once the deserializer constructs or calls `jaz.Zer`. Give the fuzzer
  that name in the format's type syntax, as dictionary entries or
  `@ValuePool` values:
  - SnakeYAML: `!!jaz.Zer {}`
  - Fastjson: `{"@type":"jaz.Zer"}`
  - Jackson default typing: `["jaz.Zer",{}]`
  - `XMLDecoder`: `<object class="jaz.Zer"/>`

## Hooks

Add a hook only after reading the code, for a known reason: e.g. a random
or time-dependent value made constant, or a checksum check (`build.md`,
"Fuzzing-only changes"). A hook for one project method:

```java
// imports: com.code_intelligence.jazzer.api.{HookType, MethodHook}, java.lang.invoke.MethodHandle
public class HarnessHooks {
  // Clock.now() reads the system time, so an expiry check passes or fails depending on when an
  // input runs. It returns long, so the hook does too.
  @MethodHook(type = HookType.REPLACE, targetClassName = "com.example.Clock",
              targetMethod = "now", targetMethodDescriptor = "()J")
  public static long fixedTime(MethodHandle method, Object self, Object[] args, int hookId) {
    return 1_700_000_000_000L;  // a fixed instant
  }
}
```

- Register the hook class: `jazzer.custom_hooks=com.example.HarnessHooks`
  in `junit-platform.properties`, or `--custom_hooks=` in standalone runs.
- Hook types: `BEFORE` and `AFTER` inspect arguments and results;
  `REPLACE` skips the original call. `targetMethodDescriptor` picks an
  overload.
- **A `REPLACE` hook returns** `void` for a `void` method. For any other
  method, it returns the method's type, its wrapper or `Object`.
  - An `Object` hook on a `void` method can fail open: the calling class
    stays uninstrumented, and the real method runs. The log then shows
    `Error retransforming` and a `VerifyError`.
  - With `targetMethodDescriptor`, Jazzer refuses a mismatch at start.
- **Hooks act at call sites** in instrumented classes, not in the hooked
  method. A hook on a JDK method fires where project code calls it, not
  where JDK code does. For example, a hook on `ProcessBuilder.start()`
  doesn't fire when the call comes through `Runtime.exec`, and `start()`
  then really runs. Hook the project method that performs the effect, as
  above.
- A hook that lands in JDK classes through its own or a detector's
  `additionalClassesToHook` makes every call there throw
  `NoClassDefFoundError: <hook class>`. JDK classes can't load the hook
  class from the class path. Jazzer reports the error as a finding.
- Report from hooks with `Jazzer.reportFindingFromHook(...)`: the code
  under test may swallow a plain `throw`.
- A `REPLACE` hook on a function that a bug detector hooks silences that
  detector.
- Docs: https://github.com/CodeIntelligenceTesting/jazzer/blob/main/docs/advanced.md

## Recognizing other formats

Classic Jazzer targets, common in OSS-Fuzz, are classes with
`public static void fuzzerTestOneInput(byte[] data)` or
`fuzzerTestOneInput(FuzzedDataProvider data)`, and optionally
`fuzzerInitialize()`. Keep their format when extending them, and run
them with the standalone driver (`--target_class=<class>`).

Classic targets kept in `google/oss-fuzz` only measure existing fuzzing
(`fuzz-campaign`'s baseline). Compile copies of them from
`.seclab/fuzz/oss-fuzz/` with
`javac -cp "$CP" -d "$F/build/jazzer/oss-fuzz"`, and put that directory
in front of `$CP`.
