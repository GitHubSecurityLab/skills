# Targets

A target is an entry point where attacker-controlled data enters, plus the
checks that decide what counts as a bug.

## When to use

- "What should I fuzz in this repo?"

Not for writing the harness (`fuzz-harness`) or for threat modelling itself
(`security-threat-model`).

## Reads

- `.seclab/threat-model.md`: Context, Sources, Sinks, Paths. If it's
  missing, invoke `security-threat-model` first.
- `CAMPAIGN.md`: Targets, Harnesses. Merged coverage in
  `.seclab/fuzz/coverage/merged/` when it exists.

## Steps

1. **Recon of existing fuzzing.** Look before writing anything new:
   existing harnesses count as targets already covered, and their gaps are
   candidates for extension, not for new harnesses. Look at:
   - Harnesses in the repo:
     `rg -n -e 'LLVMFuzzerTestOneInput|DEFINE_\w*PROTO_FUZZER|FUZZ_TEST|@FuzzTest|fuzzerTestOneInput|it\.fuzz|module\.exports\.fuzz|__AFL_LOOP'`,
     plus folders such as `fuzz/`, `fuzzing/`, `oss-fuzz/`, `tests/fuzz/`.
   - OSS-Fuzz (optional extra information; nothing depends on it):
     `gh api repos/google/oss-fuzz/contents/projects/<name>`. If the project
     is there, read `project.yaml`, `build.sh` and the harnesses in that
     folder. Public coverage:
     `curl -s https://storage.googleapis.com/oss-fuzz-coverage/latest_report_info/<name>.json`
     points to the latest HTML report and a per-file `summary.json` (replace
     `gs://` with `https://storage.googleapis.com/`). Fuzz Introspector shows
     reachable-but-unfuzzed functions at
     `https://introspector.oss-fuzz.com/project-profile?project=<name>`.
   - Past CVEs and fix commits (from the threat model): the fixed function
     and its siblings are candidates.

   **Baseline:** build the existing harnesses locally (ones kept in
   `google/oss-fuzz`: from copies, language skill) and replay their
   corpora (`fuzz-harness`, `fuzz-coverage`), so ranking uses measured
   coverage. A harness that doesn't build after one attempt is recorded as
   unmeasured, and one still replaying as pending. The repository's seeds
   are often not that corpus: for an OSS-Fuzz project, replay its public
   corpus (`fuzz-harness`'s seeds.md).
   - If a baseline doesn't reach the entry point's callees: it used the
     wrong corpus; record that harness as unmeasured and look for the
     right one.

   Then look for three gaps that existing fuzzing hides, each a strong
   candidate:
   - **modules no harness links:** library sources or build targets that
     appear in no harness's coverage (`summary --scope` reports them as
     having no data: 0%); OSS-Fuzz coverage and Fuzz Introspector don't
     show them at all;
   - **variants of a fuzzed entry point** that no harness calls, with a
     different buffer or state contract (a length-taking or streaming
     variant, an in-place API): the same code under other preconditions;
     rank them by that difference, not by uncovered branches;
   - **harnesses that check nothing:** they call a transformer (serializer,
     normalizer, diff, minifier) without checking its output, or never
     call its inverse; a round-trip or differential property there is a
     cheap extension (`fuzz-harness`'s properties.md).
2. **List candidates** from the threat model's sources, public APIs that
   receive attacker-controlled data (of any type: bytes, strings, files,
   numbers, options, structured values, call sequences), CVE fix sites,
   gaps of existing harnesses, and sinks. For each, fill in the Candidate
   section of `targets/<id>.md` (template in ledger.md next to this
   file):
   - entry point, using the outermost function real callers use;
   - what the attacker controls;
   - **checks**: sanitizers or the engine's detectors, DoS signals, and
     properties. Flag candidates that suit property checks: an
     encode/decode or parse/serialize pair (round-trip), two implementations
     or modes of one operation (differential), normalizers (idempotence),
     and those the project's property-based tests cover (threat model);
   - setup and prior state it needs;
   - how to call it, from the project's tests and examples (`path:line`).
3. **Rank all candidates cheaply.** Rough estimates rank them all; only
   the top 1–3 get scope files and sinks (steps 4–5). Rank in this order:
   1. Drop anything an attacker can't reach under the threat model.
   2. Uncovered attack-surface code the candidate would reach: a rough
      estimate from the module or files it obviously calls, and their
      coverage in the baseline when there is one.
   3. Bug likelihood: sinks and hotspots, complexity, bug history, never
      fuzzed before, changed since the last release (`git diff --stat
      <latest tag>..HEAD`; a new campaign on a new release ranks it first).
   4. Cost: setup and state the harness must build, external dependencies,
      build difficulty. Among similar candidates, the cheaper one goes first.
4. **Scope files for the top 1–3 only** (static estimate). A target's
   coverage counts only its scope files, where its attacker-controlled
   data is processed. Starting at the entry point, follow the calls with
   `rg`, ctags or the agent's code navigation. Keep files where attacker
   data is processed (parsing, decoding, validation, sinks); stop at code
   the threat model excludes (logging, CLI front ends, tests, vendored
   code, trusted configuration). If the code is C/C++ and
   `fuzz-reachability` from the AFL++ project is installed: it can replace
   the manual walk (`fuzz-c-cpp`'s coverage.md, "Optional tools"). If the
   code is TypeScript (`fuzz-js`'s coverage.md, "TypeScript"): build the
   candidate's modules first (seconds; `fuzz-js`'s build.md) and write
   their `.js` paths here, and their lines in step 5; the reasons name the
   `.ts` files. Write each file with a
   one-line reason into the target file, and the bare paths into
   `targets/<id>.scope`. With coverage data, measure the scope; its
   uncovered branches may reorder the top candidates:
   `python3 .seclab/fuzz/bin/coverage.py summary .seclab/fuzz/coverage/merged/<latest>.lcov --scope .seclab/fuzz/targets/<id>.scope --root .`.
   Its branch coverage (0% without data) starts the target's Scope
   branches in the index.
5. **Sinks** for the same top candidates, at most `sink_groups` groups
   (Settings) per target in every language, the threat model's paths
   first.
   - Java/JS: the threat model's sinks that fall into the scope files, one
     group per sink kind and function, with whether the engine's
     detector sees each: yes, partly or no.
   - C/C++: files, processes, network and output sinks as for Java/JS (no
     detector sees them); for memory safety only the threat model's
     hotspots in the scope files, one row per hotspot function at its
     first statement (kind `hotspot`). Site
     groups come later, after the first plateau and only for hotspots
     reached but not triggered (`fuzz-coverage`, "Check the sinks").
   - Write them to `sinks/<id>.tsv` (header: `id file line group kind note`,
     tab-separated) and a summary ("20 of 140 groups tracked") into the
     target file.
6. **Present.** Interactive mode shows the ranked list with checks and
   scope at the targets checkpoint (`fuzz-campaign`), with a proposed
   stopping rule. Autonomous mode takes the candidates its stopping rule
   selects, top first.

**Refresh** when the outer loop picks the next target: re-rank with the
current merged coverage, mark done targets, add candidates that coverage
analysis surfaced, and do steps 4–5 for new top candidates.

## Done when

- Every candidate has an entry point, attacker control and checks; the top
  1–3 also have scope files with reasons and sinks (C/C++: hotspots).
- The ranking is written with a one-line reason per target.
- Existing harnesses were built and replayed first, or recorded as
  unmeasured (or pending, if the user chose to rank on partial results).

## Pitfalls and shortcuts to reject

- Ranking by "this function looks important". Rank by uncovered attack
  surface.
- Internal helpers as entry points. Bugs there may be impossible through
  the real API, and they make false positives likely.
- A CLI `main()` as the target when the code is used as a library. Fuzz the
  library API unless the CLI on untrusted files is the deployment.
- A new target whose workflow an existing harness already contains. Extend
  that harness instead.
- Grouping C/C++ sink sites before the first plateau (step 5).

## Writes

- `targets/<id>.md`: Candidate; for the top 1–3, Scope files and Sinks,
  `targets/<id>.scope` and `sinks/<id>.tsv`.
- `CAMPAIGN.md`: Targets table (status `candidate`, rank order; scope
  branches when the scope is set).
