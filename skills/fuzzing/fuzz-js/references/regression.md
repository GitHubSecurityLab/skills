# Jazzer.js regression tests

## Seed replay: the harness as a test

**CLI targets:** a replay test in the project's own runner calls the
exported `fuzz` function on every seed, so the project's normal test job
checks that the harness still runs. With `node:test` (Node 20+, no
dependency):

```js
// fuzz/<thing>.replay.test.js
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { fuzz } = require("./<thing>.fuzz.js");

const dir = path.join(__dirname, "corpus", "<thing>");
for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
  test(`<thing> seed ${name}`, async () => fuzz(fs.readFileSync(path.join(dir, name))));
}
```

It runs without Jazzer.js, so bug detectors don't apply: a seed passes
unless the call throws.
- If the project tests with Jest, Vitest or Mocha: the same loop works
  with their `test`/`it`.
- If the project is ESM: `import` the target.

## The bug's reproducer (`fuzz-triage`)

Every real bug gets one, written in triage on the build that found it: a
Node script in `crashes/<bucket>/repro.js` that calls the project's API
with the concrete values the harness derived from the input, not through
the harness and not as a crash file. It loads the package from
`process.env.PKG`, so it runs against any tree (the checkout, a worktree,
`base`):

```js
const assert = require("node:assert");
const { parseRecord } = require(process.env.PKG + "/src/record");
// Regression: parseRecord() trusts the field index from the input.
assert.strictEqual(parseRecord(Buffer.from("REC1\x05a,b", "latin1")), null);
```

- **It includes the check that caught the bug,** so it fails before the
  fix: the exception itself, the harness's property check written out, or
  an explicit assertion of a bug detector's effect (e.g. that
  `Object.prototype` is unchanged after the call). A plain call often
  passes.
- Run it through the launcher; the state `exited 1` with the bug's error
  means it reproduced, `finished` means it passes:

<!-- ci: js-reproducer -->
```sh
$L start --id poc-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env PKG="$P" -- \
  node --max-old-space-size=<memory_mb> "$F/crashes/<bucket>/repro.js"
```

The fix (`fuzz-triage`) turns it into a test of the project's framework (Jest, Mocha,
Vitest, `node:test`) in the fix commit, with the bug named in a comment.

## Hand-off (`fuzz-campaign`)

- CLI targets: the replay test above joins the project's normal test job,
  with the seeds in `fuzz/corpus/<thing>/`; add the npm script that fuzzes
  them.
- Hooks used only for the campaign stay out of the hand-off unless the
  fuzz test needs them.

## Keep it private until disclosure

A held-back bug's test (`fuzz-campaign`, "Safety rules") stays in the
`fuzz(fix)` commit, which is never pushed, until the bug is public and
fixed upstream: it reveals the bug as soon as it's pushed. Never put a
crash file among the seeds the replay test reads: the project's CI
replays it, failing until the fix lands and revealing a held-back bug.
The reproducer is the regression test.
