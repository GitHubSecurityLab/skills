# C/C++ builds

## Toolchain

Check it and install what's missing: `install.md` next to this file. If the
host is macOS: use Homebrew's clang by absolute path
(`CC=/opt/homebrew/opt/llvm/bin/clang`).

## Variants

| Variant | Compile flags (library and harness) | Link the fuzz binary with |
|---|---|---|
| `libfuzzer` | `-g -O1 -fno-omit-frame-pointer -fsanitize=fuzzer-no-link,address,$UB -fno-sanitize-recover=$UB $HARDEN -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION` | `-fsanitize=fuzzer,address,$UB` |
| `aflpp-plain` | `CC`/`CXX` the AFL++ compiler (`<afl_cc>`, `aflpp.md`), `-g -O1`, no sanitizers; no `-fsanitize=fuzzer*` on the library | `-fsanitize=fuzzer`, compiling and linking the harness in one command |
| `aflpp` | as `aflpp-plain`, with `AFL_USE_ASAN=1` and `-fsanitize=$UB -fno-sanitize-recover=$UB $HARDEN` in its wrapper (`aflpp.md`) | as `aflpp-plain` |
| `cmplog` | as `aflpp-plain`, with `AFL_LLVM_CMPLOG=1` in the wrapper | as `aflpp-plain` |
| `coverage` | `-g -O1 -fprofile-instr-generate -fcoverage-mapping -mllvm -runtime-counter-relocation -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`, no sanitizers (the relocation flag lets continuous profiles work on Linux) | `-fprofile-instr-generate -fsanitize=fuzzer` (libFuzzer's `main` replays the corpus) |
| `msan` (Linux, optional; untested in CI) | `-fsanitize=fuzzer-no-link,memory -fsanitize-memory-track-origins` | `-fsanitize=fuzzer,memory` |
| `ubsan-int` (optional; untested in CI) | `libfuzzer` plus `-fsanitize=unsigned-integer-overflow,implicit-conversion,alignment` | as `libfuzzer` |

`$UB` is the one UBSan list of every build, reproducer, test build and CI
template (OSS-Fuzz's; `ubsan-int` adds `unsigned-integer-overflow`).
`$HARDEN` turns on libstdc++'s and libc++'s bounds checks, which catch an
index past a `std::vector`'s size but within its capacity (ASan with
libstdc++ doesn't); every variant but `coverage` compiles with it, as do
reproducers and test builds. Add both to `.seclab/fuzz/env.sh` (`run.md`):
<!-- ci: c-ub -->
```sh
UB=array-bounds,bool,builtin,enum,function,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unreachable,vla-bound,vptr
HARDEN="-D_GLIBCXX_ASSERTIONS -D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_DEBUG"
```
- **C code adds `-fno-sanitize=function`, last** on every command that
  compiles C (a later `-fsanitize=...,$UB` turns it back on): C often
  calls callbacks through a cast function type (`(free_fn)node_free`),
  which aborts on the first input.
- **`-fno-rtti` code adds `-fno-sanitize=vptr`**: `vptr` needs RTTI.
- Not the whole `undefined` group, nor AFL++'s `AFL_USE_UBSAN=1` (which
  enables it): its extra checks report bugs the other builds never see.

Every variant defines `FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`; AFL++'s
compilers do it themselves. Link statically (`-DBUILD_SHARED_LIBS=OFF`, `--disable-shared`, `--default-library=static`):
uninstrumented shared libraries give the fuzzer no feedback.
- If you build `msan`: do so only for C code without other dependencies,
  or through the project's OSS-Fuzz MSan build: every linked library, the
  C++ standard library included, must be built with MSan, or reports are
  false.
- If you build `ubsan-int`: it reports much intended wrap-around; use it on
  arithmetic-heavy scope files and check each report against intent.
- If the project uses FuzzTest: its variants are in `fuzztest.md` next to this file.

## Integrating with the build system

**CMake** (out of tree; `F` from `.seclab/fuzz/env.sh`, run.md;
`<compile_flags>` and `<link_flags>` are the variant's columns above):

<!-- ci: c-cmake-build -->
```sh
cmake -S . -B "$F/build/<variant>" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_C_COMPILER=<llvm_bin>/clang -DCMAKE_CXX_COMPILER=<llvm_bin>/clang++ \
  -DCMAKE_BUILD_TYPE= -DCMAKE_C_FLAGS="<compile_flags> -fno-sanitize=function" -DCMAKE_CXX_FLAGS="<compile_flags>" \
  -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= \
  -DBUILD_SHARED_LIBS=OFF -D<fuzzers_option>=ON -DFUZZING_ENGINE="<link_flags>"
cmake --build "$F/build/<variant>" --target <harness>
```

The empty Release flags stop a project that sets `CMAKE_BUILD_TYPE=Release`
when it's empty from adding `-O3 -DNDEBUG` after ours (the build check
below catches other forced types).
- If the library is built for harnesses outside the project (OSS-Fuzz's,
  below): it needs neither of the last two `-D`s: `--target <library>`.
- If the project has no fuzzing option for `<fuzzers_option>`: add one
  first, in the project's style:
  ```cmake
  option(<PROJECT>_BUILD_FUZZERS "Build fuzz targets" OFF)
  set(FUZZING_ENGINE "-fsanitize=fuzzer" CACHE STRING "Link flags of the fuzzing engine")
  if(<PROJECT>_BUILD_FUZZERS)
    add_subdirectory(fuzz)
  endif()
  # fuzz/CMakeLists.txt
  add_executable(parse_fuzzer parse_fuzzer.c)
  target_link_libraries(parse_fuzzer PRIVATE <project-library> ${FUZZING_ENGINE})
  ```
  OSS-Fuzz's `build.sh` can then pass `-DFUZZING_ENGINE="$LIB_FUZZING_ENGINE"`.

**Autotools** (untested in CI): configure from the build directory:
`cd .seclab/fuzz/build/libfuzzer && <repo>/configure CC=... CFLAGS="..." --disable-shared && make`.
If the checkout has no `configure` yet (a git checkout often hasn't): first run
the project's `./autogen.sh` or `./bootstrap` (else `autoreconf -fi`) in the
checkout, and check with `git status` that it changed no tracked files.

**Meson** (untested in CI):
`CC=... CXX=... meson setup .seclab/fuzz/build/libfuzzer --buildtype=plain -Db_ndebug=false --default-library=static -Db_lundef=false -Dc_args="..." -Dcpp_args="..." -Dc_link_args="..." -Dcpp_link_args="..."`,
the compile flags as link arguments too, or the sanitizer runtimes aren't
linked (a link failure on Linux, a crash at start on macOS).
`--buildtype=plain` and `b_ndebug=false` keep a default `release` build
type from adding `-O3 -DNDEBUG`; `b_lundef=true` (the default) breaks ASan
with shared libraries.

**Bazel** (untested in CI): `rules_fuzzing` (https://github.com/bazel-contrib/rules_fuzzing)
with `cc_fuzz_test` and a `.bazelrc` config such as `asan-libfuzzer`. Build
with `bazel build`, then run the binary from `bazel-bin/` through the
launcher.

**In-tree builds** (plain Makefiles; builds that fail outside the source directory) get one git worktree
per variant, outside the checkout. Before rebuilding a variant, sync its worktree to the checkout's working
tree, uncommitted harnesses and fixes included. A snapshot commit, made with a temporary index and its own
identity, leaves the branch and the staged changes alone; a clean checkout uses `HEAD` itself:
<!-- ci: build-worktree -->
```sh
W="<checkout>/../<repo>.seclab-worktrees/build/<variant>"; [ -d "$W" ] || git worktree add -q --detach "$W" HEAD
snap() { if [ -z "$(git status --porcelain)" ]; then git rev-parse HEAD; else (
  export GIT_INDEX_FILE="$(git rev-parse --git-path seclab-snapshot.index)"; trap 'rm -f "$GIT_INDEX_FILE"' EXIT
  git read-tree HEAD && git add -A &&
    git -c user.name=seclab -c user.email=seclab@localhost commit-tree "$(git write-tree)" -p HEAD -m snapshot ); fi; }
c=$(snap) && git -C "$W" checkout -q --detach "$c"
```
Only changed files are rewritten, so the rebuild is incremental. Build each variant in its worktree,
link the outputs into `$F/build/<variant>/`, where every later command looks, and map both spellings
of the worktree's path (through symlinks or not) to the checkout:
<!-- ci: c-intree-build -->
```sh
W="<checkout>/../<repo>.seclab-worktrees/build/<variant>"; mkdir -p "$F/build/<variant>"
map="-fcoverage-prefix-map=$(cd "$W" && pwd)=$PWD -fcoverage-prefix-map=$(cd "$W" && pwd -P)=$PWD"
make -C "$W" CC=<llvm_bin>/clang CFLAGS="<compile_flags> $map -fno-sanitize=function" \
  LDFLAGS="<link_flags> -fno-sanitize=function" <targets>
ln -sf "$W/<binary>" "$W/<library>" "$F/build/<variant>/"
```

**OSS-Fuzz projects:** read `projects/<name>/build.sh` in `google/oss-fuzz`
for the steps and harness list. It expects `$CC`, `$CXX`, `$CFLAGS`,
`$CXXFLAGS`, `$LIB_FUZZING_ENGINE`, `$SRC`, `$OUT`, `$WORK`; running it
natively only works when it doesn't depend on the Docker image's layout.
If the harnesses are kept there, not in the project: they're for the
baseline only. Copy them to `.seclab/fuzz/oss-fuzz/`, and per variant,
build the library as above and link each copy against it as `build.sh`
does, in a script `.seclab/fuzz/build/<variant>.sh`. New harnesses, and copies you extend,
go into the project's tree (`fuzz-campaign`'s handoff.md offers them for
`projects/<name>/`).

## Instrumentation checks

- **Assertions on, little optimization:** no compile line may contain
  `-DNDEBUG` or `-O2` and above (CMake and Meson write
  `compile_commands.json`; other builds: one compile line of a verbose
  build, e.g. `make V=1`):
  <!-- ci: c-build-check -->
  ```sh
  test -s "$F/build/<variant>/compile_commands.json" && ! grep -E -- '-DNDEBUG|-O[23s]([ "]|$)' "$F/build/<variant>/compile_commands.json"
  ```
  If it matches: the project forces its own flags; override them (CMake:
  `-DCMAKE_C_FLAGS_<TYPE>=` for the build type it sets).
- **Static, before running anything:** the library archive or objects
  (not the linked binary) contain the variant's hooks:
  <!-- ci: c-hooks-check -->
  ```sh
  nm -A <library> 2>/dev/null | awk '/sanitizer_cov/ {c++} /__asan_/ {a++} /__cmplog_ins_hook/ {l++} /__profc_/ {p++}
    END {printf "sanitizer_cov %d, __asan_ %d, __cmplog_ins_hook %d, __profc_ %d\n", c, a, l, p}'
  ```
  `libfuzzer`: `sanitizer_cov` and `__asan_` above 0; `coverage`: `__profc_`;
  AFL++ variants: `aflpp.md` next to this file.
- **libFuzzer, at the start of each run:**
  `INFO: Loaded 1 modules (N inline 8-bit counters)`. For a real library
  `N` is in the thousands; a few dozen means only the harness is
  instrumented.
- **FuzzTest**'s `Edges covered` grows from its own framework: check the
  library's objects as above.

## Common failures

| Symptom | Cause and fix |
|---|---|
| `libclang_rt.fuzzer_osx.a` not found | Apple's clang; use Homebrew LLVM |
| duplicate symbol `main` | A library or tool object defines `main`; leave it out of the fuzz target |
| `-Werror` fails on new warnings, or a `coverage` link on `argument unused during compilation: '-mllvm -runtime-counter-relocation'` | Add `-Wno-error` to the fuzz and coverage variants only |
| undefined `__asan_*` at link | Link with the same `-fsanitize=` flags as the compile |
| `-Wl,-z,defs` or `--no-undefined` errors | Remove them for sanitizer builds |
| `-fsanitize=vptr` not allowed with `-fno-rtti` | Add `-fno-sanitize=vptr` |
| profile version errors in `llvm-profdata` | Tools from another LLVM version than the compiler (e.g. `xcrun llvm-cov` with Homebrew clang); use the compiler's |
| MSan reports in the standard library | Uninstrumented dependencies; skip MSan |
