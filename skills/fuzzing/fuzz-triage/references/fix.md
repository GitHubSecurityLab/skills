# Fix

A tested fix makes a report far more useful to maintainers. Fix commits
reveal the bug. For a bug the hold-back rule covers (`fuzz-campaign`,
safety rules), the fix commits stay on a local branch of their own until
the bug is public and fixed upstream.

## Reads

What the steps in this file read. Paths are relative to `.seclab/fuzz/`
unless they start with `.seclab/`:
- `crashes/<bucket>.md`: Root cause, Reproduce, inputs in
  `crashes/<bucket>/`, reproduction logs in `runs/repro-<h>-<n>/`.
- `targets/<id>.md`: Harness, build variants.
- The language skill: regression test style and sanitizer builds.

## Steps

1. **Check the precondition, then freshness.** The precondition: the
   bucket has a root cause and a real-bug verdict. If it lacks either,
   triage it first (`fuzz-triage`'s steps). Then check freshness: make
   sure upstream hasn't fixed the bug already. Run the bug's reproducer
   on upstream's HEAD and on its latest release.
   - **Find the latest release** (`<release_tag>`) in the release
     metadata: `gh release list --limit 5 --json tagName,publishedAt`,
     which covers every branch. If that doesn't identify the latest
     release, use the project's changelog or the tags sorted by date.
     Don't use `git describe` on HEAD: it misses releases cut from
     maintenance branches.
   - **Check out both** as the git worktrees `fresh-head` and
     `fresh-release` in `<checkout>/../<repo>.seclab-worktrees/`. The
     project's remote is `upstream` in a fork's clone, and `origin`
     otherwise:
     <!-- ci: fresh-worktrees -->
     ```sh
     R=origin; git remote get-url upstream > /dev/null 2>&1 && R=upstream
     git fetch --tags "$R" && git remote set-head "$R" -a > /dev/null
     W="<checkout>/../<repo>.seclab-worktrees"
     [ -d "$W/fresh-head" ] || git worktree add --detach "$W/fresh-head" "$R/HEAD"
     git -C "$W/fresh-head" checkout -q --detach "$R/HEAD"
     [ -d "$W/fresh-release" ] || git worktree add --detach "$W/fresh-release" <release_tag>
     git -C "$W/fresh-release" checkout -q --detach <release_tag>
     ```
     Later freshness checks move the same worktrees to the new commits.
   - **Build and run the reproducer in each.** The reproducer calls the
     project's API directly, not through a harness, so build only what it
     needs (`fuzz-harness`'s build.md and runs.md). Then run it as the
     language skill's regression.md, "The bug's reproducer", says.
   - **Record** both results in the bucket's Evidence: they show which
     versions are affected.
   - If the reproducer passes on HEAD, upstream fixed the bug. Find the
     fixing commit with `git log` on the files involved, and record it as
     Fixed upstream (verdicts.md, "State and Fixed upstream"). Then:
     - If the bug is blocking, use upstream's commit as our fix. Do step
       2, then apply the commit with `git cherry-pick -n <sha>` in place
       of step 3, and go on with steps 4, 6, 7 and 8; step 5's review is
       upstream's. Step 6's commit names the upstream commit, and its
       branch rule applies as for any fix.
     - Otherwise the bug needs no fix of ours. It goes on to report.md
       only if the hold-back rule covers it (`SKILL.md`, "Route").
2. **The regression test is the bug's reproducer** from
   `crashes/<bucket>/` (`SKILL.md`, "Write the reproducer"). Run it as
   the language skill's regression.md, "The bug's reproducer", says: it
   must fail on the current code. Turn it into a test in the project's
   own framework and style, named after the bug.
   - If the project is C/C++, its tests build with sanitizers
     (`fuzz-c-cpp`'s regression.md, "Sanitizer build for the tests").
     Memory bugs often don't fail without sanitizers; the test's comment
     says so.
3. **Write the fix.** Keep it minimal and at the root cause: the missing
   or wrong check, the incorrect size computation, the unsafe API use.
   Fixing only the line that crashed isn't enough. Follow the project's
   style and error-handling conventions.
   - If the same mistake appears in sibling code, note those places in
     the bucket file. Fix them only when they're part of the same root
     cause.
   - If no safe fix is possible, follow "When no safe fix is possible"
     below.
4. **Validate.** All of the checks below must hold. For a queued bug's
   fix (`SKILL.md`, Route), run the reproducer, its regression test and
   the replay checks now. Run the other three checks once for all the
   fixes of the batch; if the batch fails, check its fixes one at a time.
   The checks:
   - The reproducer, and the regression test made from it, now pass.
     This check decides.
   - If the harness is unchanged since the inputs were found: on the
     fixed build, replay the bucket's kept inputs
     (`crashes/<bucket>/inputs/`) and one input of every other open
     bucket of the harness, as `SKILL.md`, "Reproduce" and "Group", say.
     - An input of this bucket that still crashes is another bug: a new
       bucket.
     - Another bucket that stops crashing is a duplicate candidate.
       Confirm it by root cause, with its reproducer passing with the fix
       and failing without it (a fix can also mask a bug).
   - The project's own tests pass. They're project code, so the
     first-build notice applies to them too.
   - The harness gate passes again (`fuzz-harness`).
   - One fuzzing round from the corpus, of the schedule's first length,
     runs its full time without hitting the bug again. A round that
     another bug cuts short isn't evidence. Skip or fix that other bug
     first (`SKILL.md`, "Blocking bugs"), or record "cut short at
     <bucket> after <n> s".
5. **Review what the patch means.** Passing checks don't make a fix right.
   Answer each question below in the bucket's Fix section, and go back to
   step 3 on any no. Write n/a where a question doesn't apply (e.g. the
   patch has no early return). The questions:
   - Does it repair the root cause where the bad state is computed,
     rather than clamp at the crash site, cap a loop or catch the error?
   - Are the guard's operands trusted (the buffer's real length, not a
     length field from the input)?
   - Does an early return or `goto` release what was allocated and leave
     the state callers see valid?
   - Does the check also hold for sibling call sites, off-by-one values
     and wrap-around?
   - Do valid inputs behave as before: the same outputs, or the same
     coverage of the core function, on the harness's valid seeds?
6. **Commit** the fix with its regression test as one commit:
   `fuzz(fix): <bucket> <one-line summary>`. Never push it.

   The fix of a bug the hold-back rule covers (`fuzz-campaign`, safety
   rules) never goes on a branch that may be pushed. For such a fix,
   switch to a new local branch before the commit:
   <!-- ci: held-back-branch -->
   ```sh
   git switch -c seclab/held-back/<bucket>
   ```
   Commit the fix there, and continue the campaign on that branch, since
   the campaign's builds need the fix. Set Branch in `CAMPAIGN.md` to the
   new branch and log the switch in `decisions.md`. Later commits,
   harnesses included, land on that branch. The next held-back fix stacks
   its own branch on top.

   Tell the user, now and in every later summary, that the branch they
   started on doesn't have the fix. Also tell them which branches must
   not be pushed (the hold-back rule). The branch they started on is one
   of them if its harnesses reach the bug.
7. **Record** in the bucket's Fix section: the commit, each validation
   result, the review's answers, and any sibling places noted. Set the
   bucket's Progress to `fixed-locally`. For a queued bug, do that only
   after the batch's checks (step 4); until then, Progress stays `queued`
   and the bug's report waits. The fix counts as mechanically validated
   until the user reviews the patch (report.md, "Report checkpoint").
8. **Clean up after the fix:**
   - If the bucket's original input is missing from the harness's
     persistent corpus (a merge dropped it while it crashed), copy it
     back. It now runs code the fuzzer couldn't pass before.
   - If the bug is a leak in `leaks/<h>.supp`, remove its line.
   - If long runs are going on the unfixed build, leave them running:
     they belong to the user. Their analysis (`fuzz-campaign`) offers a
     new run's command on the fixed build.

## When no safe fix is possible

Fixing before reporting is the default. The report goes out without a
patch when:
- the root cause is unclear;
- the problem is at the design level;
- the fix needs a large refactor;
- the fix needs the maintainers' knowledge of the intended behaviour.

In interactive mode, ask the user before going without a fix, with the
reason. They may know the intended behaviour or accept the large
refactor; then go back to step 3. For a blocking bug, also say that the
target is then set aside. Autonomous mode doesn't ask (`fuzz-campaign`'s
autonomous.md).

The report then says why: "Suggested fix: none" and the reason
(report.md). Set the bucket's Progress to `needs-decision`. If the bug
blocks fuzzing and the engine can't skip it, the target is set aside
(`SKILL.md`, "Blocking bugs").

Never "fix" by suppressing the symptom: catching the exception, removing
the check that fires, raising a limit, or disabling a sanitizer.

## Done when

- Either the fix commit exists, every validation check passed and is
  recorded, and the review's questions are answered; or the no-fix path
  was taken and recorded.

## Pitfalls and shortcuts to reject

- Validating with the raw crash input after the harness changed: its bytes
  may now mean something else. The reproducer decides.
- A fix that silences the sanitizer report but leaves the root cause
  (clamping an index after the out-of-bounds read already happened).
- Fixing in the harness what is a bug in the target, or the other way round.
- Pushing fix commits. The user pushes.
- Putting a held-back bug's fix (`fuzz-campaign`, safety rules) into any
  branch meant for upstream: it reveals the bug. Other fixes go upstream
  on their own branches (`fuzz-campaign`, upstreaming.md), not inside
  harness pull requests.

## Writes

What the steps in this file write or change. Paths are relative to
`.seclab/fuzz/` unless they start with `.seclab/`:
- A `fuzz(fix): ...` commit with the regression test made from the
  reproducer; for a held-back bug, on a new `seclab/held-back/<bucket>`
  branch.
- `crashes/<bucket>.md`: Evidence (HEAD, latest release), Fixed upstream,
  Fix. `CAMPAIGN.md`: Open crashes (Progress).
