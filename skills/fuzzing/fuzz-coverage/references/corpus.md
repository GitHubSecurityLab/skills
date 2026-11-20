# Targeted inputs and corpus upkeep

The corpus the fuzzer builds on top of the seeds is the campaign's most
valuable artifact: keep it, minimize it with the right tool, and resume
from it.

## Steps

1. **Targeted inputs** do one of two jobs: cover a blocked branch from
   `gaps`, to reach the code behind it, or trigger a sink that's reached
   but not yet triggered. Both start from inputs that already reach the
   branch's guard or the sink; a generator varies them toward what the
   guard or the sink needs.

   Typical inputs:
   - a check on a magic value or keyword: an input with the expected value;
   - a length that must be consistent: compute it in the generator (a
     checksum needs a fuzzing-only patch: diagnosis.md next to this file);
   - a sink group that is reached but not triggered: boundary values in
     the field that feeds it: 0, 1, the buffer size and the buffer size
     ±1, the type's maximum, values whose arithmetic wraps (`0x7fffffff`,
     `0x80000000`, `0xffffffff`, and for 16 bits `0x7fff`, `0x8000`,
     `0xffff`).

   1. Find the starting points: inputs that already reach the guard or
      the sink, with `reach.py` (the language skill's `coverage.md`):
      - if the corpus reaches the guard or the sink along more than one
        path, e.g. its function is called from several places that have
        hits in `show`, read how each path handles the input from the
        harness to the guard or the sink: what it checks, clamps or
        transforms, e.g. one path rejects a length over 255 and another
        adds a header size to it. For each different handling, run
        `reach.py`, with `--max 1`, on a line only that path runs, such as
        its call line: one path may let through what another rejects;
      - for a sink whose condition is on the same line, e.g.
        `if (x == 42) sink();`, confirm that the call ran: `reach.py`
        reads line coverage, like `sites`.
   2. State what the variants need:
      - for a sink, the condition that breaks it: the value at the sink
        that triggers the bug, e.g. one that overruns the buffer, wraps
        the size, leaves the base directory or names a class from input;
      - how each path checks and transforms the input on its way to the
        guard or the sink: the variants must pass the checks, and the
        transforms decide which input gives the value needed there.
   3. Write a generator under `generators/` that varies the starting
      inputs toward the guard or that condition (e.g. the value it
      compares, a consistent length, the field that feeds the sink),
      within those checks. Keep it, to rerun when the code changes.
   4. Replay the variants alone right away (the language skill's
      coverage.md, "Exact subset"), and `show` the function of the guard
      or the sink:
      - if the guard or the sink has no hits, a check on the way rejects
        the variants: find it by following the call path back to the
        deepest line they reach, with `show`, and fix the generator;
      - for a branch, add the variants to the corpus: those that cover
        the branch, or new code on the way to it, stay;
      - for a sink they reach, put the variants into the corpus and run a
        round (`fuzz-coverage`, "Run a round"), which runs them first: a
        sink's trigger shows only on the sanitizer or detector build.
   5. Repeat from step 2 until the branch is covered, or until the
      variants reach the sink and have run in a round. Stop after four
      tries. For C/C++, value profiling or CmpLog can also help the
      fuzzer climb toward the value.

2. **Corpus upkeep:** one corpus per harness, whichever engine runs it.
   Develop rounds write into it; long runs write into their own run
   directory.
   - When a long run is analyzed (`fuzz-campaign`), its corpus is merged
     back on the current build, with the commands in the language skill's
     run.md, "Merging a run's corpus back". An AFL++ queue is merged the
     same way, with the libFuzzer ASan+UBSan build. Either merge also
     minimizes.
   - Minimize the persistent corpus alone, with the same command but
     without a run's corpus. For Jest fuzz tests, minimize only when the
     user asks (the language skill); for other harnesses, minimize:
     - when the develop loop ends, before the long-run commands;
     - when it passes about 10,000 files, including right after an
       import: every replay, `reach.py` and a long run's start load all of
       them;
     - when the user asks.
   - Swap a merge's result in for the persistent corpus only if all three
     hold:
     - the merge's run state is `finished`;
     - the merge reported no new findings;
     - its output corpus isn't empty.

     A killed merge leaves its output corpus empty (`fuzz-campaign`'s
     analyze.md).
   - If a harness change consumes input differently: keep the old corpus
     as seeds, but expect it to be worth less.
