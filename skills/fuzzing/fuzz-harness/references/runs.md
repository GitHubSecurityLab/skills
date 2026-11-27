# Runs

Every run of project code during a campaign goes through `launcher.py`:
gate runs, fuzzing rounds, coverage replays, merges, reproductions, fix
validation and long runs. The launcher runs the command it's given as one
or more copies. Each copy gets an empty environment, its own scratch
directory, its own findings directory and a bounded log. The launcher
records the run in `runs/<id>/`.

Builds, installs and the project's own tests don't go through the
launcher: they run with the user's environment. The agent runs develop
runs itself. Long runs are commands the agent hands to the user, who runs
them in a terminal.

## Reads

What the steps in this file read. Paths are relative to `.seclab/fuzz/`
unless they start with `.seclab/`:
- `CAMPAIGN.md`: Settings (`memory_mb`, `input_timeout_s`, `round_minutes`,
  `long_run_hours`), Long runs.
- `targets/<id>.md`: Harness (engine, build variants, corpus, dictionary).
- The language skill for the engine's exact command and environment.

## Steps

### 1. Pick the run id and the command

Give each run a new id, and a command that names its findings directory
and passes its caps. The agent's own shell commands, `$L start` included,
begin with `. .seclab/fuzz/env.sh;`, which sets `F` (the absolute path of
`.seclab/fuzz`) and `L` (`python3 $F/bin/launcher.py`).

- **Run ids** (one directory each under `runs/`):
  - develop runs: `gate-<h>-<n>`, `r<round>-<h>` (a round, named like its
    snapshot `r<round>.lcov`), `cov-<h>-<n>`, and `reach-<h>-<n>-<step>`
    (`reach.py`'s replays, which it names itself);
  - triage: `repro-<h>-<n>` (a finding's replay), `min-<h>-<n>`
    (minimizing), `poc-<h>-<n>` (a bug's reproducer program),
    `oss-fuzz-<h>-<n>` (a replay on OSS-Fuzz's build), so grouping reads
    only local replays;
  - analysis merges: `merge-<h>-<n>`;
  - long runs: `<h>-long<n>`.

  The launcher refuses an id that has already started; use the next number.
- **The command** comes from the language skill, with absolute paths and
  these placeholders, which the launcher fills per copy: `{run}` (the run
  directory), `{scratch}` (the copy's working directory), `{tmp}`, `{copy}`
  (its number). It runs without a shell; a command that needs one uses
  `sh -c '...'` with the placeholders in double quotes.
- **Findings** go to `{run}/artifacts/copy-{copy}/`, for every command that
  runs inputs: libFuzzer, Jazzer and the Jazzer.js CLI with
  `-artifact_prefix`, Jest with `-exact_artifact_path`, FuzzTest with
  `FUZZTEST_REPRODUCERS_OUT_DIR`, AFL++ in its own `out/<instance>/`.
  Otherwise libFuzzer writes them into scratch, which is deleted, and Java
  into `src/test/resources`.
- **Caps** from Settings: memory per process (`memory_mb`), per-input
  timeout (`input_timeout_s`), and the engine's own time limit set to the
  run's `--time`. The launcher sets none of them: every command passes
  them as engine flags, as the language skill's run.md commands do. A
  reproducer runs one input, so its time limit is the run's `--timeout`.
  For what the caps don't limit, see `fuzz-campaign`'s long-runs.md,
  "Cores and memory".

Options, `spec.json`, states and exit codes:
`python3 .seclab/fuzz/bin/launcher.py start --help`.

### 2. Develop runs (the agent)

One plain process each, in the foreground. Choose the flags first:

- **Timed runs** (`--time <s>`) fuzz; **self-ending runs** (`--timeout <s>`,
  only a kill limit) replay, merge or reproduce. For no time limit, omit
  `--time` (0 is refused).
- **Rounds** use the harness's persistent corpus as their output corpus
  (`--corpus`), so each round starts from the previous one's inputs.
- **Longer than the agent can wait for one command** (Claude Code: 2
  minutes by default, at most 10), such as the 240 s round below: add
  `--detach`, then call `$L wait <id> --max 100` until it prints
  the result. **Codex** keeps a command running when it hands back after
  10 seconds, so its runs stay in the foreground: in its sandbox, a
  detached run ends with the command on Linux.

Then start it:

```sh
$L start --id r3-<h> --harness <h> --time 240 --corpus "$F/corpus/<h>" \
  --env ASAN_OPTIONS=... -- <command>
$L start --id cov-<h>-4 --harness <h> --timeout 1800 -- <replay command>
```

- **Read the result line, not the log:** the state says why each copy ended
  (`finished`, `found-crash`, `failed`, `exited <code>`, `timed out`,
  `stopped`, `failed to start: <error>`), with the findings counted
  separately and listed by path.
  Look at the log's tail only when needed: `tail -n 20 .seclab/fuzz/runs/<id>/copy-0/log`.
- **Exit status:** a single-copy self-ending run returns its command's
  exit status (124 if it timed out); any run, detached too, returns 127
  if a command can't be started, and its other copies are stopped.
- **Exit status alone doesn't identify a finding:** libFuzzer exits 72 when
  stopped, Jest exits 1 for a finding and for a configuration error, and
  merges and Jazzer `--keep_going` exit 0 after crashes. Replays in
  regression mode or with file arguments may write no finding file (Jazzer
  and the Jazzer.js CLI write none for inputs given as files): read their
  logs, which show the exception.

### 3. Check, wait, stop

```sh
$L status                 # running runs and all long runs
$L status <id>            # one run: copies, states, findings, status lines
$L wait <id> --max 100    # blocks at most 100 s
$L stop <id>              # TERM, a grace period, then KILL
```

- `status --all` adds ended develop runs; a `reach.py` search shows as
  one row.
- **States:** `running`, `running (unsupervised)` (copies alive,
  supervisor gone), the end states above, `died` (all gone without an end
  record), and `unverified`. A run is `unverified` when process
  identities can't be read here, e.g. in a sandbox; it's never taken for
  ended.
- Never `kill` PIDs from old records: PIDs get reused. `stop` checks each
  process's identity first. If a process is left, `stop` says which copy
  it belongs to and exits 1, without recording the run as stopped.
- `stop` reaches only each copy's process group. A process that left its
  group (`setsid`, a daemon) runs on, with `SECLAB_RUN=<id>/...` in its
  environment.
- If the run is AFL++: each instance's `out/<instance>/fuzzer_stats` and
  `plot_data` show its progress.

## Environment pitfalls

- **Tool caches:** `HOME` and the working directory are the copy's
  scratch. A command therefore passes the real caches as absolute paths,
  runs Java and JS builds offline, and names the build file or project
  directory, as the language skill's run.md commands do.
- **Codex** blocks network access in its default sandbox, which breaks
  dependency installs.
- **Limits of the launcher's hygiene:** it catches relative paths,
  temporary files and inherited tokens, not code that writes to absolute
  paths or opens network connections (except where the Java and JS
  detectors block them). For untrusted code, recommend a codespace or VM.

## Done when

- Develop run: its result line (state, findings) is recorded where the
  calling skill says.

## Pitfalls and shortcuts to reject

- Running a fuzzer directly instead of through the launcher. The run then
  inherits tokens, writes into the repository and can't be reproduced.
- Reading or pasting the whole log. Use the result line, `status`, and
  `tail`.
- Monitoring a long run, or sleeping in a loop waiting for it.
- Rebuilding a launch worktree a running run uses.
- Several copies writing findings into one directory: identical files
  collapse into one, and the launcher can't tell which copy found it.

## Writes

What the steps in this file write or change. Paths are relative to
`.seclab/fuzz/` unless they start with `.seclab/`:
- `runs/<id>/`: `spec.json`, `meta.json`, `progress.tsv`, `artifacts/`,
  `copy-<i>/log`.
- `corpus/<h>/` (Jest: the test's directory in the package's
  `.cifuzz-corpus/`): each round's new inputs.
- `CAMPAIGN.md`: Long runs. `targets/<id>.md`: Coverage rounds, Notes.
