# Harness gate

The gate shows that a harness works before it fuzzes for long: the
target is instrumented and reached, the seeds don't crash it, no state
leaks between inputs, it's fast enough, it respects the threat model,
and it leaves no stray files. Run every check after each harness or
build change, before any other run.

**Light gate,** for a skill used on its own (`fuzz-harness`, or
`fuzz-coverage` on a harness without a gate result): checks 1, 2, 3, 5
and 7. Checks 4 and 6 run before the harness goes into a campaign or a
long run. Commands are language-neutral here; the language skill gives
the exact engine and coverage commands. `<h>` is the harness id, `<id>`
the target id.

## 0. Prepare

Prepare what the checks need: seeds, builds, the working tree before the
gate run, and the corpus's coverage before fuzzing.

- Seeds and dictionary come first (seeds.md next to this file): seeds exist in
  `corpus/<h>/` (Jest: `.cifuzz-corpus/...`), at least one valid input if
  the format has one.
- The variants are built (build.md next to this file). The gate's fuzzing is a launcher
  develop run with the id `gate-<h>-<n>` (runs.md next to this file, with the command
  from the language skill's run.md).
- Save the working tree's content, untracked files included, before the
  replay below, so that check 7 also sees files the replay writes. Save it
  into the gate run's directory (the launcher accepts a directory without
  `meta.json`), with git's new objects there too, not in `.git` (Codex's
  sandbox makes it read-only):
  <!-- ci: gate-tree-before -->
  ```sh
  G="$PWD/.seclab/fuzz/runs/gate-<h>-<n>" && O=$(cd "$(git rev-parse --git-path objects)" && pwd) &&
    mkdir -p "$G/objects" && (export GIT_INDEX_FILE="$G/tree.index" GIT_OBJECT_DIRECTORY="$G/objects" \
      GIT_ALTERNATE_OBJECT_DIRECTORIES="$O"; git add -A && git write-tree) > "$G/tree-before.txt"
  ```
- **Replay the corpus** (at a new harness's first gate: only its seeds) on
  the coverage build, before the gate's fuzzing, and export lcov to
  the harness's next snapshot `coverage/<h>/r<round>.lcov` (`fuzz-coverage`,
  "Snapshot"). After a harness, build or code change, it's the new baseline
  (`fuzz-coverage`, "Measure the action"). Check 2 uses it.

## 1. Builds with sanitizers, and the target is instrumented

Check the instrumentation in the target library itself, not only in the
fuzz binary: the language skill's build.md, "Instrumentation checks".

## 2. Reaches the target

**Valid inputs reach the core,** before any fuzzing (Jazzer typed
parameters, which have no seed files: after a short run, `fuzz-java`'s
harness.md). Fuzzing could hide a harness that feeds the input wrongly.
Name the target's core function from its candidate entry: the dispatch or
main loop that processes a valid input (e.g. `apply_patch`, the record
loop, a chunk handler). Step 0's replay must cover it (for a function the
coverage data doesn't name, e.g. a JS class method, `--lines <first>-<last>`
instead of `--function`):

```sh
python3 .seclab/fuzz/bin/coverage.py show .seclab/fuzz/coverage/<h>/r<round>.lcov --root . --file <core-file> --function <core-function>
```

- If valid inputs stop at the entry point or in input validation: the
  harness feeds the input wrongly (the wrong argument, a truncation, the
  wrong mode or length); fix it, even if fuzzing would later find a way
  around it.

**Fuzzing reaches the scope, beyond the seeds.** After the gate's fuzzing,
the entry point and its callees in scope must be covered; at a new
harness's first gate, the scope's covered branches must also grow over the
seeds' (`change`), unless the seeds already cover the scope. Run the gate
run for `gate_seconds` (default 60). Then replay the corpus in the
coverage build again, into the next snapshot `r<next>`, and compare it
with step 0's on the scope files:

<!-- ci: gate-progress -->
```sh
python3 .seclab/fuzz/bin/coverage.py summary .seclab/fuzz/coverage/<h>/r<next>.lcov --prev .seclab/fuzz/coverage/<h>/r<round>.lcov --root . --scope .seclab/fuzz/targets/<id>.scope
```

This snapshot is the baseline that the inner loop's first round compares
with (`fuzz-coverage`).

- If the covered branches didn't grow at a new harness's first gate:
  mutations don't get past an early check (a checksum, a length or magic
  the layout breaks); diagnose it (`fuzz-coverage`'s diagnosis.md) before
  long runs, unless the seeds already cover the scope.

## 3. No instant crash

A crash on the seeds or within the run's first seconds goes to
`fuzz-triage`. In code that was never fuzzed, shallow real bugs are common.
A `harness_bug` is fixed here; a real bug in the target is **blocking**,
fixed or filtered as `fuzz-triage`'s blocking-bug rule says. Then start the
gate again.

## 4. No state leaking between inputs (a smoke check, not a proof)

Replay a sample of the corpus twice, all inputs in one process and each
input in a fresh process, and compare their coverage. The state-leak check
block in the language skill's coverage.md does all of it, as two launcher
runs:

- **Sample:** `sample_size` (default 10) non-empty inputs at random, fewer
  if the fresh replays would take more than about a minute. With fewer
  than two inputs the check doesn't apply; record that.
- **Compare hit counts** (`coverage.py overlap --counts --processes <n>`),
  without `--scope`: state often leaks in the harness itself. A line or
  branch that ran a different number of times in the two points at state
  leaking between inputs. Fix the harness (reset the state per input), or
  record why the difference is harmless.
- **Once per process** (listed apart: the fresh side ran it once per
  input): setup such as module loading, `LLVMFuzzerInitialize` or a static
  table is harmless; record that with the lines. Work on the input that
  ran only for the first one (a cache, a "parsed" flag) is a leak.
- If the harness is Java: the block compares covered sets
  (`fuzz-java`'s coverage.md).
- If `overlap` exits 1 (a side has no coverage): the check is
  **inconclusive**; the replay or the paths are broken. It isn't a pass.

## 5. Fast enough

Record executions per second from the run's final statistics.

- If it's below `min_exec_per_sec` (default 100), or below half of this
  harness's last recorded speed (after a corpus import or a harness
  change): find the cause before long runs (it isn't a gate failure):
  setup per input, logging, large inputs (a missing or raised input cap),
  slow I/O.

## 6. Respects the threat model (checklist review)

- [ ] The input carries only attacker-controlled data, and options the
      attacker or a deployment chooses (`SKILL.md`, "Design the harness").
- [ ] The entry point is the real trust boundary.
- [ ] Documented API contracts are honoured.
- [ ] Only documented exceptions are caught (C++: no `catch (...)`; JS: a
      plain `Error` by its class, `fuzz-js`'s harness.md), and a check's
      failure is raised outside the `try` that catches them.
- [ ] Checks match the target's candidate entry; properties are guaranteed
      by the spec or docs, and each one can fail (`properties.md`).
- [ ] Input feeding and side effects follow
      `input-and-side-effects.md` (next to this file).

## 7. No stray files

The fuzzed code must leave no files in the working tree. Compare it with
the tree step 0 saved:

<!-- ci: gate-tree-after -->
```sh
G="$PWD/.seclab/fuzz/runs/gate-<h>-<n>" O=$(cd "$(git rev-parse --git-path objects)" && pwd)
(export GIT_INDEX_FILE="$G/tree.index" GIT_OBJECT_DIRECTORY="$G/objects" GIT_ALTERNATE_OBJECT_DIRECTORIES="$O"
 git add -A && git write-tree > "$G/tree-after.txt" &&
   git diff-tree -r --name-status "$(cat "$G/tree-before.txt")" "$(cat "$G/tree-after.txt")"
 s=$?; rm -rf "$G/objects" "$G/tree.index"; exit $s)
```

Each listed path, also one already changed or untracked before the gate,
is a side effect of the fuzzed code, except the engine's own output
locations listed in the language skill. Fix the harness so they go to the
copy's scratch. The block deletes the snapshot's objects and index: with
a read-only `.git`, they hold a copy of every tracked file. Files written
into directories the project already ignores aren't visible here.

## Record

Write one row into the target file's Gate table: date, harness, pass or
fail (and "light" for the light gate), the failed check, the core function
the seeds reach, executions per second, the state-leak check's result.
