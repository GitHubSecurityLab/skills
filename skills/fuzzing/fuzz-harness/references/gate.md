# Harness gate

The gate shows that a harness works before it fuzzes for long: the
target is instrumented and reached, the seeds don't crash it, no state
leaks between inputs, it's fast enough, it respects the threat model,
and it leaves no stray files. Run every step below, in order, after
each harness or build change, before any other run.

**Light gate,** for a skill used on its own (`fuzz-harness`, or
`fuzz-coverage` on a harness without a gate result): every step except "No
state leaking between inputs" and "Respects the threat model". Those two
run before the harness goes into a campaign or a long run.

Commands here are language-neutral; the language skill gives the exact
engine and coverage commands. `<h>` is the harness id and `<id>` the
target id.

A **new harness** is one written from scratch, not an existing one being
extended, until it first passes the gate.

## 1. Prepare

Prepare what the checks need: seeds, builds, the working tree before the
gate run, and the corpus's coverage before fuzzing.

- Seeds and dictionary come first (seeds.md next to this file). The seeds
  are in `corpus/<h>/` (Jest: `.cifuzz-corpus/...`), with at least one
  valid input if the format has one.
- The variants are built (build.md next to this file). Pick the gate
  run's id, `gate-<h>-<n>`: the next item saves the working tree into the
  run's directory.
- Save the working tree's content, untracked files included, before the
  replay below, so that "No stray files" also sees files the replay
  writes. Save it into the gate run's directory, which the launcher
  accepts without a `meta.json`. Git's new objects go there too, not into
  `.git`, which Codex's sandbox makes read-only:
  <!-- ci: gate-tree-before -->
  ```sh
  G="$PWD/.seclab/fuzz/runs/gate-<h>-<n>" && O=$(cd "$(git rev-parse --git-path objects)" && pwd) &&
    mkdir -p "$G/objects" && (export GIT_INDEX_FILE="$G/tree.index" GIT_OBJECT_DIRECTORY="$G/objects" \
      GIT_ALTERNATE_OBJECT_DIRECTORIES="$O"; git add -A && git write-tree) > "$G/tree-before.txt"
  ```
- **Replay the corpus** on the coverage build, before the gate run. At a
  harness's first gate, the corpus is only the seeds; for Jazzer typed
  parameters, which have no seed files, a short develop run fills it
  first (`fuzz-java`'s harness.md). Export lcov to the
  harness's next snapshot, `coverage/<h>/r<round>.lcov` (`r0` if the
  harness has none), with the commands in the language skill's
  coverage.md. "Reaches the target" uses this snapshot. After a harness,
  build or code change, it's also the new baseline (`fuzz-coverage`,
  "Measure the action").

## 2. Reaches the target

**Valid inputs reach the core,** before any fuzzing: fuzzing could hide
a harness that feeds the input wrongly. Jazzer typed parameters are the
exception: at their first gate, Prepare's replay follows a short develop
run.

Name the target's core function from its candidate entry: the dispatch or
main loop that processes a valid input, e.g. `apply_patch`, the record
loop or a chunk handler. Prepare's replay must cover it. For a function
the coverage data doesn't name, such as a JS class method, pass
`--lines <first>-<last>` instead of `--function`:

```sh
python3 .seclab/fuzz/bin/coverage.py show .seclab/fuzz/coverage/<h>/r<round>.lcov --root . --file <core-file> --function <core-function>
```

- If valid inputs stop at the entry point or in input validation, the
  harness feeds the input wrongly: the wrong argument, a truncation, the
  wrong mode or length. Fix the harness, even if fuzzing would later find
  a way around the problem.

## 3. The gate run

Fuzz for `gate_seconds` (default 60) in a launcher develop run with the
id from Prepare. Start it as runs.md next to this file says, with the
command from the language skill's run.md. The steps below read the run's
log and statistics.

## 4. The target library is instrumented

Check the instrumentation in the target library itself, not only in the
fuzz binary: the language skill's build.md, "Instrumentation checks".

## 5. No crash in the gate

A crash on the seeds or anywhere in the gate run goes to `fuzz-triage`.
In code that was never fuzzed, shallow real bugs are common. A
`harness_bug` is fixed here. A real bug in the target is **blocking**:
handle it as `fuzz-triage`'s blocking-bug rule says. Then start the gate
again.

## 6. Fast enough

Record executions per second from the gate run's final statistics. Find
the cause before long runs if the speed is:
- below `min_exec_per_sec` (default 100); or
- after a corpus import or a harness change, below half of this harness's
  last recorded speed.

Neither case is a gate failure. Look for the cause among setup per input,
logging, large inputs (a missing or raised input cap) and slow I/O.

## 7. Fuzzing gets past the seeds

Replay the corpus in the coverage build again, into the next snapshot
`r<next>`: the baseline that the inner loop's first round compares with
(`fuzz-coverage`). Compare it with Prepare's snapshot on the scope files:

<!-- ci: gate-progress -->
```sh
python3 .seclab/fuzz/bin/coverage.py summary .seclab/fuzz/coverage/<h>/r<next>.lcov --prev .seclab/fuzz/coverage/<h>/r<round>.lcov --root . --scope .seclab/fuzz/targets/<id>.scope
```

- The entry point and its callees in scope must be covered.
- For a new harness whose seeds don't already cover the scope, the
  scope's covered branches must also grow over the seeds' (`change`). If
  they don't grow, the gate fails: mutations don't get past an early check,
  such as a checksum, or a length or magic that the layout breaks.
  Diagnose the cause (`fuzz-coverage`'s diagnosis.md), fix it, and run
  the gate again.

## 8. No state leaking between inputs (a smoke check, not a proof)

Replay a sample of the corpus twice, all inputs in one process and each
input in a fresh process, and compare their coverage. The state-leak check
block in the language skill's coverage.md does all of it, as two launcher
runs:

- **Sample:** `sample_size` (default 10) non-empty inputs at random, fewer
  if the fresh replays would take more than about a minute. With fewer
  than two inputs the check doesn't apply; record that.
- **Compare hit counts** (`coverage.py overlap --counts --processes <n>`),
  without `--scope`: state often leaks in the harness itself. A line or
  branch that ran a different number of times in the two replays points
  at state leaking between inputs. Fix the harness (reset the state per input), or
  record why the difference is harmless.
- **Once per process:** code that ran once in each process gets its own
  list in `overlap`'s output, because the fresh-process replay ran it
  once per input. Setup
  such as module loading, `LLVMFuzzerInitialize` or a static table is
  harmless; record that, with the lines. Work on the input that ran only
  for the first input, such as a cache or a "parsed" flag, is a leak.
- If the harness is Java: the block compares covered sets
  (`fuzz-java`'s coverage.md).
- If `overlap` exits 1 (a side has no coverage): the check is
  **inconclusive**; the replay or the paths are broken. It isn't a pass.

## 9. Respects the threat model (checklist review)

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

## 10. No stray files

The fuzzed code must leave no files in the working tree. Compare the
working tree with the one Prepare saved:

<!-- ci: gate-tree-after -->
```sh
G="$PWD/.seclab/fuzz/runs/gate-<h>-<n>" O=$(cd "$(git rev-parse --git-path objects)" && pwd)
(export GIT_INDEX_FILE="$G/tree.index" GIT_OBJECT_DIRECTORY="$G/objects" GIT_ALTERNATE_OBJECT_DIRECTORIES="$O"
 git add -A && git write-tree > "$G/tree-after.txt" &&
   git diff-tree -r --name-status "$(cat "$G/tree-before.txt")" "$(cat "$G/tree-after.txt")"
 s=$?; rm -rf "$G/objects" "$G/tree.index"; exit $s)
```

Each listed path is a side effect of the fuzzed code, even one that was
already changed or untracked before the gate. The only exceptions are the
engine's own output locations, which the language skill lists. Fix the
harness so these side effects go to the copy's scratch.

The block deletes the saved objects and index: with a read-only `.git`,
they hold a copy of every tracked file. Files written into directories the
project already ignores aren't visible here.

## Record

Write one row into the target file's Gate table: date, harness, pass or
fail (and "light" for the light gate), the failed check, the core function
the seeds reach, executions per second, the state-leak check's result.
