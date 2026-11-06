---
name: fuzz-c-cpp
description: >-
  C/C++ fuzzing reference for libFuzzer, AFL++ and FuzzTest: harness
  formats, builds, runs, coverage, triage and regression commands. Not a
  starting point: use fuzz-harness or fuzz-campaign.
license: MIT
---

# Fuzzing C and C++

One `LLVMFuzzerTestOneInput` harness serves libFuzzer
and AFL++, so switching engines needs no harness changes. When unsure
of an option, check the installed tool first
(`<fuzzer> -help=1`, `afl-fuzz -hh`, `<fuzztest binary> --helpfull`), then
the linked docs.

## When to use

- A workflow skill needs a C/C++ command.
- Questions such as "how do I run AFL++ on all cores?"

Not for deciding what to do next; that's the workflow skills.

## Reads

- `targets/<id>.md`: Harness (format, engine, build variants).
- `CAMPAIGN.md`: Settings (`memory_mb`, `input_timeout_s`).

## Steps

1. **Choose the harness format and engine:** the first row that matches
   the project wins.

   | The project has | Format | Develop runs | Long runs |
   |---|---|---|---|
   | `LLVMFuzzerTestOneInput` harnesses (in-tree or in its OSS-Fuzz folder) | keep it | libFuzzer | AFL++ on Linux (when installed), libFuzzer copies otherwise and on macOS |
   | `FUZZ_TEST`s | keep them | FuzzTest | FuzzTest |
   | Only native AFL++ harnesses (`__AFL_LOOP`, or a `main()` reading a file) | `LLVMFuzzerTestOneInput` for the same code (`aflpp.md`) | libFuzzer | as above |
   | No harnesses, and the user names an engine | that engine's | that engine | that engine |
   | No harnesses, GoogleTest with Bazel or CMake | `FUZZ_TEST` | FuzzTest | FuzzTest |
   | No harnesses | `LLVMFuzzerTestOneInput` | libFuzzer | as above |

   - If the user names an engine that the existing format supports: only
     the engine changes, e.g. "use AFL++" on `LLVMFuzzerTestOneInput`
     harnesses keeps them and runs AFL++ on them.

2. **Check the toolchain** with
   [references/install.md](references/install.md) and propose installs for
   what's missing. Record the versions in the target file.
3. **Look up the command** for the step in its reference:

   | Step | Reference |
   |---|---|
   | Write or extend a harness | [references/harness.md](references/harness.md); a stateful API: [references/call-sequences.md](references/call-sequences.md) |
   | Build variants, build-system integration, instrumentation checks | [references/build.md](references/build.md) |
   | Develop and long runs, environment, corpus merge | [references/run.md](references/run.md) |
   | Coverage replay to lcov | [references/coverage.md](references/coverage.md) |
   | Reproduce, minimize, debug | [references/triage.md](references/triage.md) |
   | Regression tests | [references/regression.md](references/regression.md) |
   | Engine tips | [libfuzzer.md](references/libfuzzer.md), [aflpp.md](references/aflpp.md), [fuzztest.md](references/fuzztest.md) |

## Tested versions (checked 2026-10-27)

| Tool | Version | How it was checked |
|---|---|---|
| clang, libFuzzer, sanitizers | Homebrew LLVM 23.1; Ubuntu 24.04 LLVM 20 | ran on macOS arm64 and Linux arm64 |
| AFL++ | 5.03c (Homebrew; built with LLVM 20 on Linux) | ran on macOS arm64 and Linux arm64 |
| FuzzTest | tag `2026-06-29` (Bazel module `20260629.0`) | CMake build, corpus, UBSan and replay ran on macOS arm64 (Homebrew LLVM 23, `--rss_limit_mb=0`) and Linux arm64 (clang 20); not yet run in a campaign |

Install the engine versions in this table (AFL++, FuzzTest) unless the
project already pins one; a newer release only when the user asks, after
checking its license. Compilers come from the package manager
(`install.md`). Record the installed versions; say so when they differ
from this table. AFL++ is
AGPL-3.0-or-later since 5.00c: invoke installed copies only, never copy its
code or commit files it generates.

## Engine output locations in the project tree

For the gate's working-tree comparison: none, when every run goes through
the launcher with the paths in [references/run.md](references/run.md).
Watch for:
- libFuzzer crash files in the working directory when `-artifact_prefix`
  is missing;
- `default.profraw` when `LLVM_PROFILE_FILE` isn't set;
- FuzzTest's reproducers, unless `FUZZTEST_REPRODUCERS_OUT_DIR` is set
  (CMake builds never write a corpus database; only Bazel's Centipede
  configuration does);
- Bazel's `bazel-*` symlinks at the workspace root.

## Done when

- The step's command ran with the flags from the reference, and its check
  passed (e.g. the instrumentation check before a gate).

## Pitfalls and shortcuts to reject

- On macOS with `detect_leaks=1`, libFuzzer reports a false leak in its RSS
  thread at exit. Fuzz with leaks off there; replaying a leak there:
  [references/triage.md](references/triage.md), "Reproduce".
- libFuzzer `-fork` or `-jobs` for parallel runs: they share one scratch
  directory, and `-fork`'s exit status differs between macOS and Linux.
  Run N copies through the launcher instead.
- A merge's or replay's exit status as a crash signal: libFuzzer's merges
  exit 0 after crashes. Read the launcher's findings.
- One `ASAN_OPTIONS` for both engines: AFL++ needs `symbolize=0`, libFuzzer
  wants `symbolize=1`.
- Building with `NDEBUG` (CMake's Release and RelWithDebInfo): assertions
  vanish and with them `assertion_failure` findings.

## Writes

- `targets/<id>.md`: tool versions, build commands (through the calling
  skill).

## Next

- Back to the workflow skill that asked.
