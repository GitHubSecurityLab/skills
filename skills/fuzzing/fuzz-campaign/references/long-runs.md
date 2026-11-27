# Long runs: commands and worktrees

## Long-run commands (the user)

At the "long-run commands" checkpoint, prepare one command per harness,
with `$L` from `.seclab/fuzz/env.sh`, as for develop runs
(`fuzz-harness`'s runs.md). Never start them yourself unless the user
asks. A harness that passed only the light gate first runs
`fuzz-harness`'s gate.md, "No state leaking between inputs" and "Respects
the threat model".

1. **Cores and memory.** Use all the machine's cores when develop is
   finished, and all but one or two when it continues. Subtract the cores
   of runs already going (`$L status`), and divide the rest among the
   harnesses handed over together.

   Then cap the copies so that their memory fits in the available memory
   minus a few GB for the system. Count `memory_mb` per copy, plus about
   0.5 GB per JVM. To read the available memory:
   - Linux, in MB:
     `awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo`;
   - macOS: `sysctl -n hw.memsize` and `memory_pressure | tail -1`.

   The engines' caps leave some memory uncapped:
   - ASan caps single allocations only (AFL++'s ASan instance runs with
     `-m none`).
   - Node's flag caps its heap only.
   - A JVM's memory beyond its heap (`-Xmx`) isn't capped.

   Disk use has no cap: sizing the copies by memory and checking free disk
   space (step 4) are the only protection.
2. **The tree.** Commit everything first. The run uses a worktree of the
   launch commit at `<checkout>/../<repo>.seclab-worktrees/<sha>/`, with
   the builds the run needs ("Which tree", below; `fuzz-harness`'s
   build.md). Pass the worktree with `--tree`. The option is repeatable:
   list every tree the binaries come from, including the tree of the
   build used to triage the run's findings.
3. **Prepare** the spec with the same options as a develop run. Use the
   engine's long-run form from the language skill: N copies, or the AFL++
   mix as a hand-written `spec.json`. The run's duration is
   `long_run_hours`, or none:
   ```sh
   $L start --prepare --id <h>-long1 --harness <h> --copies 6 --time 86400 \
     --tree <tree> -- <command>
   ```
   The command points at copies of the dictionary and LeakSanitizer
   suppressions in the run directory (`{run}/<h>.dict`). Make the copies
   once prepare has created the run directory, so edits during develop
   don't affect the run.
4. **Hand over** the printed command. Tell the user:
   - The command runs in the foreground and prints a progress line every
     5 minutes. Ctrl-C or closing the terminal stops all copies
     (`stopped`), so over SSH it runs inside `tmux` or `screen`.
   - The run's cores and duration.
   - For AFL++ and Jazzer (Java): whether the run is `continuous`, that
     is, keeps going past findings. When a known, unfixed bug would flood
     the run, offer stopping at the first finding instead. Jazzer.js
     copies always stop at their first finding.
   - Which worktree the run depends on: don't switch branches or rebuild
     there while it runs.
   - Where results land (`.seclab/fuzz/runs/<id>/`), and to check free
     disk space for multi-day runs.
   - A sleeping machine pauses the run: prefix the command with
     `caffeinate -i` on macOS or `systemd-inhibit` on Linux (not inside
     containers).
   - In a codespace, the progress lines keep the codespace from idling
     out, but its 12-hour lifetime ends any run still going (`died`).
     Choose a duration within that lifetime.
5. **Record** each command in `CAMPAIGN.md` → Long runs (run id, harness,
   command, tree, cores, duration, analysis `no`).
6. **If the user asks you to start it:** add `--detach`, and warn that in
   Claude Code and Codex a run the agent starts may end with the session.
   In Codex's sandbox, give the user the command instead: on Linux, a
   detached run ends there with the command that started it.

The agent doesn't monitor long runs. A run that died (reboot, stopped
machine) isn't resumed. Its corpus joins the persistent corpus when the
run is analyzed, and the next run starts from there.

Done when each command is handed over and listed in Long runs.

## Which tree

Every long run uses a `git worktree` of its **launch commit**: the
campaign branch's commit when the commands are given. `<sha>` is its full
hash (`git rev-parse HEAD`). Every harness launched from that commit
shares the worktree, also when develop is finished. Develop goes on in the
checkout, and nothing there touches a run's binaries.

- **The worktree:**
  - **Location:** `<checkout>/../<repo>.seclab-worktrees/<sha>/`, outside
    the checkout, so the project's own tools (Jest, IDEs, `bazel //...`)
    don't see a second copy. Tell the user where it is. Sandboxed agents
    ask before writing there.
  - **Contents:** the fuzz builds the runs need, plus the libFuzzer
    ASan+UBSan build for triaging AFL++ findings. The fuzz builds go in
    the worktree's own `.seclab/fuzz/build/` (`fuzz-harness`'s build.md,
    "Build each variant in its own directory"). There's no coverage
    build: all measurement uses the checkout's. By language:
    - JS: `npm ci --prefer-offline`, or the project's equivalent. For
      TypeScript, also the worktree's own `tsc` output (`fuzz-js`'s
      build.md).
    - C/C++: submodules initialized.
    - Java: the worktree's own classpath file.
    - Jest: the checkout's `.cifuzz-corpus/...`, passed as an extra
      read-only seed directory.

  ```sh
  git worktree add "<checkout>/../<repo>.seclab-worktrees/<sha>" <sha>
  ```
- Each tree passed with `--tree` (step 2) is recorded in the run's
  `meta.json`, with its commit and clean state.
- If the C/C++ project builds only in-tree, each tree has a worktree per
  variant, also under `<repo>.seclab-worktrees/` (`fuzz-c-cpp`'s build.md,
  "In-tree builds"):
  - `build/<variant>/` for develop's builds, which stay when develop is
    finished;
  - `<sha>/<variant>/` for a launch;
  - `base/<variant>/` for `base`.

## Launch worktrees are read-only

Never change, rebuild or remove a launch worktree:
- while a run that lists it in its `trees` is going
  (`launcher.py status`);
- before phase 1 of every run that used it (`analyze.md`).

The findings of those runs must reproduce on the builds that found them.
Reproducing a Jest finding is the one exception: it copies the finding
into the fuzz test's regression directory, and removes the copies right
after (`fuzz-js`'s jest.md, "Triage").

## Cleanup

After phase 1 of every run that used it, remove a launch worktree: run
corpora live in the checkout's `runs/`. Before that, copy a Jest run's new
corpus files out (`fuzz-js`); nothing else in it needs keeping. Bisect
and upstreaming worktrees go when their step is done. `base` and
`fuzz-triage`'s freshness worktrees, `fresh-head` and `fresh-release`,
are kept for reuse. Remove them when the campaign finishes (`SKILL.md`,
"Finishing"), or, without a running campaign, when the request that made
them is done. A new `base` replaces the old one before that.

Remove a worktree with `--force`: a plain `git worktree remove` refuses
when the worktree has initialized submodules, or build outputs the project
doesn't ignore. A project that builds only in-tree has a worktree per
variant: remove each `<tree>/<variant>`, then `rmdir "<tree>"`.
<!-- ci: worktree-cleanup -->
```sh
git worktree remove --force "<worktree>"
```

## The `base` worktree

Reproducing a bug without our fixes needs the `base` build, but only once
a local fix exists; before that, the current build serves. Whenever the
`base` build is needed, remove any old `base` worktree first ("Cleanup").
Then create it from the ledger's base commit plus the campaign branch's
commits other than `fuzz(fix)`:
<!-- ci: base-worktree -->
```sh
git worktree add --detach "<checkout>/../<repo>.seclab-worktrees/base" <base_commit>
c=$(git rev-list --reverse --no-merges --invert-grep --grep='^fuzz(fix)' <base_commit>..HEAD)
[ -z "$c" ] || git -C "<checkout>/../<repo>.seclab-worktrees/base" cherry-pick $c
```
Build there what the reproducers need, as for a launch ("Which tree"):
the libFuzzer ASan+UBSan library, the compiled classes and classpath
file, or `npm ci` and, for TypeScript, `tsc`.
