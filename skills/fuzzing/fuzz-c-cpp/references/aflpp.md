# AFL++ tips

Docs: https://github.com/AFLplusplus/AFLplusplus/tree/stable/docs, mainly
`fuzzing_in_depth.md`. Options of the installed version: `afl-fuzz -hh`;
environment variables: `docs/env_variables.md`.

License: AGPL-3.0-or-later since 5.00c. Invoke installed copies only; never
copy its code. Harness-side files such as the libFuzzer driver keep their
Apache-2.0 headers.

## Install

- **Linux:** a pinned release at user level, with the packages from
  `install.md`: `git clone --depth 1 --branch v5.03c https://github.com/AFLplusplus/AFLplusplus`,
  then `LLVM_CONFIG=llvm-config-20 make all PREFIX=$HOME/.local` and
  `make install PREFIX=$HOME/.local` (the prefix is compiled in). `make all`
  skips LLVM mode when it fails to build: check that `afl-clang-fast` and
  `afl-clang-lto` exist. Ubuntu's `afl++` package is outdated.
- **macOS:** `brew install afl++` (uses Homebrew LLVM). 5.03c changed shared
  memory on macOS: rebuild targets built with older versions.

## Builds

Three variants, all from the libFuzzer harness: `aflpp-plain` (no
sanitizer), `aflpp` (ASan+UBSan) and `cmplog` (flags: `build.md` next to
this file). `AFL_USE_ASAN` and `AFL_LLVM_CMPLOG` are read at compile time, so a
rebuild without them in the environment silently drops the sanitizers or
CmpLog. Put them, and the UBSan flags (`$UB` and `$HARDEN` from env.sh,
written out), in a wrapper per variant. `<afl_cc>` is `afl-clang-lto`
(link-time instrumentation, the best coverage) where it builds: it needs
`lld` of the same LLVM version and `AR=llvm-ar RANLIB=llvm-ranlib`.
Otherwise, and on macOS, it's `afl-clang-fast`.

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

- `aflpp`: compiling library objects with `-fsanitize=fuzzer-no-link`, or
  mixing `.c` and `.cc` files in the `-fsanitize=fuzzer` command, produced
  binaries that crash when run standalone.

**Instrumentation checks.** Run `build.md`'s check on the library's objects:
AFL++ refuses uninstrumented binaries, but not an uninstrumented library.
`aflpp`: `__asan_`; `cmplog`: `__cmplog_ins_hook`; with `afl-clang-fast`,
`aflpp-plain` and `aflpp` also `sanitizer_cov`. Check objects, because
every binary linked with AFL++'s runtime contains `__cmplog_ins_hook`
whether or not the library was compiled for CmpLog. With `afl-clang-lto`,
objects are bitcode, instrumented at the link. `cmplog`'s binary calls the
hook: `objdump -d <binary> | grep -cE '(call|bl)[[:space:]].*<__cmplog_ins_hook'`
above 0 (checked with 5.03c on arm64). `aflpp-plain` has no hook: its edge
count must exceed the harness's alone, from its harness command run again
(`<harness_command>`, without `-o`) with an allowlist naming only the
harness's file:
<!-- ci: aflpp-lto-check -->
```sh
A="$F/build/aflpp-plain"; echo "<harness_file>" > "$A/harness-only.list"
AFL_LLVM_ALLOWLIST="$A/harness-only.list" <harness_command> -o "$A/harness-only"
echo "edges: library $(AFL_DUMP_MAP_SIZE=1 "$A/<binary>"), harness alone $(AFL_DUMP_MAP_SIZE=1 "$A/harness-only")"
```

## Long runs on several cores (Linux default)

Each instance is one copy in the run's `spec.json` (`fuzz-harness`'s runs.md), with its own
findings and queue. The mix depends on the cores the run gets:

| Cores | Instances |
|---|---|
| 1 | `-M main` on `aflpp`, with `-c <cmplog-binary> -l 2AT` |
| 2 | `-M main` on `aflpp-plain` with `-c <cmplog-binary> -l 2AT`; `-S asan` on `aflpp` |
| 3 or more | `-M main` on `aflpp-plain`; exactly one `-S asan` on `aflpp`; `min(2, N-2)` CmpLog secondaries on `aflpp-plain` with `-c <cmplog-binary>`, one of them with `-l 2AT`; the rest on `aflpp-plain`, varied with `-P explore` or `-P exploit`, power schedules (`-p fast`, `explore`, `coe`, `rare`, ...), `-Z`, `AFL_DISABLE_TRIM=1`, and `-a text` or `-a binary` on a third each |

Write the spec by hand into `runs/<h>-long<n>/spec.json`, copy the
dictionary next to it, and hand over `launcher.py start --spec <file>`
(`fuzz-campaign`'s long-runs.md). For 3 cores, with `<b>` the tree's `.seclab/fuzz/build`:

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
- **Memory:** `-m <memory_mb>` on instances without ASan; `-m none` on the
  ASan instance, whose shadow memory needs terabytes of address space, so
  `max_allocation_size_mb` in `ASAN_OPTIONS` caps its allocations instead.
  A larger one aborts (`allocator_may_return_null` stays 0), as libFuzzer
  reports it (`out-of-memory (malloc(<n>))`); with 1 it returns NULL,
  unreported. `detect_stack_use_after_return` stays off for speed: the
  analysis merge replays every queue with it.
- **`AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES`** always: without it,
  `afl-fuzz` refuses to start where `core_pattern` pipes crashes to a
  handler (Ubuntu's apport, GitHub's runners) or the macOS crash reporter
  is on. It only makes crash handling slower.
- **`AFL_TRY_AFFINITY`** always: each instance binds to a free core when
  there is one and runs unbound otherwise, instead of aborting with
  `No more free CPU cores` (e.g. when other processes are pinned).
- **Seeds:** the harness's persistent corpus (`corpus/<h>/`). AFL++ needs at
  least one seed: if the corpus is empty, write a 1-byte file first. It
  comes from earlier runs, so `AFL_CMPLOG_ONLY_NEW` and `AFL_FAST_CAL`
  skip CmpLog on it and shorten its calibration.
- **`AFL_CRASHING_SEEDS_AS_NEW_CRASH=1`** writes a seed that crashes to
  `crashes/` (`...op:dry_run,...,orig:<seed>`) as an ordinary finding, even
  when AFL++ then stops because every seed crashes.
- **`continuous`** (the default for long runs): AFL++ keeps going past
  crashes and keeps a crash file per new crashing path, so one bug can
  leave dozens; triage groups them.
- Secondaries sync only from `main`, and `main` keeps only entries that are
  new on its own (plain) binary. So during the run, the ASan instance
  checks only `main`'s new entries under ASan. The analysis merge replays
  every instance's queue on the libFuzzer ASan build (`run.md`). An input
  that corrupts memory silently on a plain instance without new coverage
  is in no queue, so no sanitizer sees it: the price of their speed.
- If the run has no duration: leave out `"time"` and `-V`.
- If the user chose **stop at the first finding** (at the long-run
  commands checkpoint, e.g. while a known shallow bug would flood the
  run): add `"AFL_BENCH_UNTIL_CRASH": "1"`, so each instance ends at its
  own first crash while the others continue.

## Progress and stopping

- `afl-whatsup -s .seclab/fuzz/runs/<id>/out` summarizes all instances.
- Per instance, `out/<name>/fuzzer_stats`: `execs_per_sec`, `corpus_count`,
  `saved_crashes`, `saved_hangs`, `last_find` (Unix time of the last new
  path), `bitmap_cvg`, `stability`; `out/<name>/plot_data` has
  `edges_found` over time, which analysis reads for growth.
- `stability` well below 100% means inputs behave differently on replay:
  check the harness for state that leaks between inputs (gate).
- Stop with `launcher.py stop <id>`.

## Output

`out/<name>/queue/`, `crashes/`, `hangs/`, `fuzzer_stats`, `plot_data`.
Crash files are named like `id:000000,sig:06,src:000002,time:13,execs:500,op:havoc,rep:2`;
skip the `README.txt` in `crashes/`. Hangs exceeded `-t`.

## Dictionaries and comparisons

- `-x <file>` takes the same format as libFuzzer.
- `AFL_LLVM_DICT2FILE=<absolute path>` while compiling the `aflpp` variant
  writes string and comparison constants from the code to a dictionary;
  `AFL_LLVM_DICT2FILE_NO_MAIN=1` skips `main()`. Review it and merge the
  useful entries into `dict/<h>.dict` (`fuzz-harness`'s seeds.md).
- CmpLog (`-c`) solves comparisons with input bytes, as they are or
  simply transformed (input-to-state: magic values, lengths, tags); a
  checksum computed over the input needs a fuzzing-only patch
  (`fuzz-coverage`'s diagnosis.md).

## Native AFL++ harnesses

Harnesses with their own `main()` or `__AFL_LOOP` have no tested commands
here: write an `LLVMFuzzerTestOneInput` harness for the same code (its
corpus seeds the new one), which every recipe here runs, AFL++ included.
To keep using the native one, follow AFL++'s docs
(`instrumentation/README.persistent_mode.md`, `afl-tmin`, `afl-cmin`).

## macOS

- AFL++ 5.03c and later use POSIX shared memory and run without
  `afl-system-config`; older versions need it (it uses `sudo`).
- The crash reporter slows crash handling; the spec's
  `AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1` lets `afl-fuzz` start anyway.
  Turning it off needs `sudo`: print the command from `afl-fuzz`'s
  message for the user.
- Long runs on macOS use libFuzzer copies (`run.md`), though persistent
  mode is fast there: the crash reporter handles every crash, and the
  AFL++ recipes run in CI only on Linux.

## Linux and codespaces

- `AFL_SKIP_CPUFREQ=1`: no root needed for the CPU governor check.
- A piped `/proc/sys/kernel/core_pattern` stops `afl-fuzz` at start
  unless the spec's `AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1` is set.
  Containers can't change it.
- `afl-system-config` needs root; only in disposable environments, and
  many of its settings don't apply inside containers.
