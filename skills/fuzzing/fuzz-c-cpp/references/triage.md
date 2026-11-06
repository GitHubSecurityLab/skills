# C/C++ triage

Every command below executes the target: run it through the launcher as a
self-ending run (`$L start --id repro-<h>-<n> --harness <h> --timeout <s>
--env ... -- <command>`), with the environment from `run.md` next to this
file. A reproduction that reproduces ends `exited <code>`; its log holds
the report.

## Reproduce

A finding of a libFuzzer harness, from libFuzzer or AFL++:

<!-- ci: c-repro -->
```sh
$L start --id repro-$H-<n> --harness $H --timeout <timeout_s> \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "$F/build/libfuzzer/<binary>" -rss_limit_mb=<memory_mb> -timeout=<input_timeout_s> "<finding>"
```

| Crash from | Command |
|---|---|
| libFuzzer (`runs/<id>/artifacts/copy-<i>/crash-*`, `timeout-*`, `oom-*`, `leak-*`) | the block above |
| AFL++ (`runs/<id>/out/<instance>/crashes/id:*`, `hangs/id:*`) | the block above: same harness, symbolized report |
| FuzzTest (reproducer in `runs/<id>/artifacts/copy-<i>/`) | the block below |
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
  its time to get the report. Then grow the relevant part of the input
  over several sizes (e.g. 1x, 2x, 4x, 8x) and compare run times: a linear
  parser also takes twice as long on twice the input, so growth faster
  than the size (time per byte rising) shows an algorithmic DoS, and so
  does a small input that already hits the timeout; one that never ends
  is a hang.
- If it's out of memory: the report gives the allocation stack
  (`malloc(<n>) exceeds limit`) or the RSS (`used: <n>Mb`). Check whether
  the size comes from the input.
- If it's a leak: leaks are on by default on Linux. On macOS, replay with
  `detect_leaks=1` in `ASAN_OPTIONS` and `-rss_limit_mb=0
  -malloc_limit_mb=<m>`; otherwise libFuzzer's own RSS thread shows up as a
  leak at exit.
- If it's a FuzzTest reproducer: replay it in the fuzzing build. On macOS,
  FuzzTest's `--rss_limit_mb` is `0` (`fuzztest.md`). To run it 100 times
  in one process: `FUZZTEST_REPLAY` set to a directory of 100 copies of the
  file.
  <!-- ci: fuzztest-repro -->
  ```sh
  $L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env FUZZTEST_REPLAY="<finding>" \
    --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
    "$F/build/fuzztest/<test_binary>" --gtest_filter=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
  ```

## Minimize

A libFuzzer harness. If it's a stack overflow: minimize it without
`dedup_token_length`; a stack overflow stops at once with it, since its
frames shift with the depth.

<!-- ci: c-minimize -->
```sh
mkdir -p "$F/crashes/<bucket>"
$L start --id min-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS:dedup_token_length=3" --env UBSAN_OPTIONS="$UBSAN_OPTIONS:dedup_token_length=3" -- \
  "$F/build/libfuzzer/<binary>" -minimize_crash=1 -runs=10000 -rss_limit_mb=<memory_mb> \
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
- If the harness is a FuzzTest: `FUZZTEST_MINIMIZE_REPRODUCER` exits at the
  first smaller input that crashes (any crash), so run it in a loop, each
  result the next input, until a step finds nothing smaller within
  `<timeout_s>` (20 to 60 s). Then replay and group the result as for
  every engine (`fuzz-triage`, "Minimize"):
  <!-- ci: fuzztest-minimize -->
  ```sh
  m="$F/crashes/<bucket>/minimized"; mkdir -p "$F/crashes/<bucket>"; cp "<finding>" "$m"
  for i in $(seq 1 30); do
    out="$F/runs/min-$H-<n>-$i/reproducers"; mkdir -p "$out"
    $L start --id min-$H-<n>-$i --harness $H --timeout <timeout_s> --env FUZZTEST_MINIMIZE_REPRODUCER="$m" \
      --env FUZZTEST_REPRODUCERS_OUT_DIR="$out" --env ASAN_OPTIONS="$ASAN_OPTIONS" \
      --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
      "$F/build/fuzztest/<test_binary>" --fuzz=<fuzz_test> \
      --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb> > /dev/null || :  # 134: smaller crash
    next=$(ls -S "$out" | tail -n 1)
    [ -n "$next" ] && [ "$(wc -c < "$out/$next")" -lt "$(wc -c < "$m")" ] || break
    cp "$out/$next" "$m"
  done
  ```

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
- **FuzzTest:** `=== BUG FOUND!`, the failing arguments, a regression test
  draft and the reproducer path; a signal's stack (an `abort()`, a failed
  `assert`) follows. Timeouts and memory limits print `[!] Per-input
  timeout exceeded` or `[!] RSS limit exceeded` before the banner; any stack
  after it is the watchdog's, not the input's.

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
