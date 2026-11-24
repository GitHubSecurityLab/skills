# FuzzTest

Docs: https://github.com/google/fuzztest/tree/main/doc. Checked by
building and running tag `2026-06-29`. `doc/flags-reference.md` is
incomplete: check `<test_binary> --helpfull`.

## When

GoogleTest projects (Bazel or CMake) and projects with `FUZZ_TEST`s. CMake
builds run on Linux and macOS (Homebrew LLVM, Ninja). FuzzTest's Bazel
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
- Seeds come in three forms:
  - literal tuples;
  - a seed-provider lambda, needed when seeds depend on globals from
    other files;
  - files: `.WithSeeds(fuzztest::ReadFilesFromDirectory(dir))`, which
    fits fuzz tests with one string parameter.
- Dictionary on a string or container domain, in libFuzzer's dictionary
  format:
  `fuzztest::Arbitrary<std::string>().WithDictionary(fuzztest::ReadDictionaryFromFile(path))`.
- Expensive setup: `FUZZ_TEST_F` with a fixture class. To use an existing
  GoogleTest fixture, wrap it in one of two adapters:
  - `fuzztest::PerIterationFixtureAdapter<T>`: safe;
  - `PerFuzzTestFixtureAdapter<T>`: faster, but reset any state the fuzz
    test mutates.
- `Map`, `FlatMap`, `ConstructorOf` domains take no seeds; `ReversibleMap`
  does.

## Build

For CMake, follow the project's style. The flags macro,
`fuzztest_setup_fuzzing_flags()`, goes in the **top-level**
`CMakeLists.txt`: right after FuzzTest is made available, and before any
of the project's own `add_subdirectory()` or `add_library()`:

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

The macro sets flags only for its own directory and the directories below
it. A library in a sibling directory, or one defined before the macro,
stays uninstrumented. If the top-level `CMakeLists.txt` can't be edited,
pass the macro's flags as cache variables instead, in the `fuzztest`
build below:
- add `-g -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION -UNDEBUG
  -fsanitize=address -fsanitize-coverage=inline-8bit-counters
  -fsanitize-coverage=trace-cmp -DADDRESS_SANITIZER` to
  `-DCMAKE_C_FLAGS` and `-DCMAKE_CXX_FLAGS`;
- add `-fsanitize=address` to `-DCMAKE_EXE_LINKER_FLAGS`.

These are the macro's flags in tag `2026-06-29`
(`cmake/FuzzTestFlagSetup.cmake`); check them again after an upgrade.

Either way, check the library's objects (`build.md`), and make the
library static: FuzzTest reads only the first module's counters (warning:
`... was called multiple times`). If the project builds the library only
shared, making it static for these builds is a `fuzz(build)` change.

Build two variants, `fuzztest` and `coverage`, each in its own build
directory. `<test_target>` is the fuzz test's CMake target. Put `clang`
and `clang++` from the LLVM directory on `PATH` (`run.md`). The empty
Release flags matter in both variants. FuzzTest's dependency `antlr_cpp`,
like many projects, sets `CMAKE_BUILD_TYPE=Release` when it's empty.
Release's `-O3 -DNDEBUG` would then compile the assertions out.
- **Clang 20 or later:** with clang 18, the link fails on `__sancov_cntrs`
  in a discarded section; with clang 19 and UBSan, it fails on
  `__muloti4`. Ubuntu 24.04: `clang-20 lld-20 libclang-rt-20-dev
  llvm-20`, with `/usr/lib/llvm-20/bin` as the LLVM directory.

`fuzztest`: fuzzing mode, which adds ASan and coverage instrumentation
(Clang only):
<!-- ci: fuzztest-build -->
```sh
CC=clang CXX=clang++ cmake -S . -B "$F/build/fuzztest" -DFUZZTEST_FUZZING_MODE=on \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_C_FLAGS_RELWITHDEBINFO="-O1 -g" -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="-O1 -g" \
  -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= \
  -DCMAKE_C_FLAGS="-fsanitize=$UB -fno-sanitize-recover=$UB -fno-sanitize=function" \
  -DCMAKE_CXX_FLAGS="-fsanitize=$UB -fno-sanitize-recover=$UB $HARDEN" \
  -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=$UB" && cmake --build "$F/build/fuzztest" --target <test_target>
```

`coverage`: unit-test mode (no `FUZZTEST_FUZZING_MODE`):
<!-- ci: fuzztest-coverage-build -->
```sh
CC=clang CXX=clang++ cmake -S . -B "$F/build/coverage" -DCMAKE_BUILD_TYPE= -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= \
  -DCMAKE_C_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION" \
  -DCMAKE_CXX_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION" \
  -DCMAKE_EXE_LINKER_FLAGS="-fprofile-instr-generate" && cmake --build "$F/build/coverage" --target <test_target>
```

- **UBSan** comes from the cache flags, with `$UB` (`build.md`); the macro
  adds only ASan.
- **`-O1 -g` explicitly.** Upstream's `RelWithDebug` is an unknown build
  type, so it gets no flags: `-O0`, no `-g`. Plain `RelWithDebInfo` adds
  `-DNDEBUG`.

Bazel (untested in CI):
1. Add `bazel_dep(name = "fuzztest", version = "20260629.0")`.
2. Generate the configs with
   `bazel run @fuzztest//bazel:setup_configs > fuzztest.bazelrc`, and
   import `fuzztest.bazelrc` from `.bazelrc`.
3. Build with `bazel build --config=fuzztest //<package>:<test>`.
4. Run the binary from `bazel-bin/` through the launcher.

Docs: `doc/quickstart-cmake.md`, `doc/quickstart-bazel.md`.

## Run

A develop run. `L`, `F`, `H` and the environment are as in `run.md`;
`<fuzz_test>` is `<Suite.Test>`:

<!-- ci: fuzztest-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" \
  --env FUZZTEST_TESTSUITE_IN_DIR="$F/corpus/$H" --env FUZZTEST_TESTSUITE_OUT_DIR="$F/corpus/$H" \
  --env FUZZTEST_REPRODUCERS_OUT_DIR="{run}/artifacts/copy-{copy}" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "$F/build/fuzztest/<test_binary>" --fuzz=<fuzz_test> --fuzz_for=<run_s>s \
  --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
```

- **macOS: `--rss_limit_mb=0`** here and in every FuzzTest replay.
  FuzzTest's tag `2026-06-29` reads macOS's peak RSS, which is in bytes,
  as KiB, so any limit aborts at the first input. Upstream fixed this
  after that tag.
- **One flat corpus directory** per fuzz test serves as both `IN_DIR` and
  `OUT_DIR`. The run reads `IN_DIR` at the start and writes new inputs to
  `OUT_DIR`. Inputs read from `IN_DIR` aren't written again, so the
  directory grows only by new inputs.
- **No `--corpus_database`:** CMake builds read it but never write it.
- `--list_fuzz_tests` lists the fuzz tests. Status lines look like
  `[*] Corpus size: <n> | Edges covered: <n> | ...`. Durations are
  written `30s`, `5m`, `2h`, `inf`.
- The run stops at the first finding; `--continue_after_crash` only works
  with Bazel's Centipede engine.
- **Minimizing a corpus, and merging a long run's** (undocumented
  upstream; re-check after an upgrade): minimize the union of the
  persistent corpus and the long run's corpus, on the current build. The
  minimizing run stops by itself, and its result replaces the persistent
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
  The result keeps FuzzTest's edges, not every source branch. A branch
  compiled without an edge of its own, such as a select instruction, can
  lose its only input. So llvm-cov's branch count may drop a little after
  a merge: 2 of 100 branches on a test. That's accepted.

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
- The replay prints one `[.] Replaying ...` line per input. Check exit
  status 0 and that `profraw/*.profraw` exists before exporting.
- If **it aborts** on a crashing input (exit 134, no profile), replay one
  file per process, so every other input counts:
  <!-- ci: fuzztest-cov-replay-files -->
  ```sh
  $L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
    --env LLVM_PROFILE_FILE="{run}/profraw/%p.profraw" --env FUZZTEST_REPRODUCERS_OUT_DIR="{run}/artifacts/copy-{copy}" -- sh -c '
      b=$1; dir=$2; log=$3; shift 3
      for f in "$dir"/*; do FUZZTEST_REPLAY=$f "$b" "$@" > "$log" 2>&1 || { echo "crashed: $f"; cat "$log"; }; done' \
    sh "$F/build/coverage/<test_binary>" "$F/corpus/$H" "{scratch}/replay.log" --gtest_filter=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
  ```
  As in libFuzzer's replay (`coverage.md`, "Replay"), each input that
  crashes is a finding for `fuzz-triage` and stays in the corpus. The log
  has the crash's report after a `crashed: <file>` line. Until the bug is
  fixed, the snapshot is partial.
- If the corpus directory is missing or empty, the replay aborts too. Run
  without `FUZZTEST_REPLAY` and with `--env FUZZTEST_FUZZ_FOR=0s` instead:
  that runs the `.WithSeeds` seeds, and no random inputs after them.

**Exact subset:** to replay exactly the inputs in `{dir}`, set
`FUZZTEST_REPLAY={dir}`; `{dir}` needs at least one input. For example,
for `reach.py`, with its paths quoted as in `coverage.md`, "Inputs that
reach a line":
<!-- ci: fuzztest-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --env "LLVM_PROFILE_FILE={out}/%p.profraw" --env "FUZZTEST_REPLAY={dir}" \
  --timeout <timeout_s> --replay "'$F/build/coverage/<test_binary>' --gtest_filter=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>" \
  --export "sh -c 'l=\$1; shift; llvm-profdata merge -sparse \"\$0\"/*.profraw -o \"\$0/p.profdata\" && llvm-cov export -format=lcov -instr-profile=\"\$0/p.profdata\" \"\$@\" > \"\$l\"' {out} {lcov} '$F/build/coverage/<test_binary>'"
```

**The gate's state-leak check** (`fuzz-harness`'s gate.md): replay the
sample as one directory in one process, then one file per process.
Compare the two without the fetched dependencies (`_deps/`), since
FuzzTest's own code replays a directory and a file differently:
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

## Triage

A report starts with `=== BUG FOUND!` and the failing arguments
(`argument 0: ...`). Then come a regression test draft and the path of
the reproducer, which is in FuzzTest's own serialization. For a signal,
such as an `abort()` or a failed `assert`, its stack follows. A timeout
or memory limit prints `[!] Per-input timeout exceeded` or
`[!] RSS limit exceeded` before the banner; any stack after the banner is
the watchdog's, not the input's. `crash_buckets.py` reads all of these
reports.

- **Reproduce** a reproducer from `runs/<id>/artifacts/copy-<i>/` in the
  fuzzing build. On macOS, pass `--rss_limit_mb=0` (Run, above). To run
  it 100 times in one process, use `FUZZTEST_REPLAY` set to a directory of
  100 copies of the file.
  <!-- ci: fuzztest-repro -->
  ```sh
  $L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env FUZZTEST_REPLAY="<finding>" \
    --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
    "<build>/fuzztest/<test_binary>" --gtest_filter=<fuzz_test> \
    --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
  ```
- **Minimize:** `FUZZTEST_MINIMIZE_REPRODUCER` exits at the first smaller
  input that crashes, whatever the crash. So run it in a loop that takes
  each result as the next input, until a step finds nothing smaller
  within `<timeout_s>` (20 to 60 s):
  <!-- ci: fuzztest-minimize -->
  ```sh
  m="$F/crashes/<bucket>/minimized"; mkdir -p "$F/crashes/<bucket>"; cp "<finding>" "$m"
  for i in $(seq 1 30); do
    out="$F/runs/min-$H-<n>-$i/reproducers"; mkdir -p "$out"
    $L start --id min-$H-<n>-$i --harness $H --timeout <timeout_s> --env FUZZTEST_MINIMIZE_REPRODUCER="$m" \
      --env FUZZTEST_REPRODUCERS_OUT_DIR="$out" --env ASAN_OPTIONS="$ASAN_OPTIONS" \
      --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
      "<build>/fuzztest/<test_binary>" --fuzz=<fuzz_test> \
      --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb> > /dev/null || :  # 134: smaller crash
    next=$(ls -S "$out" | tail -n 1)
    [ -n "$next" ] && [ "$(wc -c < "$out/$next")" -lt "$(wc -c < "$m")" ] || break
    cp "$out/$next" "$m"
  done
  ```
  Then replay and group the result as for every engine (`fuzz-triage`,
  "Minimize").
- **The reproducer's values:** the regression test draft
  (`=== Regression test draft`) calls the property function with the
  failing arguments. The property function is our harness, so take only
  the argument values from the draft, for the reproducer's direct API
  call (`regression.md`, "The bug's reproducer"). A `FUZZ_TEST_F` gets no
  draft: take its values from the report's `argument <i>: ...` lines.
  The draft and the `argument` lines cut long values. A value that ends
  in ` ...<value too long>` was cut at 2048 printed characters, where an
  escaped byte such as `\001` counts four. In the `argument` lines, a
  string or byte vector that ends in ` ..."` was cut after 1000 bytes.
  Print a cut value once from the property function instead, replaying
  the reproducer (Reproduce, above). The reproducer file doesn't show the
  values: FuzzTest's serialization is binary.
- **Seeds in `.WithSeeds({...})`** run in every mode, including the short
  unit-test run in normal CI. So they are a harness check, not a bug's
  test.

## Long runs and OSS-Fuzz

**Long runs** are N copies of the fuzz test, prepared for the user
(`fuzz-campaign`'s long-runs.md). `<tree>` and `<tree_build>` are as in
`run.md`, "Long runs". Each copy reads the persistent corpus at the start
(`IN_DIR`), and writes new inputs into the run's corpus (`OUT_DIR`), the
launcher's default `--corpus`:
<!-- ci: fuzztest-long -->
```sh
$L start --prepare --id $H-long<n> --harness $H --copies <copies> --time <run_s> --tree "<tree>" \
  --env FUZZTEST_TESTSUITE_IN_DIR="$F/corpus/$H" --env FUZZTEST_TESTSUITE_OUT_DIR="{run}/corpus" \
  --env FUZZTEST_REPRODUCERS_OUT_DIR="{run}/artifacts/copy-{copy}" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "<tree_build>/fuzztest/<test_binary>" --fuzz=<fuzz_test> --fuzz_for=<run_s>s \
  --time_limit_per_input=<input_timeout_s>s --rss_limit_mb=<memory_mb>
```

- Copies don't share inputs while they run, since FuzzTest has no
  reload. Files are named by content, so one output directory for all
  copies is safe.
- Each copy stops at its first finding; the others continue.
- No `--jobs=<n>`: it needs Bazel's Centipede.
- If the run has no duration: `run.md`, "Settings to flags".
- At analysis, merge by minimizing the union of the persistent corpus and
  the run's corpus (Run, above).

**OSS-Fuzz** builds FuzzTest harnesses with Bazel. A CMake project can
follow libavif, which builds them in libFuzzer compatibility mode, with a
pinned FuzzTest and a few patches (`tests/oss-fuzz/build.sh`). The
upstreaming pull request says so and leaves the choice to the
maintainers. The campaign itself uses FuzzTest's standard mode only.

## Pitfalls

- Domains or seeds initialized from globals of other files (static
  initialization order); use a seed provider.
- Fixtures whose state leaks between iterations: nondeterministic findings.
- GCC: fine for unit-test mode, rejected for fuzzing mode.
