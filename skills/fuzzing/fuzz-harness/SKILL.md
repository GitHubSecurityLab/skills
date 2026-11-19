---
name: fuzz-harness
description: >-
  Writes or extends a fuzz harness (fuzz test) for one entry point in the
  project's test style, builds its variants, adds seeds and a dictionary,
  and checks it with the harness gate. Use for "write a fuzz test for X",
  "build this with ASan and libFuzzer", or to check on or stop a fuzzing
  run. Not for choosing targets (fuzz-campaign).
license: MIT
---

# Fuzz harness

A harness turns fuzzer input into calls on the target. A good one exercises
exactly what the attacker controls, through the same entry point real
callers use, fast and deterministically. Its own files go to scratch and
are removed; the target's side effects aren't stubbed. Every harness
change goes through build and gate before it runs.

**Run every step** this skill gives for the request; skip one only when the user asks. Later steps rely on what earlier ones measured and recorded, so a skipped step makes later results wrong, not the work faster.

## When to use

- Another skill needs a new or changed harness.
- "Write a fuzz test for `parse_header`."
- "Build this with ASan and libFuzzer", "build this for fuzzing": the build
  variants alone ([references/build.md](references/build.md)).
- "Make seeds and a dictionary for this harness"
  ([references/seeds.md](references/seeds.md)).
- "Run this harness for ten minutes", "is the run still going?": a develop
  run through the launcher ([references/runs.md](references/runs.md)).

Not for choosing targets (`fuzz-campaign`) or improving coverage with
targeted inputs (`fuzz-coverage`).

## Start

Before anything else, and again whenever you resume, run from the
repository root and follow what it prints:

```sh
python3 <this skill's directory>/../fuzz-campaign/scripts/init.py
```

- The safety rules it prints apply here too.
- Exit 2 (no ledger yet): rerun with `--branch current`.
- Ledger status `standalone`: before the first commit, ask whether the work
  moves to a new `seclab/fuzz-<date>` branch (`--branch new`); read-only
  requests never ask.
- If `init.py` is missing, the skill was installed without its plugin: ask
  the user to install the `seclab-skills` plugin; don't look for another
  copy.

## Reads

What the steps in this file read. Paths are relative to `.seclab/fuzz/`
unless they start with `.seclab/`:
- `.seclab/threat-model.md`. Without one, invoke `security-threat-model` in
  focused mode for this entry point.
- `targets/<id>.md`: Candidate (entry point, attacker control, checks,
  setup, how to call), Scope files. Without one (standalone use): write
  the minimal one first
  ([references/target-file.md](references/target-file.md)).
- `CAMPAIGN.md`: Harnesses (workflow of each existing harness).
- The language skill (`fuzz-c-cpp`, `fuzz-java`, `fuzz-js`): harness format,
  project conventions, input helpers, how checks signal failure.

## Steps

1. **No unjustified duplication.** Compare the planned workflow (entry
   point, call sequence, options the input selects, checks) with each
   existing harness's workflow. This rule, not coverage, decides whether a
   new variant is kept:
   - If an existing harness's workflow contains the planned one, extend
     that harness instead, e.g. with an option that selects the new
     variant (step 3, "Adding an option").
   - If the planned workflow contains an existing harness's workflow,
     replace that harness. Its corpus becomes seeds if the input format
     matches.
   - A partial overlap, such as shared setup, is fine.

   A **specialist harness** for the same workflow is kept when it adds
   coverage or a recorded new dimension: configuration, state, call order,
   input format, or oracle. A slow or noisy property, for example, may get
   its own harness. A specialist for one configuration is the alternative
   to such an option; it starts from the other harness's corpus when the
   input format matches.

   Each target keeps a short configuration matrix in its file: which
   harness covers which configurations. After a plateau, try 2–3
   candidate variants.
2. **Format and location.** Work like the project, so its maintainers can
   adopt the harness:
   - Follow the engine-selection order: the project's existing harness
     format and engine, then the user's choice, then the language default
     (see the language skill).
   - Put the harness where the project keeps tests or fuzzers, named and
     licensed like its neighbours.
3. **Design the harness** by the rules below. Input feeding and side
   effects follow
   [references/input-and-side-effects.md](references/input-and-side-effects.md),
   and checks follow [references/properties.md](references/properties.md).
   The rules keep findings to bugs real callers and attackers can reach,
   and the harness fast and deterministic.
   - Fuzz only what the attacker controls; hold the rest fixed. Let the
     input select modes, options, algorithms or call sequences only when
     deployments use them or the attacker can choose them.
   - Enter at the real trust boundary, not at an internal helper.
   - Call the smallest part of the program that keeps the real code path:
     one function when its inputs are fully attacker-controlled; several
     components or the full initialization when the bug would need their
     state. Too small a part causes both missed bugs and false reports.
   - Build any required prior state (handshake, header, initialized
     context) inside the harness.
   - Honour documented API contracts (NUL termination, valid handles,
     required call order).
   - Use results as callers do: read back every field and element the
     target returns before freeing it, because lazy parsers decode on
     access.
   - A stateful API gets a loop of at most ~16 calls. The input picks each
     call, every call uses only handles the harness holds, and the harness
     frees what's left at the end (`fuzz-c-cpp`'s call-sequences.md shows
     it).
   - Callbacks the target calls (SAX handlers, read and error callbacks)
     return what the input picks among what real callers return: stop, a
     short read, an error.
   - Catch exceptions the API documents for invalid input, and nothing
     broader (C++: never `catch (...)`).
   - Add the checks from the target's candidate entry, including property
     checks.
   - Deterministic: no time, randomness or global state that leaks between
     inputs. Fix seeds and reset state.
   - Fast: put expensive one-time setup in the engine's initialization
     hook, log nothing per input, and limit the input size to what the
     format needs.
   - Input layout: `FuzzedDataProvider` (every language) takes single
     values, such as integers, booleans and enums, from the end of the
     input; the first call gets the last bytes. It takes bytes, arrays and
     strings from the front. Nothing can be read after
     `ConsumeRemainingBytes` or similar calls.

     So a harness that parses a document (a file in the target's format)
     and also takes options doesn't take them from the end: each seed
     would lose its last bytes to them, e.g. `[1,2]` would become the
     document `[1,`. It takes them from an options prefix instead
     (`fuzz-c-cpp`'s harness.md shows it; Java gives a parser's options a
     typed fuzz test instead, `fuzz-java`'s harness.md):
     - the prefix is a fixed number of bytes at the start of the input,
       e.g. 8: what the options read, plus spare bytes for options added
       later. Options read after its bytes run out get their lowest value
       (or `false`) every time, so the fuzzer never varies them;
     - the options come only from the prefix, through a
       `FuzzedDataProvider` over just those bytes or from the bytes
       themselves;
     - everything after the prefix is the document. The input cap counts
       the prefix too;
     - each seed is a format file with the prefix's bytes in front
       (seeds.md), whatever the options read.
   - Adding an option to a harness that parses a document, by the first
     case that applies:
     - if it's a project's harness with inputs saved outside the campaign
       (on OSS-Fuzz or ClusterFuzzLite, in CI corpus artifacts, or
       committed in the project, such as Jest's regression directory),
       don't add the option, not even from spare bytes: add a specialist
       for the new configuration instead (step 1). Those inputs can't all
       be converted, and their spare bytes hold arbitrary values, so the
       option would change what they do. OSS-Fuzz treats a testcase that
       stops reproducing as fixed, then discloses its bug;
     - in Java, the option goes into a typed fuzz test instead, as above;
     - if the whole input is the document (no options yet), add an
       options prefix and put its bytes in front of every input saved in
       the old layout: the corpus (Jest: also the regression directory).
       Update the harness's generators in `generators/` too. Otherwise
       the prefix takes each input's first bytes: `[1,2]` would leave an
       empty document;
     - if it has a prefix, read the new option after the existing ones,
       from spare bytes, so the corpus stays valid. If the prefix must
       grow, add the extra bytes where no old option reads: in front for
       a `FuzzedDataProvider` over the prefix, which reads from its end;
       right after the old prefix for a harness that reads the bytes
       directly;
     - if it takes its options from the end (a project's harness), the
       new option moves the document's end and reinterprets the corpus:
       `fuzz-coverage` replays it on the new build for a new baseline,
       and the rounds restart.
4. **Build** the needed variants as
   [references/build.md](references/build.md) says.
5. **Seeds and dictionary,** as
   [references/seeds.md](references/seeds.md) says, before the gate.
6. **Gate.** Run all checks in [references/gate.md](references/gate.md)
   (standalone: its light gate) before any other run. Only the gate shows that the target is instrumented and reached.
   If a check fails, fix the harness or the build and run the gate again.
   - If the gate finds a crash, the crash goes to `fuzz-triage` first
     (gate.md, "No crash in the gate").
7. **Record and commit.** Write the workflow into the target file's
   Harness section (`targets/<id>.md`) and into `CAMPAIGN.md`'s Harnesses
   table. Write the gate result into the target file's Gate table. Then
   commit the harness as `fuzz(harness): <target>`.

## Done when

- The gate passed (standalone: the light gate), with results recorded.
- The harness's workflow is recorded and contains no other harness's
  workflow, unless it's a recorded specialist (step 1).
- The harness follows the project's conventions and is committed.

## Pitfalls and shortcuts to reject

- "It compiles and ran for a minute without crashing, so it works." Only the
  gate shows that the target is instrumented and reached, and its
  state-leak check finds no sign of state leaking between inputs (a smoke
  check, not a proof).
- Fuzzing configuration or options that neither the attacker nor any
  deployment chooses. That reports threats that don't exist.
- Catching every exception to "stop noise". It hides real bugs; catch only
  what the API documents.
- Writing input to a file per execution when a buffer API exists. It costs
  speed and leaves files behind.
- Switching off a dangerous feature (external entities, includes) that is on
  by default in deployments. It hides the bug the campaign should find.
- A property assertion the spec doesn't guarantee. It produces harness
  bugs, not findings.
- Proposing a harness that reaches a held-back bug (`fuzz-campaign`,
  safety rules).

## Writes

What the steps in this file write or change. Paths are relative to
`.seclab/fuzz/` unless they start with `.seclab/`:
- `targets/<id>.md`: Harness (workflow, corpus and dictionary paths), Gate.
  The target file's layout: `fuzz-campaign`'s ledger.md,
  "`targets/<target>.md`".
- `CAMPAIGN.md`: Harnesses table.
- A `fuzz(harness): ...` commit on the campaign branch.

## Next

- **Before stopping,** check the Done when items that this request's steps produce: do the step behind a missing one, or record why it can't be done.
- In a campaign, or called by another skill for one step: return to it
  with the result.
- Standalone: stop when the request is done. Show the result, using the skills' terms, each explained in a few words the first time (e.g. "the gate (the harness checks)"), and offer the next step without starting it.
- The next step after a harness: `fuzz-coverage`'s inner loop.

