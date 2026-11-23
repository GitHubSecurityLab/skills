# Running Jazzer.js

Jest `it.fuzz` tests: follow `jest.md`, "Runs", instead of the settings,
CLI target and merging sections below. The environment and the run check
apply to both formats.

Every run goes through `launcher.py` (`fuzz-harness`'s runs.md explains `F`,
`L` and placeholders like `{run}`), and every command starts with
`. .seclab/fuzz/env.sh; H=<harness>;`.

## Settings to options

| Setting | Option |
|---|---|
| `memory_mb` | `-rss_limit_mb=<m>` after `--` |
| `input_timeout_s` | `--timeout=<s × 1000>` (milliseconds) |
| run time (the launcher's `--time`) | `-max_total_time=<s>` after `--` |

The libFuzzer `-timeout` flag is ignored; use Jazzer.js's `timeout`.

## Environment (`--env`)

Fill in this block and add it to `.seclab/fuzz/env.sh`, below `F` and
`L`. The shell keeps its own `PATH` behind Node's; the commands below pass
`RUN_PATH` as the run's `PATH`:
<!-- ci: js-env -->
```sh
PATH=<node_bin>:$PATH
RUN_PATH=<node_bin>:/usr/bin:/bin
P=<package_dir>
```

`<package_dir>` is the absolute path of the package directory in the
checkout. A long run's package directory is in its worktree ("Long runs",
below). Call the project's binary directly
(`$P/node_modules/.bin/jazzer`).
- If `npx` or `npm` run: pass `npm_config_cache=<real home>/.npm`
  too.
- If the package is in an npm workspace: the binary is usually in the
  root's `node_modules/.bin` instead.
  `npm exec -w <pkg> -c 'command -v jazzer'` prints the path to use.

## CLI target

**Synchronous targets** (no promise returned, the common case) run with
`--sync`, which makes them 5 to 50 times faster. The commands below have
it. Drop it for an async target, and record in the target file (Harness)
which kind the target is. The gate's log tells which: Jazzer.js ends an
async-mode run of a synchronous target with "Fuzzing in synchronous mode
seems beneficial!".

**Develop runs:** one process, which writes into the persistent corpus.

<!-- ci: js-cli-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$F/corpus/$H" --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "$F/corpus/$H" --timeout=<input_timeout_ms> --sync \
  -- -max_total_time=<run_s> -rss_limit_mb=<memory_mb> -print_final_stats=1 \
  -artifact_prefix="{run}/artifacts/copy-{copy}/" -dict="$F/dict/$H.dict"
```

- Corpus directories come after the target. New inputs go into the
  first.
- Exit status: 77 for a finding, 70 for a timeout, 0 when the time ran
  out.
- **Comparisons:** compare hooks pass libFuzzer the literal of a
  comparison with a string or number literal (`===`, `!==`, ...). They
  also pass the cases of a `switch` on a variable. Other comparisons (a
  named constant, `startsWith`, `includes`, a `Set`) need the dictionary.
  After a plateau on comparisons of computed values, add
  `-use_value_profile=1` after `--`.

**Long runs:** N copies. List the run's own corpus first and the
persistent corpus second. Every copy writes new inputs into the run's
corpus and re-reads them from there.
- An async target's copy uses about two cores: count two cores per copy
  when sizing its long runs (`fuzz-campaign`'s long-runs.md, "Cores and
  memory").
- Each copy stops at its first finding; the others continue. A known bug
  without a fix would stop the copies again and again: fix it first
  (`fuzz-triage`, "Blocking bugs").

`<tree>` is the run's worktree, and `<tree_package>` the package directory
in it (`fuzz-campaign`):

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

Merge with `-merge=1` on the **current** build, into the merge run's own
corpus. For plain JS, that's the current code; for TypeScript, the `tsc`
output rebuilt from the current sources (`build.md`). The merge run's
corpus then replaces the persistent corpus (`fuzz-campaign`):

<!-- ci: js-cli-merge -->
```sh
$L start --id merge-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "{run}/corpus" "$F/corpus/$H" \
  "$F/runs/<long_run>/corpus" --timeout=<input_timeout_ms> --sync \
  -- -merge=1 -rss_limit_mb=<memory_mb> -artifact_prefix="{run}/artifacts/copy-{copy}/"
```

- The merge records crashing inputs as findings and keeps them in the
  result.
- Check the log: `MERGE-OUTER: <n> new files`.

## Checking a run

<!-- ci: js-check-run -->
```sh
$L status <id>                                          # states, findings, status lines
tail -n 3 .seclab/fuzz/runs/<id>/copy-0/log             # libFuzzer status
```
