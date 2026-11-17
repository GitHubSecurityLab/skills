# C/C++ triage

Every command below executes the target: run it through the launcher as a
self-ending run (`$L start --id repro-<h>-<n> --harness <h> --timeout <s>
--env ... -- <command>`), with the environment from `run.md` next to this
file. A reproduction that reproduces ends `exited <code>`; its log holds
the report. `<build>` is `$F/build`; for a long run's finding (analysis,
phase 1), the run's `<tree_build>` (`run.md`, "Long runs").

## Reproduce

A finding of a libFuzzer harness, from libFuzzer or AFL++:

<!-- ci: c-repro -->
```sh
$L start --id repro-$H-<n> --harness $H --timeout <timeout_s> \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "<build>/libfuzzer/<binary>" -rss_limit_mb=<memory_mb> -timeout=<input_timeout_s> "<finding>"
```

| Crash from | Command |
|---|---|
| libFuzzer (`runs/<id>/artifacts/copy-<i>/crash-*`, `timeout-*`, `oom-*`, `leak-*`) | the block above |
| AFL++ (`runs/<id>/out/<instance>/crashes/id:*`, `hangs/id:*`) | the block above: same harness, symbolized report |
| FuzzTest (reproducer in `runs/<id>/artifacts/copy-<i>/`) | `fuzztest.md`, "Triage" |
| OSS-Fuzz or CI testcase | OSS-Fuzz's `helper.py reproduce` first (`fuzz-triage`'s oss-fuzz.md); build the matching sanitizer (`fuzz-harness`'s build.md), then as above |

- A file argument runs the input once without fuzzing; `-runs=100` before
  the file runs it 100 times in one process (`fuzz-triage`, "Reproduce").
- Symbolized frames need `llvm-symbolizer` on `PATH` and `-g` builds. For
  complete allocation stacks, add `fast_unwind_on_malloc=0` to
  `ASAN_OPTIONS` (slower).
- If an AFL++ crash doesn't reproduce on the libFuzzer build: try the
  `aflpp` binary standalone (with `symbolize=1`): the builds differ in
  instrumentation and compiler passes.
- If it's a timeout: replay with the same `-timeout`. One that finishes
  near it (`Executed <file> in <n> ms`) is still slow, since libFuzzer
  checks only every `-timeout`/2 + 1 s: replay it with `-timeout` at half
  its time to get the report. Then run the growth test (`fuzz-triage`,
  "Find the root cause").
- If it's out of memory: the report gives the allocation stack
  (`malloc(<n>) exceeds limit`) or the RSS (`used: <n>Mb`). Check whether
  the size comes from the input.
- If it's a leak: leaks are on by default on Linux. On macOS, replay with
  `detect_leaks=1` in `ASAN_OPTIONS` and `-rss_limit_mb=0
  -malloc_limit_mb=<m>`; otherwise libFuzzer's own RSS thread shows up as a
  leak at exit.
## Minimize

A libFuzzer harness. If it's a stack overflow: minimize it without
`dedup_token_length`; a stack overflow stops at once with it, since its
frames shift with the depth.

<!-- ci: c-minimize -->
```sh
mkdir -p "$F/crashes/<bucket>"
$L start --id min-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS:dedup_token_length=3" --env UBSAN_OPTIONS="$UBSAN_OPTIONS:dedup_token_length=3" -- \
  "<build>/libfuzzer/<binary>" -minimize_crash=1 -runs=10000 -rss_limit_mb=<memory_mb> \
  -timeout=<input_timeout_s> -exact_artifact_path="$F/crashes/<bucket>/minimized" "<finding>"
```

- libFuzzer prints `CRASH_MIN: failed to minimize beyond ... (<n> bytes)`
  when done; the exit status is 0. `dedup_token_length=3` makes it stop
  where the stack changes (`CRASH_MIN: mismatch in dedup tokens`), instead
  of minimizing to any crash, which can slip to another bucket. The memory
  and time limits are the finding's, so an OOM or a timeout stays one.
- Replay the minimized input and compare its signature with
  `crash_buckets.py` before keeping it.
- Look at the bytes: `xxd -g1 <file> | head -n 20`.
- If the harness is a FuzzTest: `fuzztest.md`, "Triage".

## Reading reports

- **ASan:** `ERROR: AddressSanitizer: <type>`, then `READ`/`WRITE of size
  <n>`, the access stack, and for heap bugs the allocation (and free)
  stacks. `SCARINESS: <score> (<description>)` is a rough hint for the
  report's severity, not a verdict.
- **UBSan:** `<file>:<line>: runtime error: <description>`, then
  `SUMMARY: UndefinedBehaviorSanitizer`.
- **libFuzzer:** `deadly signal` (a crash without a sanitizer report, e.g.
  `abort()`, a trap, or an uncaught C++ exception, whose type the line
  before names), `timeout after <n> seconds`, `out-of-memory`.
- **FuzzTest:** `fuzztest.md`, "Triage".

`crash_buckets.py` reads all of these.

Reports that change the verdict:

- **C++ standard-library checks** (`$HARDEN`): an abort from
  `Assertion '__n < this->size()' failed` (libstdc++) or `vector[] index
  out of bounds` (libc++) is an out-of-bounds access the check stopped:
  classify it as the read or write it would have been, not as an
  `assertion_failure`.
- **MSan** (`msan` build): memory that code built without MSan wrote (a
  dependency, the C++ standard library) reads as uninitialized, so such a
  report is false. Assign a verdict only once it reproduces on a build
  with everything instrumented.
- **UBSan integer checks** (`ubsan-int` build): start in State `needs
  investigation`; unsigned wrap-around is often intended. Look for
  what the wrapped value is used for (a size, an index).

## Debugging

Run the input under a debugger, through the launcher:
`lldb --batch -o run -o bt -- <binary> <file>` on macOS,
`gdb -batch -ex run -ex bt --args <binary> <file>` on Linux.
- If the sandbox blocks `ptrace` (Codex's default sandbox): debuggers
  can't attach; debug in a terminal outside the sandbox.
- If it's an MSan report: rebuild with `-fsanitize-memory-track-origins=2`
  to see where the uninitialized value came from.

## Fuzzing-only patches

Check whether the crash's path runs fuzzing-only code: every fuzz build
defines `FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`, which turns on our
patches and the project's own fuzzing-only code (search the crash's files
for the macro). If it does (skipped checksums or signatures): fix the input
up (recompute the checksum, e.g. in the generator) and reproduce on a build
without the macro, such as the regression-test build.
