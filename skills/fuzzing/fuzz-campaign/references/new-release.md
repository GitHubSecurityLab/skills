# A new campaign on a new release

1. Ask the user to stop running runs, and secure the evidence of every
   ended run (analyze.md's phase 1, or a full analysis). Remove their
   worktrees (long-runs.md, "Cleanup"; copying Jest corpus files first).
   Run corpora that weren't merged stay in their run directories.
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
   release: the persistent corpora plus every unmerged run corpus, as in
   analyze.md, "Merge and minimize". Mark those runs `done` in the
   archived ledger. Then update the threat model (`security-threat-model`,
   incremental mode) and rank changed code first. For each old target with
   scope files, update them for files the release moved, removed or added,
   and write its sinks again (targets.md, "Sinks"): their files and lines
   may have changed, so their states start over. Then run the gate,
   develop, long runs and analysis as usual.
