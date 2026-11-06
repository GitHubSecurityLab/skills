# libFuzzer tips

Docs: https://llvm.org/docs/LibFuzzer.html. The installed binary lists every
flag with its default: `<fuzzer> -help=1`.

## Flags worth knowing

| Flag | Use it when |
|---|---|
| `-dict=<file>` | a dictionary exists; always |
| `-max_len=<n>` | always: the harness's input cap (`run.md`). Unset, libFuzzer guesses from the corpus and logs `-max_len is not provided`; too small hides bugs that need large inputs |
| `-use_value_profile=1` | coverage plateaus on comparisons with multi-byte constants (magic numbers, lengths, fixed hashes). The corpus grows faster; minimize it afterwards |
| `-only_ascii=1` | text formats where non-ASCII bytes only hit early rejects |
| `-len_control=0` | short inputs can't reach anything interesting; try long ones from the start |
| `-focus_function=<name>` | experimental: bias fuzzing towards inputs that call a function, e.g. a rarely reached sink. It turns off the default entropic schedule, so keep other copies without it |
| `-close_fd_mask=1` | the target logs to stdout on every input (libFuzzer's own output and crash reports stay visible) |
| `-seed=<n>` | reproduce a run's mutations (printed as `INFO: Seed:`) |
| `-print_final_stats=1` | always; gives executions per second for the gate |
| `-set_cover_merge=1` | corpus minimization (`-merge=1` keeps more inputs for the same features); add `-merge_control_file=<f>` to resume an interrupted merge |
| `-minimize_crash=1 -runs=<n>` | crash minimization |

Parallel runs are N separate copies sharing a corpus directory (`run.md`
next to this file), not `-fork` or `-jobs`/`-workers`: those share one
scratch directory, write `fuzz-<n>.log` files, and `-fork`'s exit status
differs between macOS and Linux.

## Reading the output

- `cov:` counts covered edges; `ft:` counts finer features (edge hit
  counts, value profiles). `ft:` growing without `cov:` still means
  progress.
- `NEW` lines add corpus entries; `REDUCE` replaces an entry with a smaller
  one; `pulse` lines only show that it's alive. Only `pulse` lines for a
  long stretch means a plateau.
- `MS: <n> <mutations>` names the mutations that produced an entry. Mostly
  `CMP`/`DE`/`PersAutoDict` entries mean the fuzzer is solving
  comparisons; mostly `CopyPart`/`CrossOver` means it's recombining.

## Tips

- Slow first inputs: `-timeout` applies per input; one huge seed can
  dominate a short round. Keep seeds small.
- `LLVMFuzzerInitialize` runs once per process, also in `-merge`
  subprocesses.
- Status messages go to stderr; capture both streams in the run log.
