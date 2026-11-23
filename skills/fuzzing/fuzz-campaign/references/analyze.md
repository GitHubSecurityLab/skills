# Analyzing long runs

Track the analysis per run in `CAMPAIGN.md` → Long runs: analysis `no`,
`phase 1`, `done`.

## Phase 1, on the run's builds

For every ended long run (`finished`, `found-crash`, `stopped`, `failed`,
`died`; `launcher.py status`):

1. **Check what ran:** each copy's state, findings, and actual vs. planned
   duration, from `meta.json` and `progress.tsv`. The run's worktree still
   holds its builds, since launch worktrees are read-only. If the worktree
   is gone, rebuild the launch commit in a new worktree for this phase.
2. **Triage** every finding of the run (`fuzz-triage`), reproducing each
   on the build that found it. Each real bug gets its harness-independent
   reproducer here. Set the run's analysis to `phase 1`.

No build selects corpus inputs in phase 1: the run's corpus waits in
`runs/<id>/corpus` (AFL++: `out/<instance>/queue/`) until phase 2. Jest is
the exception: its new inputs are already in, or copied into, the
checkout's `.cifuzz-corpus`.

## Phase 2, on the current build

3. **Fix and report** every real bug (`fuzz-triage`'s fix and report).
4. **Merge and minimize** each harness's corpus with the current build, in
   a merge run `merge-<h>-<n>` (the language skill's run.md, "Merging a
   run's corpus back"). The persistent corpus and the run corpora go into
   the merge run's own corpus, which then replaces the persistent corpus.
   Give the merge run a generous kill limit of a few hours, given in
   seconds (`--timeout <timeout_s>`, e.g. 14400 for 4 hours): the
   per-input `-timeout` already bounds every input, and a killed merge
   leaves its corpus empty.

   After the fixes, inputs of fixed bugs no longer crash, so the merge
   keeps them if they add coverage. The current harness, which may differ
   from the run's, reads the run's inputs. They're still valid inputs,
   unless the harness got an options prefix after the run started: then
   put the prefix's bytes in front of them first (`fuzz-harness`, "Design
   the harness").
   - **Swap only after a merge that `finished`** (not `timed out`,
     `stopped` or `died`), **with no new findings and a non-empty
     corpus.** Findings of known open buckets, with Progress `queued` or
     `needs-decision`, don't block the swap: they wouldn't go away.
     **The swap** replaces `corpus/<h>/` with the merge run's `corpus/`.
     Long runs read the persistent corpus only at start-up, and nothing
     writes to it while its merge runs.
   - If the merge reports findings (e.g. AFL++ inputs that crash only under
     ASan): triage them on the current build and fix them like any other.
     After a fix, rerun the same merge on the fixed build, with a fresh run
     id and an empty output, from the same untouched input directories, and
     swap then. A fix can add branches that make a dropped input useful
     again. Keep the earlier attempts' logs and findings.
   - If the merge didn't finish: rerun it with a fresh run id and a larger
     kill limit; nothing is swapped.
   - If the engine is Jest: it needs no merge.
5. **Measure** coverage once, after the swap, with the current coverage
   build. Replay every harness's corpus, not just the long-run corpora,
   then combine the snapshots as in `SKILL.md`, "Combined coverage".

   Then update each target's sink states as `fuzz-coverage`, "Check the
   sinks", does. Base them on `sites` run on the new snapshots of the
   target's harnesses (`sites` merges several lcov files), and on the
   buckets phase 1 triaged. Step 6 picks what follows.
6. **Sort each harness.** Judge its growth on every copy that meets both
   of these conditions:
   - It didn't end at a finding: its state is `finished`, `stopped`, or
     `died`, and a copy that died is judged up to its last sample. A copy
     cut short by a finding always looks like it's growing.
   - It has at least two progress samples in the last quarter of its own
     elapsed time, in `progress.tsv` or in AFL++'s `plot_data`
     (`edges_found`). `progress.tsv` samples every 5 minutes, so every
     copy that ran 20 minutes has them.

   Sort the harness using these rules:
   - **growing:** any of these copies' coverage counter rose by at least
     1% in its last quarter, or the run reached a scope function or sink
     that the snapshot before the run didn't (step 5);
   - **unknown:** otherwise, if no copy is left to judge, because all
     ended at a finding or lack the samples;
   - **plateaued:** otherwise.

   Then pick the harness's next steps; it can have more than one:
   - growing or unknown: run it longer next time;
   - plateaued with gaps: back to develop;
   - sink groups in state reached, with tries left: targeted inputs
     (`fuzz-coverage`'s corpus.md) before its next run;
   - plateaued, with no gaps and no such sinks: nothing is left worth the
     cost. Once all of a target's harnesses end up here, the target
     becomes `done`.
7. **Write** the summary and next actions, and show the coverage goal if
   one is set. Set the run's analysis to `done`, and offer the next
   long-run commands for the harnesses that run longer next time.
