# A new campaign on a new release

1. Ask the user to stop the runs still going, and secure the evidence of
   every ended run (analyze.md's phase 1, or a full analysis). Then
   remove the ended runs' worktrees (long-runs.md, "Cleanup"), copying
   Jest corpus files out first. Run corpora that weren't merged stay in
   their run directories.
2. Keep the old branch tip as a ref outside `refs/tags`:
   `git update-ref refs/seclab/campaign-<date> HEAD`. The archived ledger
   can then still name the old branch's commits, and `git push`, `--tags`
   and `--all` never send the ref. Archive the old ledger under
   `archive/<date>/`. Still-open bugs keep their bucket files and
   reproducers.
3. Start a new branch at the release with
   `init.py --branch new --base <release tag>`, which also writes the new
   ledger. Set the new ledger's status to `running`. The old branch stays
   as it is: it may be a `seclab/held-back/*` branch, whose fix must stay
   there, or the user's own branch. Then bring over the old campaign's
   commits since its base, without the `fuzz(fix)` ones:
   <!-- ci: release-rebase -->
   ```sh
   c=$(git rev-list --reverse --no-merges --invert-grep --grep='^fuzz(fix)' <old_base>..refs/seclab/campaign-<date>)
   [ -z "$c" ] || git cherry-pick $c
   ```
   If a commit conflicts, resolve the conflict, then run
   `git cherry-pick --continue`.
4. Remove the old `base` worktree, then build the new `base`: the release
   plus our harnesses, without our fixes.
5. Run each still-open bug's reproducer on the new base. The reproducer
   doesn't go through the harness, so a harness change since the bug was
   found doesn't affect the verdict.
6. Still-open bugs are blocking, so they're handled before the gate. For
   each bug:
   - **fixed upstream** (the reproducer passes): mark it. Its fix isn't
     re-applied.
   - **still open, with a fix:** for a held-back bug, use a new
     `seclab/held-back/<bucket>-<date>` branch for the cherry-pick
     (`fuzz-triage`'s fix.md, "Commit"). Cherry-pick the old fix. If it
     applies, rerun its validation (`fuzz-triage`). If the cherry-pick
     conflicts or the fix no longer fixes the bug, fix the bug again
     instead.
   - **still open, without a fix:** fix it (`fuzz-triage`).
7. Merge the old corpora right away, on the current build of the new
   release: the persistent corpora plus every unmerged run corpus, as in
   analyze.md, "Merge and minimize". Mark those runs' analysis `done` in
   the archived ledger.

   Then update the threat model (`security-threat-model`, incremental
   mode), and rank changed code first. For each old target with scope
   files:
   - update the scope files for files the release moved, removed or
     added;
   - write the target's sinks again (targets.md, "Sinks"): their files
     and lines may have changed, so their states start over.

   Then run the gate, develop, long runs and analysis as usual.
