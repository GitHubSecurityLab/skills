# Running C/C++ fuzzers

Every run goes through `launcher.py` (`fuzz-harness`'s runs.md explains `F`,
`L` and placeholders like `{run}`), and every command starts with
`. .seclab/fuzz/env.sh; H=<harness>;`. Build before the run, and run the
built binary directly. Never `bazel run` through the launcher: Bazel's
server and cache live in the real `HOME`.

## Settings to flags

| Setting | libFuzzer | AFL++ | FuzzTest |
|---|---|---|---|
| `memory_mb` | `-rss_limit_mb=<m>` (single allocations get the same limit) | `-m <m>` without ASan; `-m none` with ASan, plus `max_allocation_size_mb=<m>` in `ASAN_OPTIONS` | `--rss_limit_mb=<m>` (macOS: `0`, `fuzztest.md`) |
| `input_timeout_s` | `-timeout=<s>` | `-t <s × 1000>` (milliseconds) | `--time_limit_per_input=<s>s` |
| run time (the launcher's `--time`) | `-max_total_time=<s>` | `-V <s>` | `--fuzz_for=<s>s` |

- If the run has no duration: leave out `--time` (a spec's `"time"`) and
  the engine's time limit. The engines then run until stopped.
- If AFL++ runs ASan targets: AFL++ doesn't cap their total memory, so
  count the instances' memory against the machine's.

## Environment (`--env`)

For libFuzzer and FuzzTest, fill in this block and add it to
`.seclab/fuzz/env.sh`, below `F` and `L`. The commands below pass its
variables with `--env`:
<!-- ci: c-env -->
```sh
ASAN_OPTIONS=allocator_may_return_null=1:detect_leaks=<detect_leaks>:detect_stack_use_after_return=1:handle_abort=1:print_scariness=1:symbolize=1
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1:print_summary=1:symbolize=1
PATH=<llvm_bin>:$PATH
RUN_PATH=<llvm_bin>:/usr/bin:/bin
```
- `<detect_leaks>`: `1` on Linux, `0` on macOS. It's also `0` in a
  sandbox that blocks `ptrace` (Codex's default sandbox): LeakSanitizer
  stops there with "LeakSanitizer has encountered a fatal error".
- `<llvm_bin>`: the LLVM `bin` directory (`install.md`, "Check without
  running project code"). Runs need it for `llvm-symbolizer`, and the
  shell for `llvm-profdata` and `llvm-cov`.
- The shell keeps its own `PATH` behind the LLVM directory, for build
  tools (CMake, Ninja, Meson). Runs get only `RUN_PATH`.
- If a harness has LeakSanitizer suppressions (`fuzz-triage`): its
  commands also get `--env LSAN_OPTIONS=suppressions="$F/leaks/$H.supp"`.
  A long run passes a copy of the file in its run directory instead.
  Reproductions run without the suppressions.
- If the engine is AFL++: AFL++ parses unsymbolized reports itself and
  has its own variables. They're in the run-level `env` of the spec in
  `aflpp.md` next to this file.

## Develop runs (gate, rounds)

A develop run is one libFuzzer process, which writes new inputs into the
persistent corpus. `<max_len>` is the harness's input cap (target file,
Harness; `harness.md` next to this file). Always pass it: without it,
libFuzzer guesses `-max_len` from the corpus. A few imported 1 MiB files
then make every input up to 1 MiB long, and small seeds cap inputs at
4 KiB.

<!-- ci: c-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" \
  --env ASAN_OPTIONS="$ASAN_OPTIONS" --env UBSAN_OPTIONS="$UBSAN_OPTIONS" --env PATH="$RUN_PATH" -- \
  "$F/build/libfuzzer/<binary>" -max_total_time=<run_s> -rss_limit_mb=<memory_mb> -max_len=<max_len> \
  -timeout=<input_timeout_s> -print_final_stats=1 -artifact_prefix="{run}/artifacts/copy-{copy}/" \
  -dict="$F/dict/$H.dict" "$F/corpus/$H"
```

- New inputs go into the first corpus directory.
- If there are extra seed directories: they go after the first, and
  libFuzzer only reads them.
- The run stops at the first finding (`crash-*`, `leak-*`, `timeout-*`,
  `oom-*`). The next round resumes from the corpus.
- Status lines read `#<n> NEW|REDUCE|pulse|DONE cov: <edges>
  ft: <features> corp: <inputs>/<bytes> exec/s: <n> rss: <mb>`. The final
  statistics (`-print_final_stats=1`) include
  `stat::average_exec_per_sec`, `stat::new_units_added` and
  `stat::peak_rss_mb`.

## Long runs

The engine depends on the system and the harness:

| Long runs on | Engine |
|---|---|
| Linux, AFL++ installed | AFL++, the mix in `aflpp.md` next to this file |
| macOS; Linux without AFL++; a harness with a libFuzzer custom mutator | libFuzzer, N copies |
| FuzzTest | N copies of the fuzz test (`fuzztest.md`) |

`<tree>` is the run's worktree, and `<tree_build>` its
`.seclab/fuzz/build` directory (`fuzz-campaign`). If the project builds
only in-tree: `build.md`, "In-tree builds".

**libFuzzer, N copies:** list the run's own corpus first and the
persistent corpus second. Every copy writes new inputs into the run's
corpus and re-reads them from there; the persistent corpus is only
read.

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

- Copies share inputs through libFuzzer's `-reload`, which is on by
  default and reloads about once a second. Each copy picks its own random
  seed (no `-seed`).
- Every third copy gets `-use_value_profile=1`, from the block's second
  command. The flag rewards getting closer on comparisons, so those
  copies search differently. The analysis merge keeps their inputs only
  where they add coverage.
- Each copy stops at its first finding; the others continue.
- No `-fork` or `-jobs`/`-workers`: they share one scratch directory,
  write `fuzz-<n>.log` files, and `-fork`'s exit status differs between
  macOS and Linux.
- If the run has no duration: "Settings to flags", above.

## Merging a run's corpus back (analysis, `fuzz-campaign`)

Merge on the **current** libFuzzer ASan+UBSan build, into the merge run's
own corpus. That corpus then replaces the persistent corpus
(`fuzz-campaign`). `-set_cover_merge=1` keeps the same features in fewer
inputs than `-merge=1`:

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
- If there's no long run (minimizing the persistent corpus alone): use
  the same command, without the long run's directory.
- If the long run was AFL++: list every instance's queue instead of the
  long run's `corpus`. The ASan build catches inputs that crash only
  under ASan:
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
