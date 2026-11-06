# FuzzTest

Docs: https://github.com/google/fuzztest/tree/main/doc. Checked by
building and running tag `2026-06-29`. `doc/flags-reference.md` is
incomplete: check `<test binary> --helpfull`.

## When

GoogleTest projects (Bazel or CMake) and projects with `FUZZ_TEST`s. CMake
builds run on Linux and macOS (Homebrew LLVM, Ninja); FuzzTest's Bazel
setup on macOS is experimental.

## Writing a fuzz test

```cpp
#include "fuzztest/fuzztest.h"
#include "gtest/gtest.h"

void ParseSerializeRoundTrips(const std::string& doc, int flags) {
  std::optional<Document> d = Parse(doc, flags);
  if (!d) return;
  std::optional<Document> again = Parse(Serialize(*d), flags);  // a property check
  ASSERT_TRUE(again.has_value());
  EXPECT_EQ(*again, *d);
}
FUZZ_TEST(ParserTest, ParseSerializeRoundTrips)
    .WithDomains(fuzztest::Arbitrary<std::string>(), fuzztest::InRange(0, 3))
    .WithSeeds({{"<a/>", 0}});
```

- The property function returns `void`, never exits, joins its threads and
  is deterministic. `EXPECT_*`/`ASSERT_*` failures are findings.
- Domains describe what the attacker controls
  (https://github.com/google/fuzztest/blob/main/doc/domains-reference.md):
  `Arbitrary<std::vector<uint8_t>>()` or `Arbitrary<std::string>()` for raw
  bytes, `InRange`, `ElementOf({...})`, `VectorOf(...).WithMaxSize(n)`,
  `StructOf<T>(...)`, `InRegexp(...)`, `OneOf(...)`.
- `.WithDomains` comes before `.WithSeeds`.
- Seeds: literal tuples, a seed-provider lambda (needed when seeds depend
  on globals from other files), or files:
  `.WithSeeds(fuzztest::ReadFilesFromDirectory(dir))`, which fits fuzz
  tests with one string parameter.
- Dictionary on a string or container domain:
  `fuzztest::Arbitrary<std::string>().WithDictionary(fuzztest::ReadDictionaryFromFile(path))`
  (libFuzzer dictionary format).
- Expensive setup: `FUZZ_TEST_F` with a fixture class; wrap an existing
  GoogleTest fixture in `fuzztest::PerIterationFixtureAdapter<T>` (safe) or
  `PerFuzzTestFixtureAdapter<T>` (faster; reset any state it mutates).
- `Map`, `FlatMap`, `ConstructorOf` domains take no seeds; `ReversibleMap`
  does.

## Build

CMake, in the project's style. The flags macro goes in the
**top-level** `CMakeLists.txt`, right after FuzzTest is made available and
before any of the project's own `add_subdirectory()` or `add_library()`:

```cmake
include(FetchContent)
FetchContent_Declare(fuzztest GIT_REPOSITORY https://github.com/google/fuzztest.git GIT_TAG 2026-06-29 GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(fuzztest)
fuzztest_setup_fuzzing_flags()   # no-op unless fuzzing mode is on
enable_testing()
include(GoogleTest)
# ... the project's own add_subdirectory()/add_library() come after this
add_executable(parser_fuzz_test parser_fuzz_test.cc)
target_link_libraries(parser_fuzz_test PRIVATE <project-library>)
link_fuzztest(parser_fuzz_test)
gtest_discover_tests(parser_fuzz_test)
```

The macro sets flags only for its directory and those below: a library in a
sibling directory, or defined before it, stays uninstrumented. If the root
can't be edited, pass the flags as cache variables. Check the library's
objects either way (`build.md`).

Two variants, each in its own build directory (`<test_target>`: the fuzz
test's CMake target), with `clang` and `clang++` from the LLVM directory
on `PATH` (`run.md`). The empty Release flags matter in both: FuzzTest's
dependency `antlr_cpp` (and many projects) sets `CMAKE_BUILD_TYPE=Release`
when it's empty, whose `-O3 -DNDEBUG` would compile the assertions out.
- **Clang 20 or later:** with clang 18 the link
  fails on `__sancov_cntrs` in a discarded section, and clang 19 with UBSan
  on `__muloti4`. Ubuntu 24.04: `clang-20 lld-20 libclang-rt-20-dev
  llvm-20`, with `/usr/lib/llvm-20/bin` as the LLVM directory.

`fuzztest`: fuzzing mode, which adds ASan and coverage instrumentation
(Clang only):
<!-- ci: fuzztest-build -->
```sh
CC=clang CXX=clang++ cmake -S . -B "$F/build/fuzztest" -DFUZZTEST_FUZZING_MODE=on \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_C_FLAGS_RELWITHDEBINFO="-O1 -g" -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="-O1 -g" \
  -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= \
  -DCMAKE_C_FLAGS="-fsanitize=$UB -fno-sanitize-recover=$UB -fno-sanitize=function" \
  -DCMAKE_CXX_FLAGS="-fsanitize=$UB -fno-sanitize-recover=$UB $HARDEN" \
  -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=$UB" && cmake --build "$F/build/fuzztest" --target <test_target>
```

`coverage`: unit-test mode (no `FUZZTEST_FUZZING_MODE`):
<!-- ci: fuzztest-coverage-build -->
```sh
CC=clang CXX=clang++ cmake -S . -B "$F/build/coverage" -DCMAKE_BUILD_TYPE= \
  -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= \
  -DCMAKE_C_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION" \
  -DCMAKE_CXX_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION" \
  -DCMAKE_EXE_LINKER_FLAGS="-fprofile-instr-generate" && cmake --build "$F/build/coverage" --target <test_target>
```

- **UBSan** comes from the cache flags, with `$UB` (`build.md`); the macro
  adds only ASan.
- **`-O1 -g` explicitly.** Upstream's `RelWithDebug` is an unknown build type
  (no flags: `-O0`, no `-g`); plain `RelWithDebInfo` adds `-DNDEBUG`.

Bazel (untested in CI): `bazel_dep(name = "fuzztest", version = "20260629.0")`, generate the
configs with `bazel run @fuzztest//bazel:setup_configs > fuzztest.bazelrc`
(imported from `.bazelrc`), build with `bazel build --config=fuzztest
//<package>:<test>`, and run the binary from `bazel-bin/` through the
launcher.
Docs: `doc/quickstart-cmake.md`, `doc/quickstart-bazel.md`.

## Run

A develop run (`L`, `F`, `H`, the environment: `run.md`; `<fuzz_test>`:
`<Suite.Test>`):

<!-- ci: fuzztest-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" \
  --env FUZZTEST_TESTSUITE_IN_DIR="$F/corpus/$H" --env FUZZTEST_TESTSUITE_OUT_DIR="$F/corpus/$H" \
  --env FUZZTEST_REPRODUCERS_OUT_DIR="{run}/artifacts/copy-{copy}" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "$F/build/fuzztest/<test_binary>" --fuzz=<fuzz_test> --fuzz_for=<run_s>s \
  --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
```

- **macOS: `--rss_limit_mb=0`** here and in every FuzzTest replay. The tag
  `2026-06-29` reads macOS's peak RSS, which is in bytes, as KiB, so any
  limit aborts at the first input (fixed upstream after that tag).

- **One flat corpus directory** per fuzz test, as both `IN_DIR` (read at the
  start) and `OUT_DIR` (new inputs). Inputs read from `IN_DIR` aren't written
  again, so it grows only by new inputs.
- **No `--corpus_database`:** CMake builds read it but never write it.
- `--list_fuzz_tests` lists the fuzz tests. Status: `[*] Corpus size: <n>
  | Edges covered: <n> | ...`. Durations: `30s`, `5m`, `2h`, `inf`.
- It stops at the first finding; `--continue_after_crash` only works with
  Bazel's Centipede engine.
- **Minimizing a corpus, and merging a long run's** (undocumented
  upstream; re-check after an upgrade): minimize the union of the
  persistent corpus and the run's, on the current build. It stops by
  itself, and its result replaces the persistent
  corpus (`fuzz-campaign`):
  <!-- ci: fuzztest-merge -->
  ```sh
  U="$F/runs/merge-$H-<n>.union"; mkdir -p "$U"
  cp -R "$F/corpus/$H/." "$F/runs/<long_run>/corpus/." "$U/"   # named by content: same name, same input
  $L start --id merge-$H-<n> --harness $H --timeout <timeout_s> --env FUZZTEST_MINIMIZE_TESTSUITE_DIR="$U" \
    --env FUZZTEST_TESTSUITE_OUT_DIR="{run}/corpus" --env FUZZTEST_REPRODUCERS_OUT_DIR="{run}/artifacts/copy-{copy}" \
    --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
    "$F/build/fuzztest/<test_binary>" --fuzz=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
  ```
  It keeps FuzzTest's edges, not every source branch: a branch compiled
  without an edge of its own (a select instruction) can lose its only
  input, so llvm-cov's branch count may drop a little after a merge (2 of
  100 branches on a test). That's accepted.

## Coverage

Replay the flat corpus directory on the `coverage` variant (Build, above):

<!-- ci: fuzztest-cov-replay -->
```sh
$L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
  --env LLVM_PROFILE_FILE="{run}/profraw/%p.profraw" --env FUZZTEST_REPLAY="$F/corpus/$H" \
  --env FUZZTEST_REPRODUCERS_OUT_DIR="{run}/artifacts/copy-{copy}" -- \
  "$F/build/coverage/<test_binary>" --gtest_filter=<fuzz_test> \
  --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
```

- On macOS, `--rss_limit_mb=0` (Run, above).
- It prints one `[.] Replaying ...` line per input. Check exit status 0
  and that `profraw/*.profraw` exists before exporting.
- If **it aborts** (exit 134, no profile) on a crashing input: replay one
  file per process, so every other input counts (the log names the ones
  that crashed; until the bug is fixed, the snapshot is partial):
  <!-- ci: fuzztest-cov-replay-files -->
  ```sh
  $L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
    --env LLVM_PROFILE_FILE="{run}/profraw/%p.profraw" -- sh -c '
      b=$1; dir=$2; shift 2
      for f in "$dir"/*; do FUZZTEST_REPLAY=$f "$b" "$@" > /dev/null 2>&1 || echo "crashed: $f"; done' \
    sh "$F/build/coverage/<test_binary>" "$F/corpus/$H" --gtest_filter=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
  ```
- If the corpus directory is missing or empty: it aborts too; run without
  `FUZZTEST_REPLAY` and with `--env FUZZTEST_FUZZ_FOR=0s` (the
  `.WithSeeds` run, and no random inputs after them).

**Exact subset:** `FUZZTEST_REPLAY={dir}` (at least one input), e.g. for
`reach.py`:
<!-- ci: fuzztest-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --env "LLVM_PROFILE_FILE={out}/%p.profraw" --env "FUZZTEST_REPLAY={dir}" \
  --timeout <timeout_s> --replay "$F/build/coverage/<test_binary> --gtest_filter=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>" \
  --export "sh -c 'llvm-profdata merge -sparse \$0/*.profraw -o \$0/p.profdata && llvm-cov export -format=lcov -instr-profile=\$0/p.profdata \$1 > \$2' {out} $F/build/coverage/<test_binary> {lcov}"
```

**The gate's state-leak check** (`fuzz-harness`'s gate.md): the sample
replayed as one directory in one process, then one file per process,
compared without the fetched dependencies (`_deps/`): FuzzTest's own code
replays a directory and a file differently.
<!-- ci: fuzztest-leak-check -->
```sh
G="$F/runs/gate-$H-<n>"; S="$G/sample"; B="$F/build/coverage/<test_binary>"; mkdir -p "$S" "$F/coverage/$H"
find "$F/corpus/$H" -type f -size +0c | sort -R | awk 'NR <= <sample_size>' | while IFS= read -r f; do i=$((i+1)); cp "$f" "$S/$i-${f##*/}"; done
for side in one fresh; do
  $L start --id "gate-$H-<n>-$side" --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
    --env LLVM_PROFILE_FILE="{run}/profraw/%p.profraw" -- sh -c '
      b=$1; side=$2; dir=$3; o="--gtest_filter=<fuzz_test> --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>"
      if [ "$side" = one ]; then FUZZTEST_REPLAY=$dir exec "$b" $o; fi
      for f in "$dir"/*; do FUZZTEST_REPLAY=$f "$b" $o || exit; done' sh "$B" "$side" "$S" > /dev/null
  llvm-profdata merge -sparse "$F/runs/gate-$H-<n>-$side"/profraw/*.profraw -o "$G/$side.profdata"
  llvm-cov export -format=lcov -instr-profile="$G/$side.profdata" -ignore-filename-regex='/_deps/' "$B" \
    > "$F/coverage/$H/gate-$side.lcov"
done
python3 "$F/bin/coverage.py" overlap "$F/coverage/$H/gate-one.lcov" "$F/coverage/$H/gate-fresh.lcov" --counts \
  --processes $(ls "$S" | wc -l) --root .
```

Export as in `coverage.md`, "Export", with `<test_binary>`.

## Long runs, crashes, OSS-Fuzz

- **Long runs:** N independent copies of the command (`--copies`), with
  `FUZZTEST_TESTSUITE_OUT_DIR={run}/corpus` and the persistent corpus as
  `IN_DIR`. Copies don't share inputs while they run (no reload; files are
  named by content, so one directory is safe). With no duration, leave out
  `--fuzz_for`. (`--jobs=<n>` needs Bazel's Centipede.) At analysis, merge
  by minimizing the union of the persistent corpus and the run's (Run, above).
- **Crashes** print the failing arguments (`argument 0: ...`); reproducers
  use FuzzTest's own serialization. More: `triage.md`, `regression.md`.
- **OSS-Fuzz** builds FuzzTest harnesses with Bazel. A CMake project can
  follow libavif, which builds them in libFuzzer compatibility mode with a
  pinned FuzzTest and a few patches (`tests/oss-fuzz/build.sh`); the
  hand-off says so and leaves the choice to the maintainers. The campaign
  itself uses FuzzTest's standard mode only.

## Pitfalls

- Domains or seeds initialized from globals of other files (static
  initialization order); use a seed provider.
- Fixtures whose state leaks between iterations: nondeterministic findings.
- GCC: fine for unit-test mode, rejected for fuzzing mode.
