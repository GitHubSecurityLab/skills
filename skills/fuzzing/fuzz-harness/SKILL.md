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
repository root:

```sh
python3 <this skill's directory>/../fuzz-campaign/scripts/init.py
```

It sets up `.seclab/fuzz/`, copies or refreshes the scripts in
`.seclab/fuzz/bin/`, and prints the first-build notice and the safety rules,
which apply here too. Show the notice before any build or run of project
code, then rerun with `--notice-shown`. Exit 2 (no ledger yet): rerun with
`--branch current`, and before the first commit ask whether the work moves
to a new `seclab/fuzz-<date>` branch (`--branch new`); read-only requests
never ask. If `init.py` is missing, the skill was installed without its
plugin: ask the user to install the `seclab-skills` plugin; don't look for
another copy.

## Reads

- `targets/<id>.md`: Candidate (entry point, attacker control, checks,
  setup, how to call), Scope files.
- `CAMPAIGN.md`: Harnesses (workflow of each existing harness).
- Standalone use without a target file: write a minimal
  `targets/<id>.md` with the entry point, what the attacker controls there,
  and the scope files, each with a one-line reason: from the entry point,
  follow the calls and keep the files where attacker data is processed,
  stopping at code the threat model excludes. Put the bare paths into
  `targets/<id>.scope`, which the gate measures. Don't read the campaign
  references for it.
- `.seclab/threat-model.md`. Without one, invoke `security-threat-model` in
  focused mode for this entry point.
- The language skill (`fuzz-c-cpp`, `fuzz-java`, `fuzz-js`): harness format,
  project conventions, input helpers, how checks signal failure.

## Steps

1. **No unjustified duplication.** Compare the planned workflow (entry
   point, call sequence, options the input selects, checks) with each
   existing harness's workflow; this rule, not coverage, decides whether a
   new variant is kept:
   - contained in an existing harness → extend that harness instead (e.g. a
     selector byte for the new variant, where the format ignores it: after
     the document or in a fixed prefix, never in bytes the format needs,
     such as a closing `]` or a ZIP trailer);
   - contains an existing harness → replace it; its corpus becomes seeds if
     the input format matches;
   - partial overlap (shared setup) is fine.
   A **specialist harness** for the same workflow is kept when it adds
   coverage or a recorded new dimension: configuration, state, call order,
   input format, or oracle (a slow or noisy property may get its own
   harness). A specialist for one configuration is the alternative to a
   selector byte; it starts from the other harness's corpus when the input
   format matches. Each target keeps a short configuration matrix in its
   file (which harness covers which configurations); after a plateau, try
   2–3 candidate variants.
2. **Format and location.** Work like the project, so its maintainers can
   adopt the harness. Follow the engine-selection order: the project's
   existing harness format and engine, then the user's choice, then the
   language default (see the language skill). Put the harness where the
   project keeps tests or fuzzers, named and licensed like its neighbours.
3. **Design the harness** by the rules below, with input feeding and side
   effects from
   [references/input-and-side-effects.md](references/input-and-side-effects.md)
   and checks from [references/properties.md](references/properties.md).
   The rules keep findings to bugs real callers and attackers can reach,
   and the harness fast and deterministic.
   - Fuzz only what the attacker controls; hold the rest fixed. Let the input
     select modes, options, algorithms or call sequences only when
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
     target returns before freeing it (lazy parsers decode on access). A
     stateful API gets a loop of at most ~16 calls, each picked by the
     input, only on handles the harness holds, freeing what's left at the
     end (`fuzz-c-cpp`'s call-sequences.md shows it).
   - Callbacks the target calls (SAX handlers, read and error callbacks)
     return what the input picks among what real callers return: stop, a
     short read, an error.
   - Catch exceptions the API documents for invalid input, and nothing
     broader (C++: never `catch (...)`).
   - Add the checks from the target's candidate entry, including property
     checks.
   - Deterministic: no time, randomness or global state that leaks between
     inputs (fix seeds, reset state).
   - Fast: expensive one-time setup goes in the engine's initialization hook;
     no logging per input; limit input size to what the format needs.
   - Input layout: nothing can be read after `ConsumeRemainingBytes` or
     similar calls, so new options go before the final consume-remaining
     call. A layout change reinterprets old seeds (FuzzedDataProvider
     reads integers from the end of the input). Where the document marks
     its own end, prefer an option read after it, with the old behaviour
     when absent: it keeps old seeds. Otherwise that's fine:
     `fuzz-coverage` replays the corpus on the new build for a new
     baseline, and the rounds restart.
4. **Build** the needed variants as
   [references/build.md](references/build.md) says.
5. **Seeds and dictionary,** as
   [references/seeds.md](references/seeds.md) says, before the gate.
6. **Gate.** Run all checks in [references/gate.md](references/gate.md)
   (standalone: its light gate) before any other run: only the gate shows
   that the target is instrumented and reached. A failing check means
   fixing the harness (or build) and running the gate again.
   - If the gate finds an instant crash: it goes to `fuzz-triage` first
     (gate.md, "No instant crash").
7. **Record and commit.** Write the workflow into `targets/<id>.md`
   (Harness) and the Harnesses table, the gate result into Gate, and commit
   the harness as `fuzz(harness): <target>`.

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

- `targets/<id>.md`: Harness (workflow, corpus and dictionary paths), Gate.
- `CAMPAIGN.md`: Harnesses table.
- A `fuzz(harness): ...` commit on the campaign branch.

## Next

- **Before stopping,** check the Done when items that this request's steps produce: do the step behind a missing one, or record why it can't be done.
- In a campaign, or called by another skill for one step: return to it
  with the result.
- Standalone: stop when the request is done. Show the result, using
  the skills' terms, each explained in a few words the first time (e.g.
  "the gate (the harness checks)"), and offer the next step,
  `fuzz-coverage`'s inner loop, without starting it.

