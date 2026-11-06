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
most security-relevant code. It owns the inner loop:
develop round → snapshot → (plateau) diagnose → act → round, until no
action left unlocks security-relevant code or a new target is worth more.

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
repository root:

```sh
python3 <this skill's directory>/../fuzz-campaign/scripts/init.py
```

It sets up `.seclab/fuzz/`, copies or refreshes the scripts in
`.seclab/fuzz/bin/`, and prints the first-build notice and the safety rules,
which apply here too. Show the notice before any build or run of project
code, then rerun with `--notice-shown`. Exit 2 (no ledger yet): rerun with
`--branch current`, and before the first commit ask whether the work moves
to a new `seclab/fuzz-<date>` branch (`--branch new`); read-only requests
never ask. If `init.py` is missing, the skill was installed without its
plugin: ask the user to install the `seclab-skills` plugin; don't look for
another copy.

## Reads

- `targets/<id>.md`: Scope files, Sinks, Harness, Coverage rounds.
  `targets/<id>.scope`, `sinks/<id>.tsv`.
- `CAMPAIGN.md`: Settings (`round_minutes`, `plateau`), Open crashes.
- The language skill: how to replay the corpus in the coverage build and
  export lcov.

## Steps

**If it's standalone use without a target file:** write the minimal one
first (`fuzz-harness`, Reads). If the coverage variant is missing, check
the toolchain with the language skill's install.md, then build the
variant (`fuzz-harness`'s build.md). If the target file has no gate
result for the harness: run the light gate (`fuzz-harness`'s gate.md,
"Light gate") before the first round; step 4 needs its executions per
second. "Why is coverage stuck?" on a harness that has a corpus: take a
snapshot (steps 2–3) and go to step 5. Without a sink list, step 7 only
reads the gaps' code.

**Measure only** (a baseline, combined coverage, or another skill asks
for a number): steps 2 and 3 on the current corpus, without a round, then
return.

1. **Run a round.** Each round is one develop run: how to run it in
   `fuzz-harness`'s runs.md, its command and environment in the
   language skill's run.md. It lasts the next value of the
   `round_minutes` schedule (1, 2, 4, 8 minutes by default), with the id
   `r<n>-<h>`, where `n` is the number its snapshot gets (step 2), and
   resumes from the harness's corpus. Rounds are short; the hours go into
   long runs. Hand findings to `fuzz-triage`.
   - If a crash cut the round short: it doesn't count toward the plateau;
     rerun it after triage (blocking bugs: `fuzz-triage`).
2. **Snapshot.** Replay the corpus in the coverage build and export lcov to
   `coverage/<harness>/r<n>.lcov` (commands in the language skill, where
   `n` is `<round>`). Every replay of a harness's corpus (the gate's, a new
   baseline, a round's) takes the next `n`, from 0, and `n` never restarts:
   no snapshot or run id is overwritten, and `r<n-1>` is the one before.
3. **Measure.** One `summary` call gives the scope's coverage and its
   change since the previous snapshot; between rounds, that's all the
   analysis (no full diagnosis). Run it on the scope files and against
   the previous snapshot of the same coverage build; without `--prev` for
   a harness's first snapshot and for a new build's baseline, since lines
   and branches of different builds don't match:
   ```sh
   python3 .seclab/fuzz/bin/coverage.py summary .seclab/fuzz/coverage/<h>/r<n>.lcov --prev .seclab/fuzz/coverage/<h>/r<n-1>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .
   ```
   Add a row to Coverage rounds, with the run's executions.
4. **Plateau?** This decides when rounds stop and diagnosis starts; gaps
   diagnosed earlier may be code the fuzzer would still reach. A round
   that ran far fewer inputs than its minutes times the gate's executions
   per second (a busy host, a slow input) doesn't count; rerun it. A round
   **stalls** when it adds no new scope branches and found no crash. The
   engine has plateaued after two stalled rounds in a row that ran at
   least 100,000 inputs together (fewer: run the next round), or when the
   schedule ends. Then diagnose (steps 5–8; a plateau isn't saturation);
   otherwise run the next round.
5. **Correct the scope.** Gaps and coverage count only the scope files,
   so first make the scope match the attack surface the harness reaches.
   Run `summary` without `--scope` to see the files the harness reaches;
   add each attack-surface file the scope doesn't list, with a reason. A
   scope file still at 0% after a plateau either stays as a gap for an
   action, or the estimate was wrong and it's removed with a reason. Log
   every change in the target's Notes.
   - If `summary` marks a scope entry as having no coverage data: entries
     are paths from the repository root: fix the path, or find out why the
     file isn't compiled, linked or loaded (C/C++ libraries: the export's
     `-object`, in the language skill).
   - If a file is macro-heavy C or also holds other API: judge its gaps by
     lines and blocked branches, not the branch share (`fuzz-c-cpp`'s
     coverage.md, "Macro branches").
6. **Diagnose the gaps.** Read each top gap in scope in context and
   classify why it's uncovered, using
   [references/diagnosis.md](references/diagnosis.md); the reason decides
   the action (step 8). List them with `gaps`, then `show` each top gap's
   function:
   ```sh
   python3 .seclab/fuzz/bin/coverage.py gaps .seclab/fuzz/coverage/<h>/r<n>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .
   python3 .seclab/fuzz/bin/coverage.py show .seclab/fuzz/coverage/<h>/r<n>.lcov --root . --file <file> --function <function>
   ```
   `gaps` lists **blocked branches** first: a branch never taken at a point
   where another branch was taken, with the guard's source line, ranked by
   an estimate of the uncovered lines it blocks (JaCoCo: blocked lines).
   Each also lists the never-called functions those lines call; such
   a callee blocks a whole function, more than the estimate shows. Then the
   remaining gaps per function.
7. **Check the sinks.** A sink group's state says whether it still needs
   inputs. Run `sites`, then update each group's state: not reached,
   reached, triggered (from the crash ledger), or **tested**: its trigger
   or boundary inputs ran on the sanitizer or detector build without a
   finding.
   ```sh
   python3 .seclab/fuzz/bin/coverage.py sites .seclab/fuzz/coverage/<h>/r<n>.lcov --sites .seclab/fuzz/sinks/<id>.tsv --root .
   ```
   If `sites` marks a site *to confirm*: check that its call ran before
   counting it (diagnosis.md, "Sites to confirm"). Tested and sanitized
   groups get no more inputs until their code changes. For the others:
   - A high-ranked group **not reached**: follow its call path back to the
     deepest covered call (`show`) and diagnose the branch there as a gap
     (step 6).
   - Groups **reached but not triggered**: reach, then trigger
     ([references/corpus.md](references/corpus.md), targeted inputs), as
     [references/diagnosis.md](references/diagnosis.md), "Sinks reached but
     not triggered", says for each language and sink kind (Java/JS regular
     expressions: [references/redos.md](references/redos.md)).
8. **Choose the next action** by the rule in
   [references/diagnosis.md](references/diagnosis.md), "Choosing the next action".
   Record the action and its **aim** (the blocked branch, function or
   condition it should cover; for a sink reached but not triggered, the
   untaken branch or condition at the sink). Where each action goes:
   - seeds, dictionary or targeted inputs for a blocked branch or a sink
     reached but not triggered →
     [references/corpus.md](references/corpus.md);
   - structure-aware escalation → `fuzz-harness`;
   - harness change (a variant it never selects, a stateful sequence, speed,
     determinism) → `fuzz-harness` (rebuild and gate);
   - build patch (checksum, signature, randomness) → `fuzz-harness` (build.md) → gate;
   - a new target worth more than every action left → leave the loop.
9. **Measure the action,** always on the same coverage build, against the
   snapshot before it: coverage that rises after an action may have risen
   without it.
   - **Seeds, dictionary, flags (same build):** replay right away only if
     inputs were added (dictionary and flag changes act only while
     fuzzing), and compare with the snapshot before the action. They stay.
   - **A code, build or harness change made to reach something (new
     build):** replay the corpus on the new build first, before the gate's
     fuzzing or any round; this is the new baseline. New variants are kept
     by `fuzz-harness`'s overlap rule instead, and bug fixes aren't judged
     here. **Keep any other change only if its aim becomes covered or scope
     coverage clearly rises:** the aim by that replay, by the restarted
     rounds, or by targeted inputs written for it right after
     ([references/corpus.md](references/corpus.md)); scope coverage when
     the scope branches covered at the next plateau are at least 5% above
     the last snapshot before the change. Otherwise revert its commit,
     rebuild, and replay the corpus again for a new baseline.
   - **When an action is applied,** the round schedule restarts at its
     first value (not after a rollback); snapshot numbers keep counting.
10. **Leave the loop** when no action left unlocks security-relevant code,
    or a new target beats every action left; the harness is then ready for
    a long run.
    Minimize its corpus first ([references/corpus.md](references/corpus.md), corpus upkeep).
    Write a short summary into the target file: final scope coverage, main
    remaining gaps with their classification, sink states, and whether a
    long run is worthwhile (reached-but-untriggered sinks, deep code still
    being found). Effort limits:
    - If build repair failed with 3 distinct approaches: ask the user
      (interactive) or set the target aside (autonomous).
    - If one gap got 3 actions without gain: mark it "hard" until there's
      new evidence.
11. **Overlap check** when the target has more than one harness, or after a
    new harness for an existing workflow, to find harnesses that duplicate
    each other. Java adds `--union`, the report of both harnesses' merged
    `.exec` files (`fuzz-java`'s coverage.md, "Combining Java coverage"):
    ```sh
    python3 .seclab/fuzz/bin/coverage.py overlap .seclab/fuzz/coverage/<a>/<last>.lcov .seclab/fuzz/coverage/<b>/<last>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .
    ```
    A harness that covers nothing the other doesn't is a hint, not a
    decision: a recorded specialist stays (`fuzz-harness`, "No unjustified
    duplication"); propose merging or removing the others.

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

- `coverage/<harness>/r<n>.lcov`.
- `targets/<id>.md`: Coverage rounds, Scope files (corrections), Sinks
  (states), Notes; `targets/<id>.scope`.
- `CAMPAIGN.md`: Targets (status, scope branch coverage).

## Next

- **Before stopping,** check the Done when items that this request's steps produce: do the step behind a missing one, or record why it can't be done.
- In a campaign: the skill for the chosen action, or back to
  `fuzz-campaign` when the loop ends. Called by another skill for one
  step: return to it with the result.
- Standalone: stop when the request is done ("why is coverage stuck?":
  the diagnosis and the proposed action). Show the result, using
  the skills' terms, each explained in a few words the first time (e.g.
  "the gate (the harness checks)"), and offer the next step
  without starting it.

