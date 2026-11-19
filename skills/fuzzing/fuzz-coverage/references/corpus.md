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
      the sink, with `reach.py` (the language skill's `coverage.md`). For a
      sink whose condition is on the same line, e.g. `if (x == 42) sink();`,
      confirm that the call ran: `reach.py` reads line coverage, like
      `sites`.
   2. For a sink, state the condition that breaks it: the value at the
      sink that overruns the buffer, wraps the size, leaves the base
      directory or names a class from input.
   3. Write a generator under `generators/` that varies the starting
      inputs toward the guard or that condition (the value it compares, a
      consistent length, the field that feeds the sink).
   4. Replay its variants right away (the language skill's coverage.md)
      and check whether the branch became covered. For a sink, also put
      the variants into the corpus and run a round (`fuzz-coverage`, "Run
      a round"), which runs them first: a sink's trigger shows only on the
      sanitizer or detector build.
   5. Iterate two to four times. For C/C++, value profiling or CmpLog can
      also help the fuzzer climb toward the value.
   6. Make sure the near-misses are in the corpus, and keep them there. A
      minimizing merge drops those that add no coverage; for sink groups
      whose state is still reached, corpus upkeep re-creates them before
      long runs.

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
   - After minimizing the corpus before the long runs, rerun the
     generators of sink groups whose state is still reached (not tested or
     sanitized). Add their output to the corpus: a few hundred inputs at
     most, and none that crash. Minimizing dropped their near-misses.
   - If a harness change consumes input differently: keep the old corpus
     as seeds, but expect it to be worth less.
