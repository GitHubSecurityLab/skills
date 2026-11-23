# C/C++ builds

## Compiler

Compile with `<llvm_bin>`'s `clang` and `clang++`, so the compiler is the
same LLVM as `llvm-cov` and the runtimes (`install.md` next to this file).
Name them by absolute path, as the commands below do: a shell without
`env.sh`'s `PATH` (`run.md`) can find another `clang`, e.g. Apple's on
macOS, which has no libFuzzer runtime. The AFL++ variants use AFL++'s
compiler instead (the table below).

## Variants

| Variant | Compile flags (library and harness) | Link the fuzz binary with |
|---|---|---|
| `libfuzzer` | `-g -O1 -fno-omit-frame-pointer -fsanitize=fuzzer-no-link,address,$UB -fno-sanitize-recover=$UB $HARDEN -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION` | `-fsanitize=fuzzer,address,$UB` |
| `aflpp-plain` | `CC`/`CXX` the AFL++ compiler (`<afl_cc>`, `aflpp.md`), `-g -O1`, no sanitizers; no `-fsanitize=fuzzer*` on the library | `-fsanitize=fuzzer`, compiling and linking the harness in one command |
| `aflpp` | as `aflpp-plain`, with `AFL_USE_ASAN=1` and `-fsanitize=$UB -fno-sanitize-recover=$UB $HARDEN` in its wrappers (`aflpp.md`) | as `aflpp-plain` |
| `cmplog` | as `aflpp-plain`, with `AFL_LLVM_CMPLOG=1` in its wrappers | as `aflpp-plain` |
| `coverage` | `-g -O1 -fprofile-instr-generate -fcoverage-mapping -mllvm -runtime-counter-relocation -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`, no sanitizers | `-fprofile-instr-generate -fsanitize=fuzzer` (libFuzzer's `main` replays the corpus) |
| `msan` (Linux, optional; untested in CI) | `-fsanitize=fuzzer-no-link,memory -fsanitize-memory-track-origins` | `-fsanitize=fuzzer,memory` |
| `ubsan-int` (optional; untested in CI) | `libfuzzer` plus `-fsanitize=unsigned-integer-overflow,implicit-conversion,alignment` | as `libfuzzer` |

Two of the `coverage` variant's flags:
- `-mllvm -runtime-counter-relocation`, the relocation flag, lets
  continuous profiles work on Linux.
- `-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0` turns fortify off. macOS turns
  it on by default, which makes `memcpy`, `strcpy` and the like macros
  from a system header. llvm-cov counts such a call right after an early
  `return` as run.

`$UB` is the one UBSan list of every build, reproducer, test build and CI
template. It is OSS-Fuzz's list; `ubsan-int` adds
`unsigned-integer-overflow`. `$HARDEN` turns on libstdc++'s and libc++'s
bounds checks. They catch an index past a `std::vector`'s size but within
its capacity, which ASan with libstdc++ doesn't. Every variant but
`coverage` compiles with `$HARDEN`, and so do reproducers and test
builds. Add both to `.seclab/fuzz/env.sh` (`run.md`):
<!-- ci: c-ub -->
```sh
UB=array-bounds,bool,builtin,enum,function,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unreachable,vla-bound,vptr
HARDEN="-D_GLIBCXX_ASSERTIONS -D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_DEBUG"
```
- **C code adds `-fno-sanitize=function`, last** on every command that
  compiles C. It goes last because a later `-fsanitize=...,$UB` turns the
  check back on. C often calls callbacks through a cast function type
  (`(free_fn)node_free`), and such a call aborts on the first input.
- **`-fno-rtti` code adds `-fno-sanitize=vptr`**: `vptr` needs RTTI.
- Don't use the whole `undefined` group, nor AFL++'s `AFL_USE_UBSAN=1`,
  which enables that group. The group's extra checks report bugs the
  other builds never see.

Every variant defines `FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`; AFL++'s
compilers do it themselves.

Build the project's libraries static: `-DBUILD_SHARED_LIBS=OFF`,
`--disable-shared` or `--default-library=static` (plain Makefiles:
"In-tree builds", below). Then each fuzz binary contains the libraries'
instrumented code. A shared library takes extra steps, and gives no
feedback if the binary loads another copy of it, e.g. an installed one.
- If a project library builds only shared (CMake
  `add_library(<name> SHARED ...)`, Meson `shared_library()`, a Makefile
  without an archive): it works with libFuzzer and with AFL++'s
  `afl-clang-fast` if each binary loads the variant's copy. The
  instrumentation checks below verify that. FuzzTest needs the library
  static (`fuzztest.md`).
- If you build `msan`: do so only for C code without other dependencies,
  or through the project's OSS-Fuzz MSan build. Every linked library, the
  C++ standard library included, must be built with MSan, or reports are
  false.
- If you build `ubsan-int`: it reports much wrap-around that is intended.
  Use it on arithmetic-heavy scope files, and check each report against
  the code's intent.
- If the project uses FuzzTest: its variants are in `fuzztest.md` next to
  this file.

## Integrating with the build system

**CMake** builds out of tree. `F` comes from `.seclab/fuzz/env.sh`
(run.md); `<compile_flags>` and `<link_flags>` are the variant's columns
in the table above:

<!-- ci: c-cmake-build -->
```sh
cmake -S . -B "$F/build/<variant>" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_C_COMPILER=<llvm_bin>/clang -DCMAKE_CXX_COMPILER=<llvm_bin>/clang++ \
  -DCMAKE_BUILD_TYPE= -DCMAKE_C_FLAGS="<compile_flags> -fno-sanitize=function" -DCMAKE_CXX_FLAGS="<compile_flags>" \
  -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= \
  -DBUILD_SHARED_LIBS=OFF -D<fuzzers_option>=ON -DFUZZING_ENGINE="<link_flags>"
cmake --build "$F/build/<variant>" --target <harness>
```

If a project sets `CMAKE_BUILD_TYPE=Release` when it's empty, the empty
Release flags stop it from adding `-O3 -DNDEBUG` after ours. The build
check below catches other forced build types.
- If the library is built for harnesses outside the project (OSS-Fuzz's,
  below): the configure step needs neither of the last two `-D`s, and
  `--target` names every library those harnesses link.
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
  OSS-Fuzz's `build.sh` can then pass
  `-DFUZZING_ENGINE="$LIB_FUZZING_ENGINE"`.

**Autotools** (untested in CI): configure from the build directory:
`cd .seclab/fuzz/build/libfuzzer && <repo>/configure CC=... CFLAGS="..." --disable-shared && make`.
A git checkout often has no `configure` yet. If so, first run the
project's `./autogen.sh` or `./bootstrap` in the checkout, or
`autoreconf -fi` if it has neither. Then check with `git status` that
this changed no tracked files.

**Meson** (untested in CI):
`CC=... CXX=... meson setup .seclab/fuzz/build/libfuzzer --buildtype=plain -Db_ndebug=false --default-library=static -Db_lundef=false -Dc_args="..." -Dcpp_args="..." -Dc_link_args="..." -Dcpp_link_args="..."`.
Pass the compile flags as link arguments too, or the sanitizer runtimes
aren't linked: the link fails on Linux, and the binary crashes at start
on macOS. `--buildtype=plain` and `b_ndebug=false` keep a default
`release` build type from adding `-O3 -DNDEBUG`. `b_lundef=true`, the
default, breaks ASan with shared libraries.

**Bazel** (untested in CI): `rules_fuzzing`
(https://github.com/bazel-contrib/rules_fuzzing) with `cc_fuzz_test` and
a `.bazelrc` config such as `asan-libfuzzer`. Build with `bazel build`,
then run the binary from `bazel-bin/` through the launcher.

**In-tree builds** (plain Makefiles, or builds that fail outside the
source directory) get one git worktree per variant, outside the checkout.
Before rebuilding a variant, sync its worktree to the checkout's working
tree, uncommitted harnesses and fixes included. The sync uses a snapshot
commit, made with a temporary index and its own identity, which leaves
the branch and the staged changes alone; a clean checkout uses `HEAD`
itself:
<!-- ci: build-worktree -->
```sh
W="<checkout>/../<repo>.seclab-worktrees/build/<variant>"; [ -d "$W" ] || git worktree add -q --detach "$W" HEAD
snap() { if [ -z "$(git status --porcelain)" ]; then git rev-parse HEAD; else (
  export GIT_INDEX_FILE="$(git rev-parse --git-path seclab-snapshot.index)"; trap 'rm -f "$GIT_INDEX_FILE"' EXIT
  git read-tree HEAD && git add -A &&
    git -c user.name=seclab -c user.email=seclab@localhost commit-tree "$(git write-tree)" -p HEAD -m snapshot ); fi; }
c=$(snap) && git -C "$W" checkout -q --detach "$c"
```
Only changed files are rewritten, so the rebuild is incremental.

In each variant's worktree, build every harness and the libraries it
links (`<targets>`). Where the Makefile can build a library both static
and shared, build only the static one: `-l<name>` picks a shared library
over a `.a`. Map both spellings of the worktree's path, through symlinks
or not, to the checkout. Then replace `$F/build/<variant>`, where every
later command looks, with a link to the worktree:
<!-- ci: c-intree-build -->
```sh
W="<checkout>/../<repo>.seclab-worktrees/build/<variant>"
map="-fcoverage-prefix-map=$(cd "$W" && pwd)=$PWD -fcoverage-prefix-map=$(cd "$W" && pwd -P)=$PWD"
make -C "$W" CC=<llvm_bin>/clang CFLAGS="<compile_flags> $map -fno-sanitize=function" \
  LDFLAGS="<link_flags> -fno-sanitize=function" <targets>
rm -rf "$F/build/<variant>" && ln -s "$W" "$F/build/<variant>"
```
Another tree (for a long run, `base` or the freshness check) gets a
worktree per variant it needs. For each such variant, run the block that
creates the tree, with `/<variant>` added to its path:
- a long run's tree: `fuzz-campaign`'s long-runs.md, "Which tree";
- `base`: `fuzz-campaign`'s long-runs.md, "The `base` worktree";
- the freshness check's trees: `fuzz-triage`'s fix.md, "Check the
  precondition, then freshness".

Then run the build block above with that `W`, without its last line.
These worktrees aren't synced to the checkout. Only the freshness check's
block moves its worktrees, to the commits it checks. The tree's directory
is its `<tree_build>`. A run passes each worktree it uses with `--tree`
(spec: `trees`).

An upstreaming branch's worktree (`fuzz-campaign`'s upstreaming.md)
builds like the checkout. Run both blocks from that worktree, with
`upstream-<topic>-build/` in place of `build/` in `W`; `<checkout>` stays
the campaign's checkout. Remove the variants' worktrees together with
the upstreaming branch's worktree.

**OSS-Fuzz projects:** read `projects/<name>/build.sh` in `google/oss-fuzz`
for the steps and the harness list. It expects `$CC`, `$CXX`, `$CFLAGS`,
`$CXXFLAGS`, `$LIB_FUZZING_ENGINE`, `$SRC`, `$OUT`, `$WORK`. Running it
natively only works when it doesn't depend on the Docker image's layout.

If the harnesses are kept in `google/oss-fuzz`, not in the project, they
only measure existing fuzzing (`fuzz-campaign`'s baseline). Copy them to
`.seclab/fuzz/oss-fuzz/`. Then, per variant, write a script
`.seclab/fuzz/build/<variant>.sh` that builds the library as above and
links each copy against it as `build.sh` does. New harnesses, and copies
you extend, go into the project's tree; `fuzz-campaign`'s upstreaming.md
offers them for `projects/<name>/`.

## Instrumentation checks

- **Assertions on, little optimization:** no compile line may contain
  `-DNDEBUG` or `-O2` and above. CMake and Meson write the compile lines
  to `compile_commands.json`, which this command checks (other builds:
  one compile line of a verbose build, e.g. `make V=1`):
  <!-- ci: c-build-check -->
  ```sh
  test -s "$F/build/<variant>/compile_commands.json" && ! grep -E -- '-DNDEBUG|-O[23s]([ "]|$)' "$F/build/<variant>/compile_commands.json"
  ```
  If the `grep` matches, the project forces its own flags: override them.
  For CMake, set `-DCMAKE_C_FLAGS_<TYPE>=` for the build type the project
  sets.
- **Before running anything:** each project library the harnesses link
  has the variant's hooks. Check the library's archive or objects, not a
  linked binary: a binary has the harness's hooks too, and linking drops
  `__profc_`. The counts are totals, so check one library per command:
  <!-- ci: c-hooks-check -->
  ```sh
  nm -A <library> 2>/dev/null | awk '/sanitizer_cov/ {c++} /__asan_/ {a++} /__cmplog_ins_hook/ {l++} /__profc_/ {p++}
    END {printf "sanitizer_cov %d, __asan_ %d, __cmplog_ins_hook %d, __profc_ %d\n", c, a, l, p}'
  ```
  `libfuzzer`: `sanitizer_cov` and `__asan_` above 0; `coverage`:
  `__profc_`; AFL++ variants: `aflpp.md` next to this file.
- **Each binary loads the variant's copy of a shared project library.**
  - Linux: `env -u LD_LIBRARY_PATH ldd <binary>` shows the library's path
    in the build (the launcher passes no `LD_LIBRARY_PATH` either). If it
    doesn't, link with `-Wl,-rpath,<its directory>`.
  - macOS: `otool -L <binary>` names that path, or `@rpath/` with the
    library's directory among the `LC_RPATH`s in `otool -l <binary>`. If
    it doesn't, run `install_name_tool -id <its path> <library>` each time
    the library is built, before the binaries link it.
- **libFuzzer, at the start of each run:** the log shows
  `INFO: Loaded 1 modules (N inline 8-bit counters)`, with one more module
  per shared project library. For a real library, `N` is in the
  thousands; a few dozen means only the harness is instrumented.
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
