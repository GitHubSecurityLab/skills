# Running Jazzer.js

Every run goes through `launcher.py` (`fuzz-harness`'s runs.md). An
agent's commands don't keep shell variables, so every command starts with
`. .seclab/fuzz/env.sh; H=<harness>;`: the file sets `F` (the absolute
path of `.seclab/fuzz`), `L` (`python3 $F/bin/launcher.py`) and the
environment below. `{run}`, `{scratch}` and `{copy}` are filled in by the
launcher.

## Settings to options

| Setting | Option |
|---|---|
| `memory_mb` | `-rss_limit_mb=<m>` after `--` |
| `input_timeout_s` | `--timeout=<s × 1000>` (milliseconds) |
| run time (the launcher's `--time`) | `-max_total_time=<s>` after `--` |

The libFuzzer `-timeout` flag is ignored; use Jazzer.js's `timeout`. If
the run has no duration: leave `-max_total_time` out.

## Environment (`--env`)

Add this block, filled in, to `.seclab/fuzz/env.sh` below `F` and `L`.
The shell keeps its own `PATH` behind Node's; the commands below pass
`RUN_PATH` as the run's `PATH`:
<!-- ci: js-env -->
```sh
PATH=<node_bin>:$PATH
RUN_PATH=<node_bin>:/usr/bin:/bin
P=<package_dir>
```

`<package_dir>` is the absolute path of the package directory in the
checkout (a long run's is in its worktree, below). Call the project's
binary directly (`$P/node_modules/.bin/jazzer`).
- If `npx` or `npm` run: pass `npm_config_cache=<real home>/.npm` too.
- If the package is in an npm workspace: the binary is usually in the
  root's `node_modules/.bin` instead;
  `npm exec -w <pkg> -c 'command -v jazzer'` prints the path to use.

## CLI target

**Synchronous targets** (no promise returned, the common case) run with
`--sync`, 5 to 50 times faster; the commands below have it. Drop it for an
async target, and record which in the target file (Harness). The gate's log
tells: Jazzer.js ends an async-mode run of a synchronous target with
"Fuzzing in synchronous mode seems beneficial!".

**Develop runs:** one process, writing into the persistent corpus.

<!-- ci: js-cli-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "$F/corpus/$H" --timeout=<input_timeout_ms> --sync \
  -- -max_total_time=<run_s> -rss_limit_mb=<memory_mb> -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="$F/dict/$H.dict"
```

- Corpus directories come after the target; new inputs go into the first.
- Exit status: 77 for a finding, 70 for a timeout, 0 when the time ran out.
- **Comparisons:** compare hooks pass libFuzzer the literal of a
  comparison with a string or number literal (`===`, `!==`, ...) and the
  cases of a `switch` on a variable; others (a named constant,
  `startsWith`, `includes`, a `Set`) need the dictionary. After a plateau
  on comparisons of computed values, add `-use_value_profile=1` after
  `--`.

**Long runs:** N copies, with the run's own corpus first (where every copy
writes and re-reads new inputs) and the persistent corpus second. An async
target's copy uses about two cores: give its long runs half as many
copies. Each copy stops at its first finding; the others continue. A known
bug without a fix would stop them again and again: the harness filters its
inputs first (`fuzz-triage`). `<tree>` is the run's worktree, and
`<tree_package>` the package directory in it (`fuzz-campaign`):

<!-- ci: js-cli-long -->
```sh
$L start --prepare --id $H-long<n> --harness $H --copies <copies> --time <run_s> --tree "<tree>" --env PATH="$RUN_PATH" -- \
  "<tree_package>/node_modules/.bin/jazzer" "<tree_package>/<target_file>" "{run}/corpus" "$F/corpus/$H" \
  --timeout=<input_timeout_ms> --sync -- -max_total_time=<run_s> -rss_limit_mb=<memory_mb> -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="{run}/$H.dict"
```

- If the run has no duration: drop `--time` and `-max_total_time`.

No `-fork` or `-jobs`: the jobs share one scratch directory and the same
seed.

## Merging a run's corpus back (analysis, `fuzz-campaign`)

`-merge=1` on the **current** code, into the merge run's own corpus, which
then replaces the persistent corpus (`fuzz-campaign`):
<!-- ci: js-cli-merge -->
```sh
$L start --id merge-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "{run}/corpus" "$F/corpus/$H" \
  "$F/runs/<long_run>/corpus" --timeout=<input_timeout_ms> --sync \
  -- -merge=1 -rss_limit_mb=<memory_mb> -artifact_prefix="{run}/artifacts/copy-{copy}/"
```
It records crashing inputs as findings and keeps them in the result.
Check `MERGE-OUTER: <n> new files` in the log.

## Checking a run

<!-- ci: js-check-run -->
```sh
$L status <id>                                          # states, findings, status lines
tail -n 3 .seclab/fuzz/runs/<id>/copy-0/log             # libFuzzer status
```
