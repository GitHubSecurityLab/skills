# Diagnosing coverage gaps and choosing actions

## Reading an annotated function

**First, rule out a fuzzer that got no feedback.** Suspect one when all
three hold for the harness's corpus:
- it's small for how long the harness was fuzzed, e.g. tens of inputs
  after hours;
- it covers little beyond what the seeds reach;
- its gaps sit at many guards at once.

Then the build or options the harness was fuzzed with probably
instrumented only the harness, not the library:
- Java: `with custom hooks only`, or narrow `--instrumentation_includes`;
- JS: `dryRun`, or `includes` that miss the files.

Check the instrumentation of the build the harness was fuzzed with (the
language skill's build.md, "Instrumentation checks"). Without that build,
check its build flags and run logs. If the fuzzer was blind, fixing that
is the action, before any seeds or harness change.

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
not triggered, by language and sink kind:
- **Java/JS, with a detector that sees the whole call:** add seeds or
  dictionary entries with the detector's trigger value, the input that
  makes the detector fire; Jazzer typed parameters use `@ValuePool`
  (`fuzz-java`). If real sanitization blocks the attack, record the group
  as sanitized, with the evidence. Each detector's checks are in
  `fuzz-java`'s harness.md, "Bug detectors", and `fuzz-js`'s harness.md,
  "Bug detectors".
- **Java/JS, with a detector that sees the sink only partly or not at all,
  and C/C++ sinks on files, processes, network or output:** add a custom
  check first (`fuzz-harness`, checks beyond crashes). A detector sees a
  sink only partly when it misses, for example, a command's arguments or a
  resolved path. Where the harness can't check it, fuzzing doesn't detect
  it. A detector that can't see the sink proves nothing about
  sanitization.
- **Java/JS regular expressions applied to input:** fuzzing finds only
  exponential backtracking. Check them as redos.md next to this file says.
- **C/C++ hotspots, after the first plateau:**
  - group the hotspot's sites ("C/C++ sink sites", below);
  - read only the top few groups site by site, and add boundary values for
    the field that feeds each of them (corpus.md next to this file);
  - leave the other kept groups to later rounds, like any other sink group;
  - enable value profiling or CmpLog (`fuzz-c-cpp`), and optionally build
    the `ubsan-int` variant.

## C/C++ sink sites

After the first plateau (above), for a hotspot that fuzzing reaches but
doesn't trigger:

1. Grep its code with the patterns of `security-threat-model`'s C/C++
   catalog, and drop sites whose operands are constants or `sizeof`.
2. Group the sites by the input field that feeds them, e.g. `hdr->len` or
   `chunk.size`. Rank the groups by the factors below, then keep the
   target's top groups within the cap from Settings (`sink_groups`,
   default 20), which counts all of the target's groups. Ranking factors:
   1. arithmetic on the value before it's used as a size;
   2. how many sites the value feeds;
   3. narrowing or sign changes;
   4. complex or missing bounds checks (sites *with* a check stay listed:
      off-by-one bugs live in checks);
   5. past security fixes in the file;
   6. low hit counts in coverage.
3. Add the kept groups' sites to `sinks/<id>.tsv`, in place of the
   hotspot's row. The TSV's columns are `id file line code group kind
   note` (`fuzz-campaign`'s targets.md, "Sinks").

## Choosing the next action

List the candidate actions:
- one for each set of diagnosed gaps that a single action would unlock,
  such as all blocked branches that compare against the same keyword;
- one for each sink group reached but not triggered;
- the top new targets from `fuzz-campaign`'s target ranking.

One rule picks among them: **the most security-relevant code unlocked
wins; cost only breaks ties.**

1. **Class:** the class of the code the action would unlock decides
   first. That code is the untaken branches, plus the code only they lead
   to. Best first:
   1. sinks, size or offset arithmetic, error handling that still
      processes attacker data, code with past bugs;
   2. ordinary parsing and processing;
   3. logging, debug output and version strings.

   Code outside the threat model or unreachable in production doesn't
   count. For a new target, count the code in its scope files, but only
   the code that no current harness reaches.
2. **Amount:** within a class, the action that unlocks more of it wins.
3. **Cost** decides only between actions that come out alike, cheapest
   first: seeds and dictionary entries, a harness extension, a build
   patch, a new harness. A rebuild and gate, or a change in how existing
   input bytes are consumed, makes an action dearer.

When acting, apply cheap actions that don't conflict (seeds, dictionary
entries, value profiling) together. Whether a code, build or harness
change stays is decided later (`SKILL.md`, "Measure the action").

## Structure-aware escalation

For formats whose validity breaks under byte mutation, after seeds,
dictionaries and targeted inputs didn't open the gap:
- C/C++: a FuzzTest domain (`fuzz-c-cpp`'s fuzztest.md), or a libFuzzer
  custom mutator that decodes the input, mutates the structure and
  re-encodes it. With a custom mutator, long runs stay on libFuzzer;
- Java: Jazzer typed parameters, written through the project's own
  serializer into its parser (`fuzz-java`, `harness.md`);
- JS: a `FuzzedDataProvider` split, or a generator-built structure.

Keep some raw byte mutation (e.g. a selector that sometimes passes the
bytes through), so malformed structure is still tested. Like any harness
change, the escalation is kept or rolled back by `SKILL.md`'s "Measure the
action".
