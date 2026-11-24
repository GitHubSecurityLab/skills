# C/C++ regression tests

## Seed replay: the harness as a test (upstreaming, `fuzz-campaign`)

A small driver lets the project's CI run a `LLVMFuzzerTestOneInput`
harness without a fuzzing engine, with any compiler. Many projects already
have one (look for a `main` next to the harnesses). Otherwise add this, in
the project's style:

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

If the harness defines `LLVMFuzzerInitialize`, make the driver call it
first. Register the test, e.g. with CMake:

```cmake
add_executable(parse_fuzzer_replay parse_fuzzer.c replay_main.c)
target_link_libraries(parse_fuzzer_replay PRIVATE <project-library>)
file(GLOB PARSE_FUZZER_INPUTS ${CMAKE_CURRENT_SOURCE_DIR}/regression/parse_fuzzer/*)
add_test(NAME parse_fuzzer_regression COMMAND parse_fuzzer_replay ${PARSE_FUZZER_INPUTS})
```

In a libFuzzer build, the fuzz binary itself replays files the same way
(`<binary> <files>`), which suits a sanitizer CI job.

## The bug's reproducer (`fuzz-triage`)

Every real bug gets one, written in triage on the build that found it: a
small program in `crashes/<bucket>/repro.c` (or `.cc`). Its `main()`
calls the project's API with the concrete values the harness derived from
the input, not through the harness and not as a crash file. To get the
values, print them once from the harness (triage.md, "Debugging").
FuzzTest prints them itself (`fuzztest.md`, "Triage"). `xxd -i <file>`
prints a byte buffer as a C array.

- **It includes the check that caught the bug,** so it fails before the
  fix: the sanitizer build, the harness's property check written out (e.g.
  `assert(decoded == original)`), or the time or memory limit. A plain
  call with the same values often passes.
- **It runs against a tree's existing build output,** without rebuilding
  that tree, since another run may use it. Build the reproducer into
  scratch, as the block below does:
  - Link the tree's libFuzzer ASan+UBSan library; in a FuzzTest project,
    the `fuzztest` build's. libFuzzer itself isn't needed.
  - `<repro_leaks>`: `1`, so a leak fails on macOS too (`run.md` turns
    leak detection off there only for libFuzzer). Use `0` in a sandbox
    that blocks `ptrace`.
  - If the reproducer is C++: use `clang++`, without
    `-fno-sanitize=function`. `$HARDEN` keeps the standard library's
    checks; C ignores it.
  - After the tree's library, add the project's other libraries in link
    order, a shared one with `-Wl,-rpath,<its directory>` as the block
    does for the tree's library. Then add system libraries such as
    `-lz`.

<!-- ci: c-reproducer -->
```sh
$L start --id poc-$H-<n> --harness $H --timeout <timeout_s> \
  --env ASAN_OPTIONS="$ASAN_OPTIONS:max_allocation_size_mb=<memory_mb>:allocator_may_return_null=0:detect_leaks=<repro_leaks>" \
  --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  sh -c 'clang -g -O1 -fsanitize=address,$4 -fno-sanitize-recover=$4 $5 -fno-sanitize=function -I"$2" "$1" "$3" -Wl,-rpath,"${3%/*}" -o "$0/repro" && exec "$0/repro"' \
  "{scratch}" "$F/crashes/<bucket>/repro.c" "<include_dir>" "<tree_build>/libfuzzer/<library>" "$UB" "$HARDEN"
```

An allocation above `<memory_mb>` aborts, as in the fuzzer, instead of
returning NULL to code that checks for it. The state `exited <code>` with
the bug's report means the bug reproduced; `finished` means the
reproducer passes.

The fix (`fuzz-triage`) turns the reproducer into a test of the project's
framework, such as GoogleTest, Check, cmocka or a CTest program. The test
goes in the fix commit, with a comment that names the bug class and the
function, and says that memory bugs fail reliably only in a sanitizer
build.

## Sanitizer build for the tests

Use the project's existing option first:
- a CMake option such as `ENABLE_ASAN` or `<PROJECT>_SANITIZE`;
- Meson's `-Db_sanitize=address,undefined`;
- Bazel `--config=asan`;
- a configure flag such as `--enable-asan`;
- a CI job that already builds with sanitizers.

If there's none, build with `-fsanitize=address,$UB
-fno-sanitize-recover=$UB -g -O1`, with `$UB` and `$HARDEN` written out
(`build.md`). C takes these flags in `CFLAGS` and adds
`-fno-sanitize=function`; C++ takes them in `CXXFLAGS` and adds
`$HARDEN`. Use them in two places:
- The regression test in the project: give the commands (the project's
  normal build, with these flags) in the test's comment or the PR text.
  Don't add an option the maintainers didn't ask for.
- The report's reproducer package (`fuzz-triage`): ship a short build
  script with it. The script builds the library and the test with these
  flags, and runs the test.

Run the test before the fix (it must fail with the sanitizer report)
and after the fix (it must pass). `-fno-sanitize-recover=$UB` makes a
UBSan report fail the test. By default UBSan prints the report and exits
0, so ctest, CI and a bisect would pass.
