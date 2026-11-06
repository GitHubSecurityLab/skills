# Long runs: commands and worktrees

## Long-run commands (the user)

At the "long-run commands" checkpoint, prepare one command per harness,
with `$L` from `.seclab/fuzz/env.sh` (develop runs: `fuzz-harness`'s
runs.md). Never start them yourself unless the user asks. A harness that
passed only the light gate first runs `fuzz-harness`'s gate.md, "No state
leaking between inputs" and "Respects the threat model".

1. **Cores and memory.** Use all the machine's cores when develop is
   finished, all but one or two when it continues, minus the cores of runs
   already going (`$L status`). Divide them among the harnesses handed over
   together. Then cap the copies so that their memory, `memory_mb` each
   plus about 0.5 GB per JVM, fits in the available memory minus a few GB
   for the system (Linux: `awk '/MemAvailable/ {print int($2/1024)}'
   /proc/meminfo` in MB; macOS: `sysctl -n hw.memsize` and
   `memory_pressure | tail -1`). The engines' caps leave some memory
   uncapped: ASan caps single allocations only (AFL++'s ASan instance runs
   with `-m none`), Node's flag caps its heap only, and a JVM's memory
   beyond its heap (`-Xmx`) isn't capped. Disk use has no cap: sizing the
   copies by memory and checking free disk space (step 4) are the only
   protection.
2. **The tree.** Commit everything first. The run uses a worktree of the
   launch commit at `<checkout>/../<repo>.seclab-worktrees/<sha>/` with
   the builds the run needs (Which tree, below)
   (`fuzz-harness`'s build.md). Pass it with `--tree` (repeatable; list
   every tree the binaries come from, including the build used to triage
   the run's findings).
3. **Prepare** the spec with the same options as a develop run, the engine's
   long-run form from the language skill (N copies, or the AFL++ mix as a
   hand-written `spec.json`), and the duration (`long_run_hours`, or none):
   ```sh
   $L start --prepare --id <h>-long1 --harness <h> --copies 6 --time 86400 \
     --tree <tree> -- <command>
   ```
   The command points at copies of the dictionary and LeakSanitizer
   suppressions in the run directory (`{run}/<h>.dict`); make them once
   prepare has created it, so edits during develop don't affect the run.
4. **Hand over** the printed command. Say:
   - it runs in the foreground and prints a progress line every 5 minutes;
     Ctrl-C or closing the terminal stops all copies (`stopped`), so over
     SSH it runs inside `tmux` or `screen`;
   - its cores and duration, and for AFL++ and Jazzer (Java) whether it's
     `continuous` (it keeps going past findings; offer stopping at the
     first finding instead when a known, unfixed bug would flood the run);
     Jazzer.js copies always stop at their first finding;
   - which worktree it depends on: don't switch branches or rebuild there
     while it runs;
   - where results land (`.seclab/fuzz/runs/<id>/`), and to check free disk
     space for multi-day runs;
   - a sleeping machine pauses the run: prefix the command with
     `caffeinate -i` on macOS or `systemd-inhibit` on Linux (not inside
     containers);
   - in a codespace, the progress lines keep it from idling out, but its
     12-hour lifetime ends any run still going (`died`): choose a
     duration within it.
5. **Record** each command in `CAMPAIGN.md` → Long runs (run id, harness,
   command, tree, cores, duration, analysis `no`).
6. **If the user asks you to start it:** add `--detach`, and warn that in
   Claude Code and Codex a run the agent starts may end with the session.
   In Codex's sandbox, give the user the command instead: on Linux, a
   detached run ends there with the command.

The agent doesn't monitor long runs. A run that died (reboot, stopped
machine) isn't resumed; its corpus joins the persistent corpus when it's
analyzed, and the next run starts from there.

Done when each command is handed over and listed in Long runs.

## Which tree

Every long run uses a `git worktree` of its **launch commit** (the
campaign branch's commit when the commands are given), shared by every
harness launched from that commit, also when develop is finished. Develop
goes on in the checkout, and nothing there touches a run's binaries.

- **The worktree:**
  - **Location:** `<checkout>/../<repo>.seclab-worktrees/<sha>/`, outside
    the checkout, so the project's own tools (Jest, IDEs, `bazel //...`)
    don't see a second copy. Say where it is. Sandboxed agents ask before
    writing there.
  - **Contents:** the fuzz builds the runs need, plus the libFuzzer
    ASan+UBSan build for triaging AFL++ findings; no coverage build (all
    measurement uses the checkout's). JS: `npm ci --prefer-offline` (or the
    project's equivalent), and for TypeScript the worktree's own `tsc`
    output (`fuzz-js`'s build.md). C/C++: submodules initialized. Java: the
    worktree's own classpath file. Jest: the checkout's `.cifuzz-corpus/...`
    passed as an extra read-only seed directory.

  ```sh
  git worktree add "<checkout>/../<repo>.seclab-worktrees/<sha>" <sha>
  ```
- Every tree a run's binaries come from is passed with `--tree` and
  recorded in its `meta.json` (commit, clean state).
- If the C/C++ project builds only in-tree (`fuzz-c-cpp`'s build.md):
  each build variant has its own worktree, also under
  `<repo>.seclab-worktrees/`: `build/<variant>/` for develop's builds,
  `<sha>/<variant>/` for a launch, `base/<variant>/` for `base`. They
  stay when develop is finished. Before rebuilding a variant, sync its
  worktree to the checkout's working tree (`fuzz-c-cpp`'s build.md).

## Launch worktrees are read-only

Never change, rebuild or remove a launch worktree while a run that lists it
in its `trees` is going (`launcher.py status`), or before phase 1 of every
run that used it (`analyze.md`): its findings must reproduce on the builds
that found them.

## Cleanup

A worktree is needed only until phase 1 of every run that used it (run
corpora live in the checkout's `runs/`; a Jest run's new corpus files are
copied out first, `fuzz-js`):

```sh
git worktree remove "<worktree>"
```

## The `base` worktree

Reproducing a bug without our fixes needs the `base` build, but only once
a local fix exists; before that, the current build serves. Whenever it's
needed, (re)create it (remove an old one first, Cleanup) from the ledger's
base commit plus the campaign branch's commits other than `fuzz(fix)`:
<!-- ci: base-worktree -->
```sh
git worktree add --detach "<checkout>/../<repo>.seclab-worktrees/base" <base_commit>
c=$(git rev-list --reverse --no-merges --invert-grep --grep='^fuzz(fix)' <base_commit>..HEAD)
[ -z "$c" ] || git -C "<checkout>/../<repo>.seclab-worktrees/base" cherry-pick $c
```
Build there what the reproducers need: the libFuzzer ASan+UBSan library,
the compiled classes and classpath file, or `npm ci` (and `tsc` for
TypeScript).
