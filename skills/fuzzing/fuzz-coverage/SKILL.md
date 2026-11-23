---
name: fuzz-coverage
description: >-
  Measures a fuzz harness's source coverage, explains why attack-surface
  code stays uncovered, and applies the most valuable fix: seeds,
  dictionary, targeted inputs, harness or build change; also corpus merges.
  Use for "why is coverage stuck?" or "improve this fuzzer's coverage". Not
  for writing a new harness (fuzz-harness).
license: MIT
---

# Fuzz coverage

This skill measures source coverage after each round, explains why code in
the target's scope stays uncovered, and picks the action that unlocks the
most security-relevant code. It owns the inner loop: develop round →
snapshot → (plateau) diagnose → act → round, until no action left unlocks
security-relevant code or a new target is worth more (step 10).

**Run every step** this skill gives for the request; skip one only when the user asks. Later steps rely on what earlier ones measured and recorded, so a skipped step makes later results wrong, not the work faster.

## When to use

- After a harness passes the gate (inner loop of a campaign).
- "Why is coverage stuck?", "improve coverage of this fuzzer", "what does
  this harness not reach?".
- "Find inputs for this branch", corpus merges and minimizing
  ([references/corpus.md](references/corpus.md)).

Not for writing the harness or its first seeds (`fuzz-harness`); this skill
decides when to change them.

## Start

Before anything else, and again whenever you resume, run from the
repository root and follow what it prints:

```sh
python3 <this skill's directory>/../fuzz-campaign/scripts/init.py
```

- The safety rules it prints apply here too.
- Exit 2 (no ledger yet): rerun with `--branch current`.
- Ledger status `standalone`: before the first commit, ask whether the work
  moves to a new `seclab/fuzz-<date>` branch (`--branch new`); read-only
  requests never ask.
- If `init.py` is missing, the skill was installed without its plugin: ask
  the user to install the `seclab-skills` plugin; don't look for another
  copy.

## Reads

What the steps in this file read. Paths are relative to `.seclab/fuzz/`
unless they start with `.seclab/`:
- `targets/<id>.md`: Scope files, Sinks, Harness, Coverage rounds.
- `targets/<id>.scope`, `sinks/<id>.tsv`.
- `CAMPAIGN.md`: Settings (`round_minutes`, `plateau`), Open crashes.
- `.seclab/threat-model.md`: what's attack surface, for scope corrections
  (step 5) and the gaps it excludes.
- The language skill: how to replay the corpus in the coverage build and
  export lcov.

## Steps

Which steps run depends on the request:

| Request | Steps |
|---|---|
| The inner loop: a campaign, or a request to improve coverage, such as "improve coverage of this fuzzer" | 1–11 |
| A diagnosis, such as "why is coverage stuck?" or "what does this harness not reach?", on a harness with a corpus | 2–3, then 5–8; stop after step 8 with the diagnosis and the proposed action, without applying it |
| **Measure only:** a baseline, combined coverage, or another skill asks for a number | 2–3 on the current corpus, without a round; return the number. Before any target exists (a campaign's baseline): `summary` without `--scope`, and no Coverage rounds row |
| Inputs for a branch, a corpus merge or minimization, such as "find inputs for this branch" | [references/corpus.md](references/corpus.md) |

**Standalone use** may lack what the steps need. Supply each missing item
before the steps that need it:
- No target file: write the minimal one first (`fuzz-harness`'s
  target-file.md).
- No coverage variant: check the toolchain with the language skill's
  install.md, then build the variant (`fuzz-harness`'s build.md).
- The harness has a corpus elsewhere, such as its own fuzzing output,
  OSS-Fuzz's or a CI artifact: copy only that corpus into `corpus/<h>/`
  (Jest: the test's directory in `.cifuzz-corpus/`), so the replay
  measures it.
- No gate result for the harness in the target file: run the light gate
  (`fuzz-harness`'s gate.md, "Light gate") before the first round. Step 4
  needs its executions per second.

A missing sink list needs nothing supplied: step 7 then skips `sites` and
only reads the gaps' code.

1. **Run a round.** Each round is one develop run, as `fuzz-harness`'s
   runs.md describes, with the command and environment from the language
   skill's run.md. It resumes from the harness's corpus.
   - It lasts the next value of the `round_minutes` schedule (1, 2, 4, 8
     minutes by default). Rounds are short; the hours go into long
     runs.
   - Its id is `r<round>-<h>`, where `<h>` is the harness id and `<round>`
     is the number its snapshot gets (step 2).
   - Hand findings to `fuzz-triage`. If a crash cut the round short, the
     round doesn't count toward the plateau: take its snapshot (step 2)
     before any rebuild or other replay, add its row (step 3), and rerun
     it after triage. For a blocking bug, follow `fuzz-triage`, "Blocking
     bugs".
2. **Snapshot.** Replay the corpus in the coverage build and export lcov
   to `coverage/<h>/r<round>.lcov`, with the commands in the language
   skill's coverage.md. Every replay of a harness's corpus takes the next
   `<round>`: the gate's, a new baseline's and each round's. Numbering
   starts at 0 and never restarts, so no snapshot or run id is
   overwritten, and `r<round-1>` is always the snapshot before.
3. **Measure.** One `summary` call on the scope files gives the scope's
   coverage and its change since the previous snapshot. Between rounds,
   that's all the analysis. Leave out `--prev` for a harness's first
   snapshot:
   ```sh
   python3 .seclab/fuzz/bin/coverage.py summary .seclab/fuzz/coverage/<h>/r<round>.lcov --prev .seclab/fuzz/coverage/<h>/r<round-1>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .
   ```
   - If the scope code differs between the two snapshots, as after a fix
     or a build patch, or the previous snapshot has a scope file this one
     lacks: `summary` says so and gives no change. This snapshot is a new
     baseline, and if it's a round's, that round doesn't count toward the
     plateau.

   Add a row to Coverage rounds, with the run's executions. If the row is
   for a round that doesn't count toward the plateau, its Change says why
   instead. The reason is a crash that cut it short, different code, a
   missing file, or step 4's "Too slow" rule.
4. **Plateau?** This decides when rounds stop and diagnosis starts. Don't
   diagnose earlier: a gap found before the plateau may be code the fuzzer
   would still reach.
   - **Too slow:** a round that ran at less than half the harness's last
     measured executions per second doesn't count; a busy host or a slow
     input held it back. The last measurement is the most recent one, from
     the gate or a round that counted. Rerun the round once. If the rerun
     is slow too, the harness got slower: count the rerun, and find the
     cause (`fuzz-harness`'s gate.md, "Fast enough").
   - **Stalled:** a round that covers no new scope branches and finds no
     crash.
   - **Plateau:** two stalled rounds in a row with at least 100,000
     executions together, or the end of the schedule. With fewer
     executions, run the next round if the schedule has one left.

   On a plateau, diagnose (steps 5–8); otherwise, run the next round. A
   plateau isn't saturation.
5. **Correct the scope.** Gaps and coverage count only the scope files, so
   first make the scope match the attack surface the harness reaches:
   - Run `summary` without `--scope` to see the files the harness reaches,
     and add each reached attack-surface file the scope doesn't list, with
     a reason.
   - A scope file still at 0% after a plateau is one of two things. If
     it's a gap for an action, it stays. If it was a wrong estimate, remove
     it, with a reason.
   - If step 3's `summary` marks a scope entry as having no coverage data:
     - check that the entry is a path from the repository root and, for
       TypeScript, the file the lcov names: the compiled `.js` file for
       CLI targets (`fuzz-js`'s coverage.md, "TypeScript"), the `.ts` file
       for Jest fuzz tests (`fuzz-js`'s jest.md, "Coverage"). Fix the
       entry if not;
     - if the path is right, find out why the file isn't compiled, linked
       or loaded; for a C/C++ library, check the export's `-object`
       arguments (the language skill).
   - If a file is macro-heavy C (macro branches count at every expansion)
     or also holds API the harness doesn't target: judge its gaps by lines
     and blocked branches, not the branch share.

   Log every scope change in the target's Notes.
6. **Diagnose the gaps.** List them with `gaps`, then `show` each top
   gap's function:
   ```sh
   python3 .seclab/fuzz/bin/coverage.py gaps .seclab/fuzz/coverage/<h>/r<round>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .
   python3 .seclab/fuzz/bin/coverage.py show .seclab/fuzz/coverage/<h>/r<round>.lcov --root . --file <file> --function <function>
   ```
   `gaps` lists **blocked branches** (JaCoCo: blocked lines) first, with
   their guards' source lines, ranked by an estimate of the uncovered
   lines each blocks. A blocked branch is one never taken at a point where
   another branch was taken. Each blocked branch also lists its
   never-called callees: functions that never ran and are called from the
   lines it blocks. Such a callee is a whole function that the branch
   blocks, so the branch blocks more than its estimate shows. The
   remaining gaps follow, per function.

   Read each top gap in scope in context and classify why it's uncovered,
   using [references/diagnosis.md](references/diagnosis.md). The reason
   decides the action (step 8).
7. **Check the sinks.** A sink group's state, and for a reached group its
   tries, say whether the group still needs inputs. Run `sites`:
   ```sh
   python3 .seclab/fuzz/bin/coverage.py sites .seclab/fuzz/coverage/<h>/r<round>.lcov --sites .seclab/fuzz/sinks/<id>.tsv --root .
   ```
   - If it reports moved sites: update their lines in the TSV.
   - If it can't find a site's code: find the site again and correct its
     row.
   - If it marks a site *to confirm*: check that its call ran before
     counting it (diagnosis.md, "Sites to confirm").

   Update each group's state: **not reached** or **reached**, as `sites`
   reports, or the first of these that applies:
   - **triggered:** a bucket in `crashes/` has its crash location or root
     cause at one of the group's sites;
   - **sanitized:** the code blocks the attack before the sink, recorded
     with the `file:line` that does;
   - **uncheckable:** no sanitizer, engine detector or check in the
     harness can see a bug at its sites, and the harness can't get a
     check that does (`fuzz-harness`'s properties.md, "Security checks at
     sinks without an engine detector"). Its Note says what goes unseen.

   A reached group's Note counts its tries with targeted inputs, e.g.
   "2 of 4 tries"; the check of a regular expression applied to input
   (redos.md) counts as all four.

   Then act on each group by its state:
   - A high-ranked group **not reached**: follow its call path back to the
     deepest covered call with `show`, and diagnose the branch there as a
     gap (step 6).
   - Groups **reached** with tries left: step 8 chooses the action that
     triggers them. [references/diagnosis.md](references/diagnosis.md),
     "Sinks reached but not triggered", gives that action for each
     language and sink kind (targeted inputs:
     [references/corpus.md](references/corpus.md)). For Java/JS regular
     expressions, that action follows
     [references/redos.md](references/redos.md).
   - **Triggered** and **sanitized** groups, and **reached** ones after
     four tries: no more inputs until their code changes.
   - **Uncheckable** groups: no inputs until something can see a bug at
     their sites. The summary lists them for review by hand (step 10).

   When a group's code or checks change, set its state again from
   `sites`, with its tries back at 0.
8. **Choose the next action.** A gap that got 3 actions without covering
   their aim is **hard**: mark it so in the target's Notes, and aim no
   further action at it until there's new evidence. Choose the action by
   the rule in [references/diagnosis.md](references/diagnosis.md),
   "Choosing the next action".
   - Record the action and its **aim** in the target file: the blocked
     branch, function or condition it should cover. For a sink reached but
     not triggered, the aim is the untaken branch or condition at the
     sink.
   - A request only for a diagnosis, such as "why is coverage stuck?",
     stops here: propose the action, don't apply it.

   Otherwise, apply the action:
   - seeds, dictionary or targeted inputs for a blocked branch or a sink
     reached but not triggered →
     [references/corpus.md](references/corpus.md);
   - structure-aware escalation → `fuzz-harness`;
   - harness change for a variant it never selects, a stateful sequence,
     speed or determinism → `fuzz-harness`, then rebuild and gate; the
     gate's corpus replay is the new baseline (step 9);
   - build patch for a checksum, a signature or randomness →
     `fuzz-harness`'s build.md, then the gate, whose corpus replay is the
     new baseline (step 9);
   - a new target worth more than every action left → leave the loop.
9. **Measure the action** against the snapshot before it.
   - **When an action is applied,** the round schedule restarts at its
     first value; reverting a change doesn't restart it. Snapshot numbers
     keep counting.
   - **Seeds, dictionary, flags (same build):** they stay. Replay right
     away only if inputs were added, and compare with the snapshot before
     the action; dictionary and flag changes act only while fuzzing.
   - **A code, build or harness change made to reach something (new build):**
     replay the corpus on the new build first, before the gate's fuzzing
     or any round. That replay is the new baseline. New harness variants,
     added to a harness or as a harness of their own, are kept or dropped
     by `fuzz-harness`'s rule instead (`fuzz-harness`, "No unjustified
     duplication"). Bug fixes aren't judged here. **Keep any other change
     made to reach something only if its aim becomes covered or scope
     coverage clearly rises:**
     - The aim counts as covered when that replay, the restarted rounds,
       or targeted inputs written for it right after the change
       ([references/corpus.md](references/corpus.md)) cover it.
     - Scope coverage clearly rises when the next plateau covers at least
       5% more scope branches than the last snapshot before the change.
       Compare `summary`'s covered counts, not percentages: the change can
       add or remove branches.

     Otherwise revert its commit, rebuild, and replay the corpus again for
     a new baseline.
10. **Leave the loop** when no action left unlocks security-relevant code,
    or a new target beats every action left; the harness is then ready for
    a long run. First minimize its corpus
    ([references/corpus.md](references/corpus.md), corpus upkeep). Then
    write a short summary into the target file: final scope coverage, main
    remaining gaps with their classification, sink states with the
    uncheckable groups to review by hand, and whether a long run is
    worthwhile (reached sinks, deep code still being found).
11. **Overlap check** when the target has more than one harness, or after
    a new harness for an existing workflow: it finds harnesses that
    duplicate each other. `<last>` is each harness's latest snapshot. For
    Java, also pass `--union` with the report from merging the two
    harnesses' `.exec` files (`fuzz-java`'s coverage.md, "Combining Java
    coverage"):
    ```sh
    python3 .seclab/fuzz/bin/coverage.py overlap .seclab/fuzz/coverage/<a>/<last>.lcov .seclab/fuzz/coverage/<b>/<last>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .
    ```
    A harness that covers nothing the other doesn't is a hint, not a
    decision. A recorded specialist stays (`fuzz-harness`, "No unjustified
    duplication"); for any other such harness, propose merging or removing
    it.

## Done when

- The loop ended for a recorded reason, with every round's numbers, and
  every action with its aim and whether it was kept, in the target file.
- Remaining gaps in scope are classified, and sink states are current.

## Pitfalls and shortcuts to reject

- Judging progress by line coverage or whole-project percentages. Use
  branch coverage on the scope files.
- Analyzing gaps before the fuzzer had time. The first rounds are short on
  purpose; diagnose what remains after the plateau (step 4).
- Keeping a harness or build change because coverage rose after it.
  Compare with the snapshot before it (step 9).
- "Coverage went up, so the harness is fine." Look at which code: CLI,
  logging and test helpers don't count.
- Chasing 100%. Dead code, platform branches and allocation-failure paths
  stay uncovered; classify and move on.
- Taking the cheapest action instead of the most valuable one.

## Writes

What the steps in this file write or change. Paths are relative to
`.seclab/fuzz/` unless they start with `.seclab/`:
- `coverage/<h>/r<round>.lcov`.
- `targets/<id>.md`: Coverage rounds, Scope files (corrections), Sinks
  (states), Notes; `targets/<id>.scope`. The target file's layout:
  `fuzz-campaign`'s ledger.md, "`targets/<target>.md`".
- `CAMPAIGN.md`: Targets (status, scope branch coverage).

## Next

- **Before stopping,** check the Done when items that this request's steps produce: do the step behind a missing one, or record why it can't be done.
- In a campaign: the skill for the chosen action, or back to
  `fuzz-campaign` when the loop ends. Called by another skill for one
  step: return to it with the result.
- Standalone: stop when the request is done. Show the result, using the skills' terms, each explained in a few words the first time (e.g. "the gate (the harness checks)"), and offer the next step without starting it.

