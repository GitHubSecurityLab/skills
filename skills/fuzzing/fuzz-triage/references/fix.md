# Fix

A tested fix makes a report far more useful to maintainers, and fixing a
bug that fires on most inputs is the only way to keep fuzzing the code
behind it. Fix commits reveal the bug; those of a bug the hold-back rule
covers (`fuzz-campaign`, safety rules) stay on a local branch of their own
until it's public and fixed upstream.

## When to use

- `fuzz-triage` assigned a real-bug verdict and found the root cause.
- A bug fires on almost every input and blocks fuzzing: fix it right away.
- "Fix this crash", "write a patch for the bug the fuzzer found".

Not for bugs in our harness (`fuzz-harness`) or untriaged crashes
(triage them first, `SKILL.md`).

## Reads

- `crashes/<bucket>.md`: Root cause, Reproduce, inputs in
  `crashes/<bucket>/`, reproduction logs in `runs/repro-<h>-<n>/`.
- `targets/<id>.md`: Harness, build variants.
- The language skill: regression test style and sanitizer builds.

## Steps

1. **Check the precondition, then freshness.** The bucket has a root
   cause and a real-bug verdict; otherwise triage it first
   (`fuzz-triage`'s steps). Before any fix work, run the reproducer on
   upstream HEAD and the latest release (report.md, "Freshness"), so no
   fix is written for a bug upstream already fixed.
   - If HEAD already fixes the bug: record Fixed upstream. A blocking bug
     gets upstream's fixing commit, cherry-picked as its `fuzz(fix)`
     commit and validated as below; any other bug needs no fix of ours.
2. **The regression test is the bug's reproducer** from
   `crashes/<bucket>/` (`SKILL.md`, "Write the reproducer"). Run it
   (the language skill's regression.md, "The bug's reproducer"): it
   must fail on the current code.
   Turn it into a test of the project's own framework, in the project's
   style, named after the bug.
   - If the project is C/C++: its tests build with sanitizers
     (`fuzz-c-cpp`'s regression.md, "Sanitizer build for the tests");
     memory bugs often don't fail without sanitizers, and the test's
     comment says so.
3. **Write the fix.** Minimal and at the root cause: the missing or wrong
   check, the incorrect size computation, the unsafe API use. Not only the
   line that crashed. Follow the project's style and error-handling
   conventions.
   - If the same mistake appears in sibling code: note those places in
     the bucket file; fix them only when they're part of the same root
     cause.
   - If no safe fix is possible: follow "When no safe fix is possible"
     below.
4. **Validate.** All of these must hold. A queued bug's fix (`SKILL.md`,
   Route) checks the first two now, and the last three once for all the
   fixes of the batch; if the batch fails, check its fixes one at a time:
   - the reproducer, and the regression test made from it, now pass. This
     decides;
   - while the harness is unchanged since the inputs were found, replay
     the bucket's kept inputs (`crashes/<bucket>/inputs/`) and one input
     of every other open bucket of the harness on the fixed build (`SKILL.md`,
     "Reproduce" and "Group"). A bucket input that still crashes is another bug: a
     new bucket. Another bucket that stops crashing is a duplicate
     candidate: confirm it by root cause, with its reproducer passing with
     the fix and failing without it (a fix can also mask a bug);
   - the project's own tests pass (same first-build caution: they're project
     code);
   - the harness gate passes again (`fuzz-harness`);
   - one fuzzing round from the corpus (the schedule's first length) runs
     its full time without hitting the bug again. A round another bug cuts
     short isn't evidence: fix or filter that one first, or record "cut
     short at <bucket> after <n> s".
5. **Review what the patch means.** Passing checks don't make a fix right.
   Answer each question in the bucket's Fix section, and go back to step 3
   on any no:
   - Does it repair the root cause where the bad state is computed,
     rather than clamp at the crash site, cap a loop or catch the error?
   - Are the guard's operands trusted (the buffer's real length, not a
     length field from the input)?
   - Does an early return or `goto` release what was allocated and leave
     the state callers see valid?
   - Can sibling call sites, off-by-one values or wrap-around still get
     past the check?
   - Do valid inputs behave as before: the same outputs, or the same
     coverage of the core function, on the harness's valid seeds?
6. **Commit** the fix with its regression test as one commit:
   `fuzz(fix): <bucket> <one-line summary>`. Never push it. The fix of a
   bug the hold-back rule covers (`fuzz-campaign`, safety rules) never
   goes on a branch that may be pushed: commit it on a new local branch,
   and continue the campaign there, since its builds need the fix:
   <!-- ci: held-back-branch -->
   ```sh
   git switch -c seclab/held-back/<bucket>
   ```
   Set Branch in `CAMPAIGN.md` to it and log the switch in
   `decisions.md`. Later commits, harnesses included, land on it; the
   next held-back fix stacks its own branch on top. Tell the user, now and
   in every later summary, that the branch they started on doesn't have
   the fix, and which branches must not be pushed (the hold-back rule),
   that one included if its harnesses reach the bug.
7. **Record** in the bucket's Fix section: the commit, each validation
   result, the review's answers, and any sibling places noted. Set the
   bucket's Progress to `fixed-locally`. The fix counts as mechanically
   validated until the user reviews the patch (report.md, "Report checkpoint").
8. **Clean up after the fix:**
   - If the bucket's original input is missing from the harness's
     persistent corpus (a merge dropped it while it crashed): copy it
     back; it now runs code the fuzzer couldn't pass before.
   - If the bug is a leak in `leaks/<h>.supp`: its line comes out.
   - If long runs are going on the unfixed build: leave them running;
     they belong to the user. Their analysis (`fuzz-campaign`) offers a
     new run's command on the fixed build.

## When no safe fix is possible

Fixing before reporting is the default. With an unclear root cause, a
design-level problem, a large refactor, or a fix that needs the
maintainers' knowledge of the intended behaviour, the report goes out
without a patch and says why ("Suggested fix: none" and the reason,
report.md). Set the bucket's Progress to `needs-decision`. If the bug
blocks fuzzing, filter its inputs in the harness (`SKILL.md`, blocking
bugs); set the target aside only when no precondition can tell them
apart and the engine can't skip them. Interactive mode asks the user
first.

Never "fix" by suppressing the symptom: catching the exception, removing
the check that fires, raising a limit, or disabling a sanitizer.

## Done when

- The fix commit exists, every validation check passed and is recorded,
  the review's questions are answered, or the no-fix path was taken and
  recorded.

## Pitfalls and shortcuts to reject

- Validating with the raw crash input after the harness changed: its bytes
  may now mean something else. The reproducer decides.
- A fix that silences the sanitizer report but leaves the root cause
  (clamping an index after the out-of-bounds read already happened).
- Fixing in the harness what is a bug in the target, or the other way round.
- Pushing fix commits (the user pushes), or putting a held-back bug's fix
  (`fuzz-campaign`, safety rules) into any branch meant for upstream: it
  reveals the bug. Other fixes go upstream on their own branches (`fuzz-campaign`,
  handoff.md), not inside harness pull requests.

## Writes

- A `fuzz(fix): ...` commit with the regression test made from the
  reproducer; for a held-back bug, on a new `seclab/held-back/<bucket>`
  branch.
- `crashes/<bucket>.md`: Fix. `CAMPAIGN.md`: Open crashes (Progress).
