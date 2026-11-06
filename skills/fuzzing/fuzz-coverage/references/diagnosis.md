# Diagnosing coverage gaps and choosing actions

## Reading an annotated function

Classify each top gap by the shape its annotation shows. `coverage.py show`
prints each line with its hit count, `!` for lines never run, and
`<-- k/m branches not taken`. Typical shapes:

| What the annotation shows | Likely cause | Action |
|---|---|---|
| The comparison runs; the branch for the expected value is never taken (`if (memcmp(p, "IHDR", 4) == 0)`, `switch` on a tag): a blocked branch in `gaps` | magic value, keyword or tag the fuzzer hasn't produced | dictionary entries from the guard line (both byte orders for integers) or targeted inputs (corpus.md next to this file); value profiling or CmpLog |
| A checksum, CRC, MAC or signature check passes only for seeds or generated inputs: hit counts collapse at the check | the fuzzer can't compute it, and every mutation breaks it | a fuzzing-only patch (`fuzz-harness`'s build.md), not a generator: generated inputs pass, their mutations don't; triage then checks crashes with valid checksums |
| A length or count check rejects every input (`if (len > remaining) return`) | inconsistent length fields | seeds with consistent lengths; a generator for structured formats |
| A function in scope is never called, and nothing in the harness's call path can reach it | the harness doesn't reach this code | a new target, or a different harness |
| Never called, but reachable through a mode, option, flag, callback or algorithm the harness never selects | a variant the harness doesn't exercise | extend the harness: let input bytes select the variant (only variants deployments use or attackers choose) |
| Never called, but it's the inverse or companion of what the harness calls (serializer, generator, normalizer, compare) | the harness transforms data without checking the result | extend the harness with a round-trip or differential check that calls it (`fuzz-harness`'s properties.md) |
| Code after a state check (`if (!s->handshake_done)`) never runs | needs earlier calls or state | a stateful harness that performs the call sequence |
| Error handling that still processes data never runs | all inputs are valid, or all are rejected earlier | deliberately broken seeds (truncated, wrong magic, out-of-range fields) |
| Deep code is reached rarely; runs are slow or time out | performance | find the slow part; limit input size; move setup to initialization |
| Inputs reach a structured format's checks but byte mutations almost always break its validity (nested lengths, grammar, compressed or encoded layers) | byte-level mutation can't keep the structure | structure-aware escalation (below) |
| Code only for other platforms, allocation-failure paths, debug output, logging | not attack surface | ignore; note it in the target file |
| Code the threat model excludes (trusted configuration, CLI front end) | outside the threat model | ignore; note it; consider removing the file from scope |

- If the code is C/C++ and `fuzz-reachability` is installed: it separates
  an input problem from a harness problem (`fuzz-c-cpp`'s coverage.md,
  "Optional tools").

## Sites to confirm

`sites` reads line coverage, so it marks a site *to confirm* when its line
ran with a branch never taken (`if (x == 42) sink();`): the line ran, but
the call may not have. Check that the call ran (the source and `show`, or
a probe) before counting it.

## Sinks reached but not triggered

Reaching a sink doesn't test it: the coverage build shows only that it
ran. For each group that `SKILL.md`'s "Check the sinks" finds reached but
not triggered:
- Java/JS with a detector that sees the call (what each detector checks:
  `fuzz-java`'s harness.md, "Bug detectors", or `fuzz-js`'s harness.md,
  "Bug detectors"): add seeds or dictionary entries with the
  input that makes it fire, its trigger value (Jazzer typed parameters:
  `@ValuePool`, `fuzz-java`); if real sanitization blocks it, record the
  sink as sanitized, with the evidence;
- Java/JS with a detector that sees it only partly (a command's
  arguments, a resolved path) or not at all, and C/C++ files, processes,
  network and output: add a custom check first (`fuzz-harness`, checks
  beyond crashes); where the harness can't check it, fuzzing doesn't
  detect it. A detector that can't see the sink proves nothing about
  sanitization;
- Java/JS regular expressions applied to input: fuzzing finds only
  exponential backtracking; check them as in redos.md next to this file;
- C/C++ hotspots, after the first plateau: group the hotspot's sites
  (below), read only the top few groups site by site, add boundary values
  for the field that feeds them, and enable value profiling or CmpLog
  (`fuzz-c-cpp`). Optionally build the `ubsan-int` variant.

## C/C++ sink sites

For a hotspot that fuzzing reaches but doesn't trigger, after the first
plateau (above):

1. Grep its code with the patterns of `security-threat-model`'s C/C++
   catalog, and drop sites whose operands are constants or `sizeof`.
2. Group the sites by the input field that feeds them (e.g. `hdr->len`,
   `chunk.size`), rank the groups, and keep the target's groups within the
   cap from Settings (`sink_groups`, default 20). Ranking factors:
   1. arithmetic on the value before it's used as a size;
   2. how many sites the value feeds;
   3. narrowing or sign changes;
   4. complex or missing bounds checks (sites *with* a check stay listed:
      off-by-one bugs live in checks);
   5. past security fixes in the file;
   6. low hit counts in coverage.
3. Add the sites to `sinks/<id>.tsv` (`id file line group kind note`), in
   place of the hotspot's row.

## Choosing the next action

List the candidate actions: one per gap group on the current harness, plus
the top new targets from `fuzz-campaign`'s target ranking. One rule picks
among them: **the most security-relevant code unlocked wins; cost only
breaks ties.**

1. **Class** of the code behind the blocker (the untaken branches, plus
   the code only they lead to), best first: sinks, size or offset
   arithmetic, error handling that still processes attacker data, code
   with past bugs; then ordinary parsing and processing; then logging,
   debug output and version strings. Code outside the threat model or
   unreachable in production doesn't count. A new target counts the code
   in its scope files that no current harness reaches.
2. **Amount:** within a class, the action that unlocks more of it.
3. **Cost,** only between actions that come out alike: seeds and
   dictionary entries, then a harness extension, a build patch, a new
   harness; a rebuild and gate, or a change in how existing input bytes
   are consumed, makes an action dearer.

Apply cheap actions that don't conflict (seeds, dictionary entries, value
profiling) together. Write the action and its aim into the target file
before acting: a code, build or harness change for coverage is kept only
if its aim becomes covered, or scope coverage clearly rises (`SKILL.md`,
"Measure the action").

## Structure-aware escalation

For formats whose validity breaks under byte mutation, after seeds,
dictionaries and targeted inputs didn't open the gap:
- C/C++: a FuzzTest domain (`fuzz-c-cpp`, `fuzztest.md`), or a libFuzzer
  custom mutator that decodes the input, mutates the structure and
  re-encodes it (long runs then stay on libFuzzer);
- Java: Jazzer typed parameters, written through the project's own
  serializer into its parser (`fuzz-java`, `harness.md`);
- JS: a `FuzzedDataProvider` split, or a generator-built structure.

Keep some raw byte mutation (e.g. a selector that sometimes passes the
bytes through), so malformed structure is still tested. Like any harness
change, it's kept or rolled back by `SKILL.md`'s "Measure the action".
