# Java triage

Every command below runs project code. Run it through the launcher
(`repro-<h>-<n>`), with `L`, `F`, `H` and the environment as in `run.md`
next to this file. For a long run's finding, set `CP` from the classpath
file of the run's worktree (`run.md`, "Long runs").

## Reproduce

Reproduce with the standalone driver, with the crash file as the only
positional argument:

<!-- ci: java-repro -->
```sh
$L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  -timeout=<input_timeout_s> -rss_limit_mb=0 -artifact_prefix="{run}/artifacts/copy-{copy}/" "<finding>"
```

- The file runs once. The state `exited 77` (70 for a timeout) means the
  finding reproduced. The report is in `runs/repro-<h>-<n>/copy-0/log`.
- If the fuzz test has typed parameters: its crash files replay only
  against the same parameter types, a record's or bean's fields
  included. Replay on the commit and classpath that the run used.
- If the finding is flaky: `-runs=100` before the file runs it 100 times
  in one JVM. Check `@BeforeAll` state, static caches, and threads.

## Reading findings

- **Standalone:** `== Java Exception: <class>: <message>`, then the
  stack, then `DEDUP_TOKEN: <16 hex digits>`. The same token means the
  same stack.
- **JUnit** (the project's CI):
  `FuzzTestFindingException: <class>: <message>`. The real stack follows
  `Caused by: <class>`. With Gradle, the stack is in
  `build/test-results/test/TEST-<Class>.xml`. Reproduce the crash file
  standalone (above).
- **Detector findings** are `FuzzerSecurityIssueLow|Medium|High|Critical`,
  with a title such as `OS Command Injection`, `Remote Code Execution`,
  `Server Side Request Forgery (SSRF)` or `File path traversal`. The
  first project frame after the detector's own frames is the sink call.
- **Any other exception thrown in Jazzer's frames**, with
  `com.code_intelligence.jazzer.sanitizers.*` on top, comes from a
  detector, not the project. Read the detector's source to learn what it
  saw. E.g. 0.30.0's SQL detector throws `ArrayIndexOutOfBoundsException`
  when a `PreparedStatement`'s `execute*()` fails with a syntax error.
  That's a likely injection.
- **Timeouts** print libFuzzer's timeout report with the thread stacks. An
  input that finishes near the limit (`Executed … in <n> ms`) is still slow:
  libFuzzer checks for timeouts only every `-timeout`/2 + 1 s. Replay such an
  input with `-timeout` at half its run time to get the report. Then run the
  growth test (`fuzz-triage`, "Find the root cause").
- **`OutOfMemoryError`** under `-Xmx<memory_mb>m`: check whether an
  allocation size comes from the input.
- **`Stack overflow (use '-Xss<n>k' to reproduce)`** is a
  `FuzzerSecurityIssueLow`: recursion the input drives too deep, through
  its nesting, its length (e.g. `(a|b)*` in `java.util.regex` on a long
  string) or a cycle. It's a DoS when the input is small.
  - Every command that runs the finding (replays, `-runs=100`,
    minimizing) needs that `-Xss`: after `env.sh`, set
    `JOPTS="$JOPTS -Xss<n>k"`. Without it, an input that only just
    overflows often doesn't overflow again in a fresh JVM.
  - The reproducer (`regression.md`) builds its input in code, with ten
    times the nesting or length that drives the recursion, so it
    overflows at the default stack size: the fix moves it into the
    project's tests, which run without `-Xss`. Without Jazzer's
    instrumentation, the finding's own input may not overflow, even at
    that `-Xss`. For a cycle, use the finding's input as it is: it
    recurses without end.
- **Other exceptions** are `unexpected_exception` unless the API documents
  them for invalid input (then the harness should catch them:
  `harness_bug`).

`crash_buckets.py` groups the standalone reports as `DEDUP_TOKEN` does:
by the deepest `Caused by:`, or by a detector's `FuzzerSecurityIssue*` if
the chain has one. So a library that wraps every failure in one
exception doesn't merge distinct bugs. When findings reported from hooks
should be grouped by the project frames below them, add
`--ignore 'HarnessHooks'` to the `crash_buckets.py` command. Use your
hook class's name in place of `HarnessHooks` if it differs.

**Skipping a blocking finding** while its fix is queued (`fuzz-triage`,
"Blocking bugs"): develop runs pass the finding's `DEDUP_TOKEN` to
Jazzer's `--ignore=<token>`, and Jazzer skips that stack. Separate
several tokens with commas. The token covers the stack's line numbers,
so a variant on another path, or an edit to a file on the stack, gets a
new token. Record the token in the target file, and drop it with the
fix.

## Minimize

Minimize into `crashes/<bucket>/minimized`, with `<minimize_s>` at least
30 s below `<timeout_s>`. Jazzer must stop on its own: a JVM that the
launcher stops writes its input in flight to `-exact_artifact_path`. That
overwrites `minimized` with an input that may not crash.

<!-- ci: java-minimize -->
```sh
mkdir -p "$F/crashes/<bucket>"
$L start --id min-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" \
  com.code_intelligence.jazzer.Jazzer --target_class=<fuzz_class> --target_method=<fuzz_method> \
  -timeout=<input_timeout_s> -rss_limit_mb=0 -minimize_crash=1 -runs=10000 -max_total_time=<minimize_s> \
  -exact_artifact_path="$F/crashes/<bucket>/minimized" "<finding>"
```

When done, Jazzer prints
`CRASH_MIN: failed to minimize beyond ... (<n> bytes)`. Replay
`minimized` either way (`fuzz-triage`, "Minimize").

## Typed parameters: the values of an input

Typed-parameter inputs are in Jazzer's own encoding, and the replay
doesn't print their values. Decode them with the helper below. It reads
a crash file, or `minimized`, with Jazzer's own decoder:
`ArgumentsMutator`, an internal API, checked with 0.30.0. The helper
doesn't call the fuzz test or touch the tree, so it works on a long
run's worktree too. Decoding runs project code (constructors of records
and beans, `toString`), so the helper runs through the launcher like a
replay. It prints one argument per line into the run's log, with
characters outside printable ASCII as `\u{<hex>}`:

<!-- ci: java-decode -->
```sh
cat > "$F/build/jazzer/Decode.java" <<'EOF'
import com.code_intelligence.jazzer.mutation.ArgumentsMutator;
import java.io.ByteArrayInputStream;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

class Decode {
  public static void main(String[] a) throws Exception {
    Class<?> test = Class.forName(a[0], false, Decode.class.getClassLoader());  // no static initializers
    for (Method m : test.getDeclaredMethods()) {
      if (!m.getName().equals(a[1])) continue;
      ArgumentsMutator arguments = ArgumentsMutator.forMethodOrThrow(m, false);
      arguments.read(new ByteArrayInputStream(Files.readAllBytes(Path.of(a[2]))));
      for (Object value : arguments.getArguments()) {
        String text = Arrays.deepToString(new Object[] {value});
        StringBuilder out = new StringBuilder();
        text.substring(1, text.length() - 1).codePoints().forEach(c ->
            out.append(c >= 0x20 && c < 0x7f ? Character.toString(c) : String.format("\\u{%x}", c)));
        System.out.println(out);
      }
    }
  }
}
EOF
$L start --id decode-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAVA_HOME="$JAVA_HOME" -- \
  "$JAVA_HOME/bin/java" $JOPTS -cp "$CP" "$F/build/jazzer/Decode.java" <fuzz_class> <fuzz_method> "<finding>"
```

## Detector findings: confirm the impact

- **Injection detectors** (command, SQL, LDAP, XPath, expression
  language): check that the input reaches the sink as syntax, not only as
  data, on a real entry point.
- **LDAP injection:** a finding that cites RFC 4515 is an invalid search
  filter. One that cites RFC 2253 is any other `NamingException`, a
  search base that doesn't exist or a lost connection included. Reproduce
  it (above) with
  `--disabled_hooks=com.code_intelligence.jazzer.sanitizers.LdapInjection`
  before the finding to see the real exception.
- **Deserialization and reflection** (`jaz.Zer`): show which class the
  attacker can name, and which gadgets the classpath offers. For the
  gadgets, ysoserial's payload list names the libraries. The honeypot
  proves control, not exploitability.
- **SSRF:** the detector reports any connection the harness didn't
  allow. Check that the host comes from attacker input.
- **Path traversal:** the default trigger path is `../jazzer-traversal`.
  A finding `Path is not allowed by the user-defined predicate` comes
  from the harness's allow predicate (`harness.md`); if the target opened
  that file on its own, fix the predicate. Check the path the
  application used against its base directory.

## Debugging

Print values from the fuzz test or the library temporarily
(`System.err.println`), recompile with the project's build (`build.md`
next to this file), and rerun the reproduction above. Remove the prints
before committing, and recompile again.
