# AFL++ tips

Docs: https://github.com/AFLplusplus/AFLplusplus/tree/stable/docs, mainly
`fuzzing_in_depth.md`. The installed version lists its options with
`afl-fuzz -hh`. Environment variables: `docs/env_variables.md`.

License: AGPL-3.0-or-later since 5.00c. Invoke installed copies only;
never copy AFL++'s code. Harness-side files such as the libFuzzer driver
keep their Apache-2.0 headers.

## Install

- **Linux:** build a pinned release at user level, with the packages
  from `install.md`. Ubuntu's `afl++` package is outdated.
  1. `git clone --depth 1 --branch v5.03c https://github.com/AFLplusplus/AFLplusplus`
  2. `LLVM_CONFIG=llvm-config-20 make all PREFIX=$HOME/.local`, then
     `make install PREFIX=$HOME/.local` (the prefix is compiled in).
  3. `make all` skips LLVM mode when LLVM mode fails to build: check that
     `afl-clang-fast` and `afl-clang-lto` exist.
- **macOS:** `brew install afl++` (uses Homebrew LLVM). 5.03c changed
  shared memory on macOS: rebuild targets built with older versions.

## Builds

Three variants, all from the libFuzzer harness: `aflpp-plain` without a
sanitizer, `aflpp` with ASan+UBSan, and `cmplog`. Their flags are in
`build.md` next to this file.

`AFL_USE_ASAN` and `AFL_LLVM_CMPLOG` are read at compile time, so a
rebuild without them in the environment silently drops the sanitizers or
CmpLog. Put them in a wrapper per variant, with the UBSan flags: `$UB`
and `$HARDEN` from env.sh, written out.

`<afl_cc>`, the AFL++ compiler the wrappers call, is:
- `afl-clang-lto` where it builds. It instruments at link time, which
  gives the best coverage. It needs `lld` of the same LLVM version, and
  `AR=llvm-ar RANLIB=llvm-ranlib`.
- `afl-clang-fast` otherwise, on macOS, and with a shared project
  library, whose edge IDs would collide with the binary's.

<!-- ci: aflpp-wrappers -->
```sh
mkdir -p "$F/build/bin" && cd "$F/build/bin"
cat > aflpp-cc <<EOF
#!/bin/sh
AFL_USE_ASAN=1 exec <afl_cc> -fsanitize=$UB -fno-sanitize-recover=$UB $HARDEN -fno-sanitize=function "\$@"
EOF
cat > cmplog-cc <<'EOF'
#!/bin/sh
AFL_LLVM_CMPLOG=1 exec <afl_cc> "$@"
EOF
chmod +x aflpp-cc cmplog-cc
```
The C++ wrappers call `<afl_cc>++`, without `-fno-sanitize=function`.

- `aflpp`: two things produced binaries that crash when run standalone:
  - compiling library objects with `-fsanitize=fuzzer-no-link`;
  - mixing `.c` and `.cc` files in the `-fsanitize=fuzzer` command.

**Instrumentation checks.** Run `build.md`'s check on the library's
objects: AFL++ refuses uninstrumented binaries, but not an uninstrumented
library. The hooks to find:
- `aflpp`: `__asan_`;
- `cmplog`: `__cmplog_ins_hook`;
- with `afl-clang-fast`, `aflpp-plain` and `aflpp` also `sanitizer_cov`.

Check objects, because every binary linked with AFL++'s runtime contains
`__cmplog_ins_hook`, whether or not the library was compiled for CmpLog.

With `afl-clang-lto`, the objects are bitcode, instrumented at the link.
The binary checks:
- `cmplog`'s binary calls the hook:
  `objdump -d <binary> | grep -cE '(call|bl)[[:space:]].*<__cmplog_ins_hook'`
  is above 0 (checked with 5.03c on arm64).
- `aflpp-plain` has no hook, so compare edge counts: the binary's count
  must exceed the harness's alone. To count the harness's edges alone,
  run the command that compiled and linked the harness again
  (`<harness_command>`, without `-o`), with an allowlist naming only the
  harness's file:
  <!-- ci: aflpp-lto-check -->
  ```sh
  A="$F/build/aflpp-plain"; echo "<harness_file>" > "$A/harness-only.list"
  AFL_LLVM_ALLOWLIST="$A/harness-only.list" <harness_command> -o "$A/harness-only"
  echo "edges: library $(AFL_DUMP_MAP_SIZE=1 "$A/<binary>"), harness alone $(AFL_DUMP_MAP_SIZE=1 "$A/harness-only")"
  ```

## Long runs on several cores (Linux default)

Each instance is one copy in the run's `spec.json` (`fuzz-harness`'s
runs.md), with its own findings and queue. The mix depends on the cores
the run gets:

| Cores | Instances |
|---|---|
| 1 | `-M main` on `aflpp`, with `-c <cmplog-binary> -l 2AT` |
| 2 | `-M main` on `aflpp-plain` with `-c <cmplog-binary> -l 2AT`; `-S asan` on `aflpp` |
| 3 or more | `-M main` on `aflpp-plain`; exactly one `-S asan` on `aflpp`; `min(2, N-2)` CmpLog secondaries on `aflpp-plain` with `-c <cmplog-binary>`, one of them with `-l 2AT`; the rest on `aflpp-plain`, varied with `-P explore` or `-P exploit`, power schedules (`-p fast`, `explore`, `coe`, `rare`, ...), `-Z`, `AFL_DISABLE_TRIM=1`, and `-a text` or `-a binary` on a third each |

Write the spec by hand into `runs/<h>-long<n>/spec.json`, and copy the
dictionary next to it. Then hand over `launcher.py start --spec <file>`
(`fuzz-campaign`'s long-runs.md). The spec for 3 cores, with `<b>` the
run's `<tree_build>` (`run.md`, "Long runs"):

<!-- ci: aflpp-spec -->
```json
{
  "id": "<harness>-long<n>", "harness": "<harness>", "trees": ["<tree>"], "time": <run_s>,
  "env": {
    "AFL_NO_UI": "1", "AFL_SKIP_CPUFREQ": "1", "AFL_TMPDIR": "{scratch}/tmp",
    "AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES": "1", "AFL_TRY_AFFINITY": "1",
    "AFL_CRASHING_SEEDS_AS_NEW_CRASH": "1", "AFL_CMPLOG_ONLY_NEW": "1", "AFL_FAST_CAL": "1",
    "ASAN_OPTIONS": "abort_on_error=1:symbolize=0:detect_leaks=0:malloc_context_size=0:max_allocation_size_mb=<memory_mb>",
    "UBSAN_OPTIONS": "halt_on_error=1:abort_on_error=1:symbolize=0",
    "PATH": "<aflpp_bin>:<llvm_bin>:/usr/bin:/bin"
  },
  "copies": [
    {"command": ["afl-fuzz", "-M", "main", "-i", "<workspace>/corpus/<harness>", "-o", "{run}/out", "-m", "<memory_mb>",
                 "-t", "<input_timeout_ms>", "-x", "{run}/<harness>.dict", "-V", "<run_s>", "--", "<b>/aflpp-plain/<binary>"],
     "findings": ["{run}/out/main/crashes", "{run}/out/main/hangs"], "corpus": "{run}/out/main/queue"},
    {"command": ["afl-fuzz", "-S", "asan", "-i", "<workspace>/corpus/<harness>", "-o", "{run}/out", "-m", "none",
                 "-t", "<input_timeout_ms>", "-x", "{run}/<harness>.dict", "-V", "<run_s>", "--", "<b>/aflpp/<binary>"],
     "findings": ["{run}/out/asan/crashes", "{run}/out/asan/hangs"], "corpus": "{run}/out/asan/queue"},
    {"command": ["afl-fuzz", "-S", "cmplog1", "-c", "<b>/cmplog/<binary>", "-l", "2AT", "-i", "<workspace>/corpus/<harness>",
                 "-o", "{run}/out", "-m", "<memory_mb>", "-t", "<input_timeout_ms>", "-x", "{run}/<harness>.dict", "-V", "<run_s>",
                 "--", "<b>/aflpp-plain/<binary>"],
     "findings": ["{run}/out/cmplog1/crashes", "{run}/out/cmplog1/hangs"], "corpus": "{run}/out/cmplog1/queue"}
  ]
}
```

- **Every instance** has the same `-i`, `-o`, `-t` and `-x`.
- **Memory:** `-m <memory_mb>` on instances without ASan. The ASan
  instance gets `-m none`, since its shadow memory needs terabytes of
  address space; `max_allocation_size_mb` in `ASAN_OPTIONS` caps its
  allocations instead.
  - An allocation larger than the cap aborts, because
    `allocator_may_return_null` stays 0. libFuzzer aborts on such an
    allocation too, and reports it as `out-of-memory (malloc(<n>))`.
    With `allocator_may_return_null=1`, the allocation would return
    NULL, unreported.
  - `detect_stack_use_after_return` stays off for speed: the analysis
    merge replays every queue with it on.
- **`AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES`** always. Without it,
  `afl-fuzz` refuses to start in two cases: `core_pattern` pipes crashes
  to a handler (Ubuntu's apport, GitHub's runners), or the macOS crash
  reporter is on. The handler or crash reporter only makes crash handling
  slower.
- **`AFL_TRY_AFFINITY`** always. With it, each instance binds to a free
  core when there is one, and runs unbound otherwise. Without it, an
  instance with no free core aborts with `No more free CPU cores`, e.g.
  when other processes are pinned.
- **Seeds:** the harness's persistent corpus (`corpus/<h>/`). AFL++ needs
  at least one seed: if the corpus is empty, write a 1-byte file first.
  The corpus comes from earlier runs, so `AFL_CMPLOG_ONLY_NEW` and
  `AFL_FAST_CAL` skip CmpLog on it and shorten its calibration.
- **`AFL_CRASHING_SEEDS_AS_NEW_CRASH=1`** writes a seed that crashes to
  `crashes/` as an ordinary finding, named
  `...op:dry_run,...,orig:<seed>`. It does so even when AFL++ then stops
  because every seed crashes.
- **`continuous`** (the default for long runs): AFL++ keeps going past
  crashes, and keeps a crash file per new crashing path. So one bug can
  leave dozens of crash files; triage groups them.
- Secondaries sync only from `main`, and `main` keeps only entries that
  are new on its own (plain) binary. So during the run, the ASan instance
  checks only `main`'s new entries under ASan. The analysis merge replays
  every instance's queue on the libFuzzer ASan build (`run.md`). If an
  input silently corrupts memory on a plain instance and adds no coverage
  there, it is in no queue, so no sanitizer sees it. That's the price of
  the plain instances' speed.
- If the run has no duration: leave out `"time"` and `-V`.
- If the user chose **stop at the first finding** at the long-run
  commands checkpoint, e.g. because a known shallow bug would flood the
  run: add `"AFL_BENCH_UNTIL_CRASH": "1"`. Each instance then ends at its
  own first crash, while the others continue.

## Progress and stopping

- `afl-whatsup -s .seclab/fuzz/runs/<id>/out` summarizes all instances.
- Per instance, `out/<name>/fuzzer_stats` has `execs_per_sec`,
  `corpus_count`, `saved_crashes`, `saved_hangs`, `last_find` (Unix time
  of the last new path), `bitmap_cvg` and `stability`.
- `out/<name>/plot_data` has `edges_found` over time, which analysis
  reads for growth.
- `stability` well below 100% means inputs behave differently on replay:
  check the harness for state that leaks between inputs (gate).
- Stop with `launcher.py stop <id>`.

## Output

Each instance writes `out/<name>/queue/`, `crashes/`, `hangs/`,
`fuzzer_stats` and `plot_data`. Crash files are named like
`id:000000,sig:06,src:000002,time:13,execs:500,op:havoc,rep:2`; skip the
`README.txt` in `crashes/`. The inputs in `hangs/` exceeded `-t`.

## Dictionaries and comparisons

- `-x <file>` takes the same format as libFuzzer.
- `AFL_LLVM_DICT2FILE=<absolute path>`, set while compiling the `aflpp`
  variant, writes string and comparison constants from the code to a
  dictionary. `AFL_LLVM_DICT2FILE_NO_MAIN=1` skips `main()`. Review the
  dictionary, and merge the useful entries into `dict/<h>.dict`
  (`fuzz-harness`'s seeds.md).
- CmpLog (`-c`) solves comparisons with input bytes, as they are or
  simply transformed. This is input-to-state: magic values, lengths,
  tags. A checksum computed over the input needs a fuzzing-only patch
  (`fuzz-coverage`'s diagnosis.md).

## Native AFL++ harnesses

Harnesses with their own `main()` or `__AFL_LOOP` have no tested commands
here. Write an `LLVMFuzzerTestOneInput` harness for the same code: every
recipe here runs it, AFL++ included. The native harness's corpus seeds
the new one. To keep using the native harness, follow AFL++'s docs
(`instrumentation/README.persistent_mode.md`, `afl-tmin`, `afl-cmin`).

## macOS

- AFL++ 5.03c and later use POSIX shared memory, and run without
  `afl-system-config`. Older versions need `afl-system-config`, which
  uses `sudo`.
- The crash reporter slows crash handling; the spec's
  `AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1` lets `afl-fuzz` start anyway.
  Turning the crash reporter off needs `sudo`: print the command from
  `afl-fuzz`'s message for the user.
- Long runs on macOS use libFuzzer copies (`run.md`), even though
  persistent mode is fast there. The reasons: the crash reporter handles
  every crash, and the AFL++ recipes run in CI only on Linux.

## Linux and codespaces

- `AFL_SKIP_CPUFREQ=1`: no root needed for the CPU governor check.
- A piped `/proc/sys/kernel/core_pattern` stops `afl-fuzz` at start
  unless the spec's `AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1` is set.
  Containers can't change `core_pattern`.
- `afl-system-config` needs root: run it only in disposable environments.
  Many of its settings don't apply inside containers.
