# Analyzing long runs

Track the analysis per run in `CAMPAIGN.md` → Long runs: analysis `no`,
`phase 1`, `done`.

## Phase 1, on the run's builds

For every ended long run (`finished`, `found-crash`, `stopped`, `failed`,
`died`; `launcher.py status`):

1. **Check what ran:** each copy's state, findings, and actual vs. planned
   duration, from `meta.json` and `progress.tsv`. The run's worktree still
   holds its builds (launch worktrees are read-only); if it's gone,
   rebuild the launch commit in a new worktree for this phase.
2. **Triage** every finding of the run (`fuzz-triage`), reproducing on the
   build that found it. Each real bug gets its harness-independent
   reproducer here. Set the run's analysis to `phase 1`.

No build selects corpus inputs in phase 1: the run's corpus waits in
`runs/<id>/corpus` (AFL++: `out/<instance>/queue/`) until phase 2. Jest is
the exception: its new inputs are already in, or copied into, the
checkout's `.cifuzz-corpus`.

## Phase 2, on the current build

3. **Fix and report** every real bug (`fuzz-triage`'s fix and report).
4. **Merge and minimize** each harness's corpus with the current build: the
   persistent corpus and the run corpora go into the merge run's own
   corpus (`merge-<h>-<n>`; the language skill's run.md, "Merging a
   run's corpus back"), which then
   replaces the persistent corpus. Give it a generous kill limit
   (`--timeout`, hours): the per-input `-timeout` already bounds every
   input, and a killed merge leaves its corpus empty. After the fixes,
   inputs of fixed bugs no longer crash, so the merge keeps them if they
   add coverage. The run's inputs are read by the current harness, which
   may differ from the run's; they're still valid inputs.
   - **Swap only after a merge that `finished`** (not `timed out`,
     `stopped` or `died`), **with no new findings and a non-empty
     corpus.** Findings of known open buckets (Progress `queued` or
     `needs-decision`) don't block it: they wouldn't go away.
     **The swap** replaces `corpus/<h>/` with the merge run's `corpus/`.
     Long runs read the persistent corpus only at start-up, and nothing
     writes to it while its merge runs.
   - If the merge reports findings (e.g. AFL++ inputs that crash only
     under ASan): triage and fix them like any other, on the current
     build.
   - If the merge didn't finish: rerun it with a fresh run id and a larger
     kill limit; nothing is swapped.
   - If the merge found a bug: fix it, then rerun the same merge, with a
     fresh run id and an empty output, from the same untouched input
     directories on the fixed build, and swap then. (A fix can add
     branches that make a dropped input useful again.) Keep the earlier
     attempts' logs and findings.
   - If the engine is Jest: it needs no merge.
5. **Measure** coverage once, after the swap, with the current coverage
   build: replay every harness's corpus, not only the long runs', then
   combine them as in `SKILL.md`, "Combined coverage".
6. **Sort each harness:**
   - still growing at the end (a copy's coverage counter rose by at least
     1% in the last quarter of its own elapsed time, from `progress.tsv`
     or AFL++'s `plot_data` `edges_found`; or the run reached a scope
     function or sink that the snapshot before it didn't, step 5), judged
     on every copy that didn't end at a finding (`finished`, `stopped`, or
     `died` up to its last sample; a copy cut short by a finding always
     looks like it's growing): run it longer next time;
   - plateaued with gaps: back to develop;
   - sinks reached but not triggered, tested or sanitized: directed
     inputs (`fuzz-coverage`'s corpus.md);
   - too few progress samples: "unknown".
7. **Write** the summary and next actions, and show the coverage goal if
   one is set. Set the run's analysis to `done`, and offer the next
   long-run commands.

## A new campaign on a new release

1. Ask the user to stop running runs, and secure the evidence of every
   ended run (phase 1, or a full analysis). Remove their worktrees
   (copying Jest corpus files first). Run corpora that weren't merged stay
   in their run directories.
2. Keep the old branch tip as a ref outside `refs/tags`, so the archived
   ledger can still name its commits and `git push`, `--tags` and `--all`
   never send it: `git update-ref refs/seclab/campaign-<date> HEAD`.
   Archive the old ledger under `archive/<date>/`. Still-open bugs keep
   their bucket files and reproducers.
3. Start a new branch at the release, which also writes the new ledger:
   `init.py --branch new --base <release tag>`; set its status to
   `running`. The old branch stays as it is: it may be a
   `seclab/held-back/*` branch, whose fix must stay there, or the user's
   own. Then bring over the old campaign's commits since its base, without
   the `fuzz(fix)` ones (resolve conflicts, then `git cherry-pick
   --continue`):
   <!-- ci: release-rebase -->
   ```sh
   c=$(git rev-list --reverse --no-merges --invert-grep --grep='^fuzz(fix)' <old_base>..refs/seclab/campaign-<date>)
   [ -z "$c" ] || git cherry-pick $c
   ```
4. Build the new `base` (the release plus our harnesses, without our
   fixes), and remove the old `base` worktree.
5. Run each still-open bug's reproducer on the new base. It doesn't go
   through the harness, so a harness change since the bug was found
   doesn't affect the verdict.
6. Still-open bugs are blocking, so this happens before the gate. For
   each bug:
   - **fixed upstream** (the reproducer passes): mark it; its fix isn't
     re-applied;
   - **still open, with a fix:** cherry-pick the old fix (a held-back
     bug's onto a new held-back branch, `fuzz-triage`'s fix.md, "Commit",
     named `seclab/held-back/<bucket>-<date>`) and rerun the fix's
     validation (`fuzz-triage`), or fix it again if the fix conflicts or
     no longer fixes the bug;
   - **still open, without a fix:** fix it (`fuzz-triage`).
7. Merge the old corpora right away, on the current build of the new
   release: the persistent corpora plus every unmerged run corpus, with
   phase 2's merge rules. Mark those runs `done` in the archived ledger.
   Then update the threat model (`security-threat-model`, incremental
   mode), rank changed code first, run the gate, then develop, long runs
   and analysis as usual.
