# Build

Fuzzing builds should come from the project's own build system, so
harnesses can be contributed upstream and maintainers can rebuild them.

## When to use

- Setup of a campaign, a new target needing a different build, a harness
  change that needs build changes, or "build this with ASan and libFuzzer".

Not for writing harnesses (`SKILL.md`) or running them (runs.md).

## Reads

- `CAMPAIGN.md`: Settings, Disposable environment, Harnesses.
- The language skill for exact flags and commands: `fuzz-c-cpp`,
  `fuzz-java` or `fuzz-js`.

## Build variants

| Language | Variants |
|---|---|
| C/C++, `LLVMFuzzerTestOneInput` | `libfuzzer` (fuzzer + ASan + UBSan), `coverage` (source-based coverage, no sanitizers); for AFL++ long runs `aflpp-plain` (AFL++ compiler, no sanitizer), `aflpp` (+ ASan + UBSan) and `cmplog` (CmpLog); optional `msan` (Linux) and `ubsan-int` (integer checks) |
| C/C++, FuzzTest | `fuzztest` (fuzzing mode + ASan + UBSan), `coverage` |
| Java | the project's test build with `jazzer-junit`, plus a classpath file for Jazzer's standalone driver, which every run uses; coverage through the JaCoCo agent |
| JS/TS | the project's install (`npm ci` or equivalent) with Jazzer.js; coverage through Istanbul |

## Steps

1. **First-build notice.** If `CAMPAIGN.md` says it wasn't shown yet,
   show it before running any project command (see `fuzz-campaign`,
   "Setup card").
2. **Toolchain.** Check what the language skill requires (compilers,
   engine versions, build tools) with the check in its `install.md`, which
   runs no project code, and record the versions in the target file.
   - **Install plan** from the language skill's `install.md`: only what's
     missing for this project, each item with the command for this OS and
     the expected time. Record what was installed in `decisions.md`.
   - Installs go to user level where possible: Homebrew, `~/.local`, fnm,
     SDKMAN, or project-local Maven and npm dependencies.
   - **Build changes checkpoint** in interactive mode: show the install plan
     and which build files will change, and ask once.
   - `sudo` and system settings changes (package installs,
     `afl-system-config`) only when the environment is disposable. On the
     user's own machine, print the command for the user to run, or use the
     engine's documented environment workaround.
3. **Understand the project's build.** Find the build system, existing
   fuzzing or sanitizer options (e.g. a CMake `*_FUZZ*` or `*_SANITIZE*`
   option, an existing `fuzz/` target, OSS-Fuzz's `build.sh`), and how the
   project builds its own tests.
4. **Choose the integration, in this order:**
   1. an existing fuzzing or sanitizer option of the project;
   2. a minimal addition in the project's style, e.g. a CMake option that
      adds the fuzz targets, or a test-scoped dependency;
   3. a standalone script `.seclab/fuzz/build/<variant>.sh` when changing the
      build isn't practical.
5. **Build each variant in its own directory** under
   `.seclab/fuzz/build/<variant>/`: CMake `-B`, a Meson build directory, an
   autotools build directory (`../configure` from there), or the build tool's
   output option. Use the same compiler and flags for the library across
   variants, link statically where possible, and don't build the project's
   CLI tools or tests with fuzzer flags.
   - If the project can only build in-tree: build each variant in its own
     git worktree outside the checkout, synced to the checkout's working
     tree before each rebuild (`fuzz-c-cpp`'s build.md, "In-tree builds").
6. **Verify each variant.** The fuzz binary or test class exists, and the
   target library itself is instrumented: C/C++ by its object files'
   symbols, Java by plain `INFO: Instrumented <class>` lines (not
   `with custom hooks only`), JS by the target's files in the lcov
   (language skill). A counter in the fuzz binary's start-up output
   doesn't show it: the harness alone has counters.
7. **Fuzzing-only patches,** when coverage analysis finds a blocker that
   fuzzing can't pass (checksums, signatures, random seeds, time checks):
   - C/C++: guard the change with
     `#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`, and define that macro
     in every fuzzing and coverage variant;
   - Java/JS: prefer a replace hook (`fuzz-java`, `fuzz-js`) or harness setup
     over editing sources;
   - commit it separately as `fuzz(fuzzing-mode): <what>`;
   - note in the target file that crashes past this point must be checked
     with a valid checksum or signature during triage.
8. **Commit and record.** Build changes become `fuzz(build): <what>`
   commits on the campaign branch. Record the variants and their build
   commands in the target file (Harness, Build variants), and after each
   build the commit it came from:
   <!-- ci: build-record -->
   ```sh
   git rev-parse HEAD > .seclab/fuzz/build/<variant>/built-from
   ```
9. **Before reusing builds** (Resume, or after anything switched or reset
   the checkout), rebuild every variant this check calls stale; builds are
   incremental, so it's cheap:
   <!-- ci: build-stale -->
   ```sh
   for v in .seclab/fuzz/build/*/; do
     [ "$(cat "$v/built-from" 2>/dev/null)" = "$(git rev-parse HEAD)" ] &&
       [ -z "$(git status --porcelain --untracked-files=no)" ] || echo "stale: $v"
   done
   ```

## When the build fails

Diagnose before changing flags at random: missing dependencies, `-Werror`
with sanitizer warnings, a `main()` in the library clashing with the
fuzzer's, uninstrumented shared libraries, incompatible compiler versions.
The language skill lists the common cases. If the target can't be built
after 3 distinct approaches: interactive mode asks the user; autonomous
mode sets it aside (status `set-aside`, the reason in its file) and moves
to the next target.

## Done when

- Every variant the next steps need builds from a recorded command and passes
  its instrumentation check.
- Build changes are committed separately; tool versions are recorded.

## Pitfalls and shortcuts to reject

- A coverage build without the fuzzing-mode macro or the same fuzzing-only
  patches as the fuzzing build. Coverage then measures different code.
- Mixing compilers or LLVM versions between variants (coverage data from
  another LLVM version can't be merged).
- Instrumenting only the harness and not the library. The fuzzer then sees
  almost no coverage feedback.
- Disabling sanitizer checks to make the build pass.
- Editing `.gitignore` or committing build output.

## Writes

- `targets/<id>.md`: Harness (build variants and commands), tool versions.
- `CAMPAIGN.md`: Targets status (`set-aside` when it applies).
- `decisions.md`: integration choice, installs.
