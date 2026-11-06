# Running C/C++ fuzzers

Every run goes through `launcher.py` (`fuzz-harness`'s runs.md). An
agent's commands don't keep shell variables, so every command starts with
`. .seclab/fuzz/env.sh; H=<harness>;`: the file sets `F` (the absolute
path of `.seclab/fuzz`), `L` (`python3 $F/bin/launcher.py`) and the
environment below. `{run}`, `{scratch}` and `{copy}` are filled in by the
launcher. Commands need absolute paths. Build before the run and run the
built binary directly; never `bazel run` through the launcher (Bazel's
server and cache live in the real `HOME`).

## Settings to flags

| Setting | libFuzzer | AFL++ | FuzzTest |
|---|---|---|---|
| `memory_mb` | `-rss_limit_mb=<m>` (single allocations get the same limit) | `-m <m>` without ASan; `-m none` with ASan, plus `max_allocation_size_mb=<m>` in `ASAN_OPTIONS` | `--rss_limit_mb=<m>` (macOS: `0`, `fuzztest.md`) |
| `input_timeout_s` | `-timeout=<s>` | `-t <s × 1000>` (milliseconds) | `--time_limit_per_input=<s>s` |
| run time (the launcher's `--time`) | `-max_total_time=<s>` | `-V <s>` | `--fuzz_for=<s>s` |

- If the run has no duration: leave the engine's time limit out; they run
  until stopped.
- If AFL++ runs ASan targets: it doesn't cap their total memory; count the
  memory of the instances against the machine's.

## Environment (`--env`)

libFuzzer and FuzzTest: add this block, filled in, to
`.seclab/fuzz/env.sh` below `F` and `L`; the commands below pass it with
`--env`:
<!-- ci: c-env -->
```sh
ASAN_OPTIONS=allocator_may_return_null=1:detect_leaks=<detect_leaks>:detect_stack_use_after_return=1:handle_abort=1:print_scariness=1:symbolize=1
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1:print_summary=1:symbolize=1
PATH=<llvm_bin>:$PATH
RUN_PATH=<llvm_bin>:/usr/bin:/bin
```
- `<detect_leaks>`: `1` on Linux, `0` on macOS, and `0` in a sandbox that
  blocks `ptrace` (Codex's default sandbox), where LeakSanitizer stops with
  "LeakSanitizer has encountered a fatal error". `<llvm_bin>`: the LLVM
  `bin` directory, for `llvm-symbolizer` in runs and `llvm-profdata` and
  `llvm-cov` in the shell (macOS: `/opt/homebrew/opt/llvm/bin`).
- The shell keeps its own `PATH` behind the LLVM directory, for build
  tools (CMake, Ninja, Meson); runs get only `RUN_PATH`.
- If a harness has LeakSanitizer suppressions (`fuzz-triage`): it also
  gets `--env LSAN_OPTIONS=suppressions="$F/leaks/$H.supp"` (long runs: a
  copy in the run directory). Reproductions run without it.
- If the engine is AFL++: it parses unsymbolized reports itself and has its
  own variables: the run-level `env` of the spec in `aflpp.md` next to this
  file.

## Develop runs (gate, rounds)

One libFuzzer process, writing new inputs into the persistent corpus.
`<max_len>` is the harness's input cap (target file, Harness; `harness.md`
next to this file); always pass it:

<!-- ci: c-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "$F/build/libfuzzer/<binary>" -max_total_time=<run_s> -rss_limit_mb=<memory_mb> -max_len=<max_len> \
  -timeout=<input_timeout_s> -print_final_stats=1 -artifact_prefix="{run}/artifacts/copy-{copy}/" \
  -dict="$F/dict/$H.dict" "$F/corpus/$H"
```

Unset, libFuzzer guesses `-max_len` from the corpus, so a few imported
1 MiB files make every input up to 1 MiB long, and small seeds cap inputs
at 4 KiB.

- New inputs go into the first directory.
- The run stops at the first finding (`crash-*`, `leak-*`, `timeout-*`,
  `oom-*`); the next round resumes from the corpus.
- Status lines `#<n> NEW|REDUCE|pulse|DONE cov: <edges> ft: <features>
  corp: <inputs>/<bytes> exec/s: <n> rss: <mb>`. Final statistics
  (`-print_final_stats=1`): `stat::average_exec_per_sec`,
  `stat::new_units_added`, `stat::peak_rss_mb`.
- If there are extra seed directories: they go after the first; they're
  only read.

## Long runs

The engine depends on the system and the harness:

| Long runs on | Engine |
|---|---|
| Linux, AFL++ installed | AFL++, the mix in `aflpp.md` next to this file |
| macOS; Linux without AFL++; a harness with a libFuzzer custom mutator | libFuzzer, N copies |
| FuzzTest | N copies of the fuzz test (`fuzztest.md`) |

**libFuzzer, N copies:** the run's own corpus first (where every copy
writes and re-reads new inputs), the persistent corpus second (read only):

<!-- ci: c-long -->
```sh
$L start --prepare --id $H-long<n> --harness $H --copies <copies> --time <run_s> --tree "<tree>" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "<tree_build>/libfuzzer/<binary>" -max_total_time=<run_s> -rss_limit_mb=<memory_mb> -max_len=<max_len> \
  -timeout=<input_timeout_s> -print_final_stats=1 -artifact_prefix="{run}/artifacts/copy-{copy}/" \
  -dict="{run}/$H.dict" "{run}/corpus" "$F/corpus/$H"
python3 -c 'import json, sys; s = json.load(open(sys.argv[1]))
for c in s["copies"][2::3]: c["command"].insert(1, "-use_value_profile=1")
json.dump(s, open(sys.argv[1], "w"), indent=1)' "$F/runs/$H-long<n>/spec.json"
```

`<tree>` is the run's worktree, and `<tree_build>` its `.seclab/fuzz/build`
directory (`fuzz-campaign`).

- Copies share inputs through libFuzzer's `-reload` (on by default, about
  once a second), and each picks its own random seed (no `-seed`).
- Every third copy gets `-use_value_profile=1` (the second command): it
  rewards getting closer on comparisons, so those copies search
  differently. The analysis merge keeps their inputs only where they add
  coverage.
- Each copy stops at its first finding; the others continue.
- No `-fork` or `-jobs`: they share one scratch directory, and `-fork`'s
  exit status differs between macOS and Linux.
- If the run has no duration: drop `-max_total_time`.

## Merging a run's corpus back (analysis, `fuzz-campaign`)

On the **current** libFuzzer ASan+UBSan build, into the merge run's own
corpus, which then replaces the persistent corpus (`fuzz-campaign`).
`-set_cover_merge=1` keeps the same features in fewer inputs than
`-merge=1`:

<!-- ci: c-merge -->
```sh
$L start --id merge-$H-<n> --harness $H --timeout <timeout_s> \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "$F/build/libfuzzer/<binary>" -set_cover_merge=1 -rss_limit_mb=<memory_mb> -max_len=<max_len> \
  -timeout=<input_timeout_s> -artifact_prefix="{run}/artifacts/copy-{copy}/" \
  "{run}/corpus" "$F/corpus/$H" "$F/runs/<long_run>/corpus"
```

- A crashing input is recorded as a finding and left out of the result.
- The merge exits 0 even after crashes: read the result line's findings.
- If there's no long run (minimizing the persistent corpus alone): the
  same command, without the long run's directory.
- If the long run was AFL++: list every instance's queue instead of its
  `corpus`. The ASan build catches inputs that crash only under ASan:
  <!-- ci: aflpp-merge -->
  ```sh
  $L start --id merge-$H-<n> --harness $H --timeout <timeout_s> \
    --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
    "$F/build/libfuzzer/<binary>" -set_cover_merge=1 -rss_limit_mb=<memory_mb> -max_len=<max_len> \
    -timeout=<input_timeout_s> -artifact_prefix="{run}/artifacts/copy-{copy}/" \
    "{run}/corpus" "$F/corpus/$H" "$F/runs/<long_run>"/out/*/queue
  ```

## Checking a run

<!-- ci: c-check-run -->
```sh
$L status <id>                                          # states, findings, status lines
tail -n 3 .seclab/fuzz/runs/<id>/copy-0/log             # libFuzzer status
afl-whatsup -s .seclab/fuzz/runs/<id>/out               # AFL++ summary while it runs
```
