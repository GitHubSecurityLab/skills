# Jest fuzz tests

Read only when the project has Jazzer.js Jest fuzz tests or the user asks
for them. The rest of this skill describes CLI targets; this file gives
the Jest way for each step. `L`, `F`, `H`, `P` and `RUN_PATH` are as in
`run.md` next to this file.

Jest fuzz tests run in two modes. Regression mode, the default, replays
saved inputs as normal Jest tests. Fuzzing mode (`JAZZER_FUZZ=1`) fuzzes
one test per process.

The probe (Setup, below), not the Jest version, decides whether the
runner works with the project's Jest. `@jazzer.js/jest-runner` 4.0.0 ran
on Jest 29.7 and 30.3, and fails on 30.4 and later.

## Setup

1. **Install the runner** unless the project has it, at the same version
   as `@jazzer.js/core` (`build.md`):
   <!-- ci-skip: the js toy's package.json pins the same version -->
   ```sh
   npm install --save-dev --save-exact @jazzer.js/jest-runner@4.0.0
   ```
2. **Probe:** a minimal `it.fuzz` test with a config of its own, in
   scratch. It runs in regression mode and in fuzzing mode, through the
   launcher like every Jest run. The probe touches no project file, and
   removes its directory:

   <!-- ci: jest-probe -->
   ```sh
   D="$F/jest-probe"; mkdir -p "$D"
   echo '{"private": true}' > "$D/package.json"
   echo 'describe("probe", () => { it.fuzz("runs", (data) => {}); });' > "$D/probe.fuzz.js"
   cat > "$D/jest.config.js" <<EOF
   module.exports = { rootDir: __dirname, testMatch: ["<rootDir>/probe.fuzz.js"],
     testRunner: require.resolve("@jazzer.js/jest-runner", { paths: ["$P"] }) };
   EOF
   $L start --id jest-probe-<n> --harness jest-probe --timeout 120 --env PATH="$RUN_PATH" -- sh -c '
       "$1" --config "$2/jest.config.js" && JAZZER_FUZZ=1 JAZZER_FUZZER_OPTIONS="[\"-runs=1000\"]" "$1" --config "$2/jest.config.js"
       rc=$?; rm -r "$2"; exit $rc' sh "$P/node_modules/.bin/jest" "$D"
   ```

   The state `finished` means the runner works with the project's Jest.
   On any other state, read the log, and undo the install
   (`npm uninstall @jazzer.js/jest-runner`, or the package manager's
   equivalent). Use CLI targets calling the same code, and tell the user
   why. Existing fuzz tests stay as they are.
3. **Config:** new fuzz tests get a config file of their own. Never edit
   the project's Jest config: with the runner in it, a Jest version that
   the runner doesn't support fails the project's own `jest` run too.

   <!-- ci: jest-fuzz-config -->
   ```javascript
   // jest.fuzz.config.js, next to the package's package.json
   module.exports = {
     displayName: "Jazzer.js",
     testMatch: ["<rootDir>/<tests_dir>/**/*.fuzz.js"],
     testRunner: "@jazzer.js/jest-runner",
   };
   ```

   - `<jest_config>` in the commands below is the config above, or the
     config that already runs the project's fuzz tests (`package.json`
     if that config is its `jest` key).
   - Copy only the keys the code under test needs from the project's
     config (`transform`, `moduleNameMapper`, `setupFiles`, ...).
   - `<tests_dir>` keeps `testMatch` out of `.seclab/`: Jest searches dot
     directories too.
   - The key is `testRunner`, not `runner` (changed in Jazzer.js 2).
   - **TypeScript** (ran with `ts-jest` 29): add `ts-jest`, and set
     `preset: "ts-jest"` and `testEnvironment: "node"`. Use `*.fuzz.[jt]s`
     in `testMatch`. Import `"@jazzer.js/jest-runner"` once for the
     `it.fuzz` types, e.g. in a `globals.d.ts`. Enable
     `"sourceMap": true` for accurate coverage.
   - Add npm scripts in the project's style:
     `"test:fuzz": "jest --config jest.fuzz.config.js"` (regression mode,
     for CI) and `"fuzz": "JAZZER_FUZZ=1 jest --config jest.fuzz.config.js"`.
4. **The project's own `jest` run leaves the fuzz tests out**, since it
   would fail on `it.fuzz`. Check that Jest lists no fuzz test with the
   project's config:

   <!-- ci: jest-own-run -->
   ```sh
   $L start --id jest-list-<n> --harness $H --timeout 120 --env PATH="$RUN_PATH" -- \
     sh -c 'cd "$1" && "$1/node_modules/.bin/jest" --listTests' sh "$P"
   grep -c '\.fuzz\.[jt]s$' "$F/runs/jest-list-<n>/copy-0/log" || :
   ```

   `0` means none. Otherwise, name or place the fuzz tests so that the
   project's `testMatch` doesn't reach them.
5. Commit the runner, the config and the scripts as `fuzz(build): ...`.

**`.jazzerjsrc.json`:** project-wide settings, read only by the Jest
runner. The runner finds the file by searching upward from the working
directory. Keys: `includes`, `excludes`, `customHooks`,
`disableBugDetectors`, `fuzzerOptions`, `sync`, `timeout` (ms, default
5000), `mode`, `dryRun`. `dryRun` set to `true` turns fuzzing feedback
off. In that case, pass `--env JAZZER_DRY_RUN=false` to fuzzing runs; the
variable wins over the file. Coverage settings go to Jest, and
`dictionaryEntries` to the test.

Each key also works as an environment variable with a JSON value
(`JAZZER_FUZZER_OPTIONS`, `JAZZER_CUSTOM_HOOKS`, ...). The campaign passes
per-run settings this way, because from scratch the upward search doesn't
find a sub-package's file. Full list: `docs/fuzz-settings.md`.

**Check:** `jest <test file>` passes in regression mode. Run it through
the launcher like the replay in "Coverage" below, without the name
pattern and the coverage options. Its output lists the fuzz test with an
`<empty>` case. If the check fails, see "Common failures" at the end of
this file.

## Writing a fuzz test

```javascript
// test/header-parser.fuzz.js
const { FuzzedDataProvider } = require("@jazzer.js/core");
const { parseHeader, HeaderError } = require("../src/header");

describe("parseHeader", () => {
  it.fuzz("parses untrusted headers", (data) => {
    const options = new FuzzedDataProvider(data.subarray(0, 8)); // the options prefix
    const strict = options.consumeBoolean();
    const header = data.subarray(8).toString("utf8");
    try {
      parseHeader(header, { strict });
    } catch (e) {
      if (!(e instanceof HeaderError)) throw e; // only the documented error
    }
  });
});
```

- Name the file `<thing>.fuzz.js` (or `.fuzz.ts`) in `<tests_dir>`,
  matching the fuzz config's `testMatch`.
- `data` is a `Buffer`. Async targets: `async (data) => {...}` or
  `(data, done) => {...}`.
- Options go in the third argument: a number (timeout in ms) or
  `{ timeout, sync, fuzzerOptions, dictionaryEntries }`. Use `sync: true`
  for synchronous targets (`run.md`).
- One-time setup: module scope or `beforeAll`.
- Property checks: `expect(...)` or `throw new Error(...)`.
- Seeds go into the generated corpus (next section). The regression
  directory, which every normal test run replays, gets only the seeds
  worth keeping, at upstreaming (below).
- Dictionaries: `dictionaryEntries: ["<!DOCTYPE", "xmlns"]` in the test's
  options, or `-dict=<file>` in the fuzzer options.
- `jest.mock("fs")` with an in-memory implementation is an alternative for
  file system access, but the path-traversal detector doesn't see the
  mocked calls.
- `JAZZER_VERBOSE=true` prints `Instrumentation error` lines for Jest's
  own Babel dependencies. Those lines are noise.

## Engine output locations in the project tree

`<corpus_dir>` is the test file's name without its extension, then the
describe title and the test title, with spaces turned into `_`. For the
example above, it's
`header-parser.fuzz/parseHeader/parses_untrusted_headers`.

For the gate's working-tree comparison:
- `.cifuzz-corpus/<corpus_dir>/` in the package root, the nearest
  directory above the test file with a `package.json`. This is the
  **generated corpus**: the harness's persistent corpus, where fuzzing
  mode writes new inputs. `init.py` excludes it.
- `<corpus_dir>/` next to the test file: the **regression directory**.
  In the commands, `<regression_dir>` is its path from `$P`. Fuzzing mode
  also writes crash files there, unless the command passes
  `-exact_artifact_path` (Runs).
- `coverage/`, if coverage runs without `--coverageDirectory`.

Neither input directory can be moved. Fuzzing mode reads both, and its
log's `INFO: using inputs from:` lines show them. Regression mode, a
normal `jest` run, runs the empty input and each file of the regression
directory as its own test, named after the file. With `--coverage`, it
runs the generated corpus's files too.

## Runs

Call `$P/node_modules/.bin/jest`. It runs from scratch, with
`--config "$P/<jest_config>" --rootDir "$P"`.

| Setting | Option |
|---|---|
| `memory_mb` | `-rss_limit_mb=<m>` in `JAZZER_FUZZER_OPTIONS` |
| `input_timeout_s` | `JAZZER_TIMEOUT=<s × 1000>` (milliseconds) |
| run time (the launcher's `--time`) | `-max_total_time=<s>` in `JAZZER_FUZZER_OPTIONS` |

**Develop runs** (gate, rounds) run in one process:

<!-- ci: jest-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$P/.cifuzz-corpus/<corpus_dir>" \
  --env PATH="$RUN_PATH" --env JAZZER_FUZZ=1 --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env JAZZER_FUZZER_OPTIONS="[\"-max_total_time=<run_s>\",\"-rss_limit_mb=<memory_mb>\",\"-print_final_stats=1\",\"-dict=$F/dict/$H.dict\",\"-exact_artifact_path={run}/artifacts/copy-{copy}/finding\"]" -- \
  "$P/node_modules/.bin/jest" --config "$P/<jest_config>" --rootDir "$P" "$P/<test_file>" \
  --testNamePattern '^<test_title>$'
```

`<corpus_dir>` is as above, and `<test_title>` is `<describe> <test name>`
with regex characters escaped (e.g. in `parse()`).

- **Crash files:** `-exact_artifact_path` keeps them out of the regression
  directory. There, every later run would stop at them, and they could get
  committed by accident. `-artifact_prefix` can't keep them out, since the
  runner overrides it. A timeout lands at `finding-timeout`.
- **The name pattern** is anchored, with regex characters in the titles
  escaped, so `^<describe> <test name>$` selects exactly one fuzz test,
  in fuzzing and regression mode alike.
- **One fuzz test per process;** the others are skipped with
  `FuzzerStartError`. The run stops at the first finding.
- Status lines are libFuzzer's: `#<n> NEW cov: <n> ft: <n> ... exec/s:
  <n>`. Jest exits 1 both for a finding and for a configuration error, so
  read the log.
- Extra seed directories go as absolute paths into
  `JAZZER_FUZZER_OPTIONS`, and are only read. Examples: `$F/corpus/$H/`,
  or the checkout's `.cifuzz-corpus/...` for a worktree run.

**Long runs:** N copies of the same command (`--copies`) share the
package's `.cifuzz-corpus` directory. Since that directory is fixed,
run only one Jest long run per harness per tree. Each copy picks up the
others' inputs (`RELOAD` lines), and stops at its first finding. Add the
checkout's `.cifuzz-corpus/...` as an extra seed directory.

Before the worktree is removed (`fuzz-campaign`'s long-runs.md), copy the
new files of its `.cifuzz-corpus` into the checkout's. libFuzzer names the
files by their content, so overwriting is harmless. `cp -n`, by contrast,
exits 1 on macOS when it skips a file:

<!-- ci-skip: no scenario runs a Jest long run in a worktree -->
```sh
cp -R "<worktree>/<package>/.cifuzz-corpus/." "<checkout>/<package>/.cifuzz-corpus/"
```

**No `-fork`, `-jobs` or `-merge`:** fork jobs "crash" with zero
executions, and the run exits 0. A merge adds nothing, and none is
needed: new inputs already land in the generated corpus.

To minimize the corpus on request, and only if the fuzz test needs no
Jest mocks:
1. Write a temporary CLI target in scratch that calls the same code as
   the fuzz test.
2. Merge with it into a new directory (`run.md`).
3. Replace the test's generated corpus with that directory.

## Coverage

Replay in regression mode, without `JAZZER_FUZZ`, with run id
`cov-<h>-<n>`. With `--coverage`, the replay runs every input of the
generated corpus and of the regression directory once:

<!-- ci: jest-cov-replay -->
```sh
$L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env NODE_OPTIONS=--max-old-space-size=<memory_mb> -- \
  "$P/node_modules/.bin/jest" --config "$P/<jest_config>" --rootDir "$P" "$P/<test_file>" \
  --testNamePattern '^<test_title>$' --coverage --coverageReporters=lcov --coverageDirectory="{run}/raw"
```

- An input that throws fails only its own test, and Jest exits 1. The
  report is still written. Hand failing inputs to `fuzz-triage`.
- The report includes every file the test loaded, the test file too.
  `coverage.py --scope` filters it to the scope files.
- Export as for CLI targets (`coverage.md`, "Export").
- **A prototype pollution stays:** every later input fails with the same
  report, and runs on the polluted prototype. Replay without the first
  failing input before trusting the report.
- **A synchronous hang** blocks the event loop, so `JAZZER_TIMEOUT` can't
  fire. The launcher kills the replay (`timed out`), and the report is
  lost. Find the hanging input by halving the subset (Exact subset,
  below); the hang is a blocking timeout bug. Exclude the input with the
  name pattern, replay, and mark the snapshot partial.
- TypeScript through `ts-jest`: coverage names the `.ts` files. Their
  line numbers match the source only with `"sourceMap": true` in
  `tsconfig.json` (not yet run in a campaign).

**Exact subset** (`reach.py`, the gate's state-leak check): use the replay
command above with the pattern `^<describe> <test name> ({names})(?!.)`.
`{names}` are the escaped input file names, joined with `|`. A pattern
ending in `$` runs no input, and an unanchored one also runs names that
contain the chosen ones.

With `reach.py`, pass both of Jest's directories as `--corpus`. Also pass
`--ok-exit 0,1`, since Jest exits 1 with a complete report when an input
throws:

<!-- ci: jest-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$P/.cifuzz-corpus/<corpus_dir>" --corpus "$P/<regression_dir>" \
  --file <file> --line <line> --env PATH="$RUN_PATH" --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env NODE_OPTIONS=--max-old-space-size=<memory_mb> --ok-exit 0,1 --timeout <timeout_s> \
  --replay "$P/node_modules/.bin/jest --config $P/<jest_config> --rootDir $P $P/<test_file> --testNamePattern '^<test_title> ({names})(?!.)' --coverage --coverageReporters=lcov --coverageDirectory={out}" \
  --export "python3 $F/bin/coverage.py merge {out}/lcov.info --sf-base {scratch} --root $F/../.. -o {lcov}"
```

**The gate's state-leak check** (`coverage.md`, "The gate's state-leak check"):
run the exact-subset pattern once with all the sample's names, and once
for each name.

## Triage

For a long run's finding, set `P` to `<tree_package>`
(`run.md`, "CLI target").

**Reproduce:** regression mode never reads `{run}/artifacts/`, so
reproduce through a copy:
1. Copy the finding into the regression directory, under a name of its
   own.
2. Run Jest in regression mode, without `JAZZER_FUZZ`, for just that
   file:

   <!-- ci: jest-repro -->
   ```sh
   $L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAZZER_TIMEOUT=<input_timeout_ms> \
     --env NODE_OPTIONS=--max-old-space-size=<memory_mb> -- \
     "$P/node_modules/.bin/jest" --config "$P/<jest_config>" --rootDir "$P" "$P/<test_file>" \
     --testNamePattern '^<test_title> (<input_name>)(?!.)'
   ```
3. Remove the copy again right after.

- Each file runs as its own test, named after the file. The state
  `exited 1`, with that test failed, means the finding reproduced.
- Escape regex characters in the whole pattern; otherwise titles like
  `parse()` match nothing. A pattern ending in `$` runs nothing.
- Jest adds its own failure block with a code frame; the `==<pid>==` block
  comes first.
- **Frame names can be off:** source maps name a frame after the
  identifier at its location (`at length (src/parser.js:5:52)` for
  `o.length` inside `parse`). Trust `file:line`, not the name.
- If the input is already in the generated corpus, it replays with
  `--coverage` added.
- **A synchronous hang** ignores `JAZZER_TIMEOUT` in regression mode, so
  the state `timed out` counts as a reproduced hang. No log has the
  hang's JS stack. Instead, `crash_buckets.py` reads its kind from the
  fuzzing run's log, and a temporary CLI target, like the one for
  Minimize below, names its loop (`triage.md`, "Timeouts").
- **Minimize** through a temporary CLI target in scratch that calls the
  same code (`triage.md`).

## Regression tests and upstreaming

- Once upstreamed, the project's CI runs the fuzz tests in regression mode
  (`npm run test:fuzz`), next to its normal test job. That run replays
  each test's regression directory (above), which checks the harness on
  its seeds. A fixed bug gets its own test instead (`regression.md`).
- `.cifuzz-corpus/` isn't committed. Seeds worth keeping go into the
  regression directories: only the project's own data or generated
  inputs, and never inputs that reach a held-back bug.
- **Never leave a crash file in a regression directory:** the project's CI
  replays it, fails until the fix lands, and reveals a held-back bug. The
  reproducer is the regression test (`regression.md`).

## Common failures

| Symptom | Cause and fix |
|---|---|
| `it.fuzz is not a function` | The test ran with the project's own config; pass `--config <jest_config>`, and check `testMatch` and `testRunner` |
| ESM imports fail under Jest | Jest transforms ESM to CommonJS; follow `docs/fuzz-targets.md` for ESM |
