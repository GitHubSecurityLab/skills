# Targeted inputs and corpus upkeep

The corpus the fuzzer builds on top of the seeds is the campaign's most
valuable artifact: keep it, minimize it with the right tool, and resume
from it.

## Steps

1. **Targeted inputs** for a gap: a blocked branch
   from `gaps`, or a sink that's reached but not triggered (reach, then
   trigger). Inputs that already reach the gap are the starting points; a
   generator varies them toward what the guard or the sink needs.
   1. Find inputs that already reach it: `reach.py` (the language skill's
      `coverage.md`), the starting points. If it's a sink behind a
      condition on its own line: confirm that the call ran (`reach.py`
      reads line coverage, like `sites`).
   2. For a sink, state the condition that breaks it: the value at the
      sink that overruns the buffer, wraps the size, leaves the base
      directory or names a class from input.
   3. Write a generator under `generators/` that varies the starting
      inputs toward the guard or that condition (the value it compares, a
      consistent length, the field that feeds the sink).
   4. Replay its variants right away (the language skill's coverage.md)
      and check whether the gap became covered. If it's a sink: its
      trigger shows only on the sanitizer or detector build; put the
      variants into the corpus and run a round
      (`fuzz-coverage`, "Run a round"), which runs them first.
   5. Iterate 2–4 times; for C/C++, value profiling or CmpLog can also help
      the fuzzer climb toward the value.
   6. Keep the near-misses in the corpus. A minimizing merge drops those
      that add no coverage; corpus upkeep re-creates them before long runs.

   Typical inputs:
   - a check on a magic value or keyword: a seed with the expected value;
   - a length that must be consistent: compute it in the generator (a
     checksum needs a fuzzing-only patch: diagnosis.md next to this file);
   - a sink group that is reached but not triggered: boundary values in the
     field that feeds it: 0, 1, the buffer size and ±1, the type's maximum,
     values whose arithmetic wraps (`0x7fffffff`, `0x80000000`,
     `0xffffffff`, and for 16 bits `0x7fff`, `0x8000`, `0xffff`).
2. **Corpus upkeep:** one corpus per harness, whichever engine runs it.
   Develop rounds write into it; long runs write into their own run
   directory.
   - A long run's corpus is merged back when the run is analyzed
     (`fuzz-campaign`), with the current build and the language skill's
     run.md, "Merging a run's corpus back" (for AFL++ queues too: the
     libFuzzer ASan+UBSan build).
     The merge also minimizes.
   - Minimize the persistent corpus alone with the same command (without a
     run's corpus) when the develop loop ends, before the long-run
     commands; when it passes about 10,000 files (every replay, `reach.py`
     and a long run's start load all of them); after importing a large
     external corpus; and when the user asks. Jest fuzz tests: only on
     request (the language skill).
   - After the minimizing before the long runs, rerun the generators of
     sinks still reached but not triggered into the corpus, a few hundred
     inputs at most and none that crash: the merge dropped their
     near-misses.
   - Swap a merge's result in only after it `finished`, with no new
     findings and a non-empty corpus: a killed merge leaves its corpus
     empty (`fuzz-campaign`'s analyze.md).
   - If a harness change consumes input differently: keep the old corpus
     as seeds, but expect it to be worth less.
