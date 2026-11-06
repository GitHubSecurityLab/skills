# C/C++ regression tests

## Seed replay: the harness as a test (hand-off, `fuzz-campaign`)

A small driver lets the project's CI run a `LLVMFuzzerTestOneInput` harness
without a fuzzing engine, with any compiler. Many projects already have one
(look for a `main` next to the harnesses). Otherwise add this, in the
project's style:

```c
/* Runs LLVMFuzzerTestOneInput once on each file named on the command line,
   so the harness can run as a test without a fuzzing engine. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int main(int argc, char **argv) {
  for (int i = 1; i < argc; i++) {
    FILE *f = fopen(argv[i], "rb");
    if (!f || fseek(f, 0, SEEK_END) != 0) { perror(argv[i]); return 1; }
    long n = ftell(f);
    rewind(f);
    uint8_t *buf = malloc(n > 0 ? (size_t)n : 1);  /* exact size: ASan sees over-reads */
    if (n < 0 || !buf || fread(buf, 1, (size_t)n, f) != (size_t)n) { perror(argv[i]); return 1; }
    fclose(f);
    LLVMFuzzerTestOneInput(buf, (size_t)n);
    free(buf);
  }
  return 0;
}
```

If the harness defines `LLVMFuzzerInitialize`, call it first. Register the
test, e.g. with CMake:

```cmake
add_executable(parse_fuzzer_replay parse_fuzzer.c replay_main.c)
target_link_libraries(parse_fuzzer_replay PRIVATE <project-library>)
file(GLOB PARSE_FUZZER_INPUTS ${CMAKE_CURRENT_SOURCE_DIR}/regression/parse_fuzzer/*)
add_test(NAME parse_fuzzer_regression COMMAND parse_fuzzer_replay ${PARSE_FUZZER_INPUTS})
```

In a libFuzzer build, the fuzz binary itself replays files the same way
(`<fuzzer> <files>`), which suits a sanitizer CI job.

## The bug's reproducer (`fuzz-triage`)

Every real bug gets one, written in triage on the build that found it: a
small program in `crashes/<bucket>/repro.c` (or `.cc`) whose `main()` calls
the project's API with the concrete values the harness derived from the
input, not through the harness and not as a crash file. Print the values
once from the harness, or read them in a debugger. `xxd -i <file>` prints
a byte buffer as a C array.

- **It includes the check that caught the bug,** so it fails before the
  fix: the sanitizer build, the harness's property check written out (e.g.
  `assert(decoded == original)`), or the time or memory limit. A plain
  call with the same values often passes.
- **It runs against a tree's existing build output,** without rebuilding
  that tree (another run may use it): link the tree's libFuzzer ASan+UBSan
  library (a FuzzTest project: the `fuzztest` build's); libFuzzer itself
  isn't needed. `<repro_leaks>`: `1`, so a leak fails on macOS too
  (`run.md` turns leak detection off there only for libFuzzer); `0` in a
  sandbox that blocks `ptrace`. If the reproducer is C++: `clang++`,
  without `-fno-sanitize=function` (`$HARDEN` keeps the standard library's
  checks; C ignores it). Build into scratch, with the library's other link
  dependencies added after it:

<!-- ci: c-reproducer -->
```sh
$L start --id poc-$H-<n> --harness $H --timeout <timeout_s> \
  --env ASAN_OPTIONS="$ASAN_OPTIONS:max_allocation_size_mb=<memory_mb>:allocator_may_return_null=0:detect_leaks=<repro_leaks>" \
  --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  sh -c 'clang -g -O1 -fsanitize=address,$4 -fno-sanitize-recover=$4 $5 -fno-sanitize=function -I"$2" "$1" "$3" -o "$0/repro" && exec "$0/repro"' \
  "{scratch}" "$F/crashes/<bucket>/repro.c" "<include_dir>" "<tree_build>/libfuzzer/<library>" "$UB" "$HARDEN"
```

An allocation above `<memory_mb>` aborts, as in the fuzzer, instead of
returning NULL to code that checks for it. The state `exited <code>` with
the bug's report means it reproduced; `finished` means it passes. The fix
(`fuzz-triage`) turns it into a test of the project's framework
(GoogleTest, Check, cmocka, a CTest program) in the fix commit, with a
comment naming the bug class and the function, and saying that memory bugs
fail reliably only in a sanitizer build.

## Sanitizer build for the tests

Use the project's existing option first: a CMake option such as
`ENABLE_ASAN` or `<PROJECT>_SANITIZE`, Meson's
`-Db_sanitize=address,undefined`, Bazel `--config=asan`, a configure flag
such as `--enable-asan`, or a CI job that already builds with sanitizers.
If there's none:
- for the regression test in the project, give the commands in the test's
  comment or the PR text (e.g. `CFLAGS="-fsanitize=address,$UB
  -fno-sanitize-recover=$UB -fno-sanitize=function -g"` with the
  project's normal build, C++ adding `$HARDEN`, both written out,
  `build.md`),
  rather than adding an option the
  maintainers didn't ask for;
- for the report's reproducer package (`fuzz-triage`), ship a short build
  script with it that builds the library and the test with
  `-fsanitize=address,$UB -fno-sanitize-recover=$UB -g -O1` (C: and
  `-fno-sanitize=function`; C++: and `$HARDEN`), both written out, and
  runs it.

Run the test before the fix (it must fail with the sanitizer report) and
after it (it must pass). `-fno-sanitize-recover=$UB` makes a UBSan
report fail the test: by default UBSan prints it and exits 0, so ctest,
CI and a bisect would pass.

## FuzzTest

- Each crash prints `=== Regression test draft`: a `TEST` calling the
  property function with the failing arguments (none for `FUZZ_TEST_F`).
  The property function is our harness, so take only the argument values
  from it, for the reproducer's direct API call.
- Seeds in `.WithSeeds({...})` run in every mode, including the short
  unit-test run in normal CI: a harness check, not a bug's test.

## Keep it private until disclosure

A held-back bug's regression test (`fuzz-campaign`, safety rules) reveals
it. It stays in the `fuzz(fix)` commit, which is never pushed, until the
bug is public and fixed upstream.
