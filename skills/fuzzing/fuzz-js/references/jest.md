# Jest fuzz tests

Read only when the project has Jazzer.js Jest fuzz tests or the user asks
for them. The rest of this skill describes CLI targets; this file gives
the Jest way for each step. `L`, `F`, `H`, `P` and `RUN_PATH` are as in
`run.md` next to this file.

Regression mode (the default) replays saved inputs as normal Jest tests;
fuzzing mode (`JAZZER_FUZZ=1`) fuzzes one test per process. Whether the
runner works with the project's Jest is the probe's call, not the version's:
`@jazzer.js/jest-runner` 4.0.0 ran on Jest 29.7 and 30.3, and fails on
30.4 and later.

## Setup

1. **Install the runner** (unless the project has it), at the version of
   `@jazzer.js/core` (`build.md`):
   <!-- ci-skip: the js toy's package.json pins the same version -->
   ```sh
   npm install --save-dev --save-exact @jazzer.js/jest-runner@4.0.0
   ```
2. **Probe:** a minimal `it.fuzz` test with a config of its own, in
   scratch, run in regression and in fuzzing mode through the launcher like
   every Jest run. It touches no project file and removes its directory:

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
   Anything else: read the log, undo the install (`npm uninstall
   @jazzer.js/jest-runner`, or the package manager's equivalent), and use
   CLI targets calling the same code. Tell the user why; existing fuzz
   tests stay as they are.
3. **Config:** new fuzz tests get a config file of their own. Never edit
   the project's Jest config: with the runner in it, a Jest the runner
   doesn't support fails the project's own `jest` run too.

   <!-- ci: jest-fuzz-config -->
   ```javascript
   // jest.fuzz.config.js, next to the package's package.json
   module.exports = {
     displayName: "Jazzer.js",
     testMatch: ["<rootDir>/<tests_dir>/**/*.fuzz.js"],
     testRunner: "@jazzer.js/jest-runner",
   };
   ```

   - `<jest_config>` in the commands below is this file, or the config
     that already runs the project's fuzz tests.
   - Copy only the keys the code under test needs from the project's
     config (`transform`, `moduleNameMapper`, `setupFiles`, ...).
   - `<tests_dir>` keeps `testMatch` out of `.seclab/`: Jest searches dot
     directories too.
   - The key is `testRunner`, not `runner` (changed in Jazzer.js 2).
   - **TypeScript** (ran with `ts-jest` 29): add `ts-jest`, and set
     `preset: "ts-jest"`, `testEnvironment: "node"` and `*.fuzz.[jt]s` in
     `testMatch`. Import `"@jazzer.js/jest-runner"` once (e.g. in a
     `globals.d.ts`) for the `it.fuzz` types, and enable `"sourceMap":
     true` for accurate coverage.
   - npm scripts in the project's style:
     `"test:fuzz": "jest --config jest.fuzz.config.js"` (regression mode,
     for CI) and `"fuzz": "JAZZER_FUZZ=1 jest --config jest.fuzz.config.js"`.
4. **The project's own `jest` run leaves the fuzz tests out** (it would
   fail on `it.fuzz`). With the project's config, Jest lists no fuzz test:

   <!-- ci: jest-own-run -->
   ```sh
   $L start --id jest-list-<n> --harness $H --timeout 120 --env PATH="$RUN_PATH" -- \
     sh -c 'cd "$1" && "$1/node_modules/.bin/jest" --listTests' sh "$P"
   grep -c '\.fuzz\.[jt]s$' "$F/runs/jest-list-<n>/copy-0/log" || :
   ```

   `0` means none. Otherwise, name or place the fuzz tests where the
   project's `testMatch` doesn't reach.
5. Commit the runner, the config and the scripts as `fuzz(build): ...`.

**`.jazzerjsrc.json`:** project-wide settings, read only by the Jest
runner, found by searching upward from the working directory:
`includes`, `excludes`, `customHooks`, `disableBugDetectors`,
`fuzzerOptions`, `sync`, `timeout` (ms, default 5000), `mode`, `dryRun`
(`true` turns fuzzing feedback off: pass `--env JAZZER_DRY_RUN=false` to
fuzzing runs then, which wins over the file). Coverage settings go to
Jest, `dictionaryEntries` to the test. Each key also works
as an environment variable with a JSON value (`JAZZER_FUZZER_OPTIONS`,
`JAZZER_CUSTOM_HOOKS`, ...), which is how the campaign passes per-run
settings: from scratch, a sub-package's file isn't found. Full list:
`docs/fuzz-settings.md`.

**Check:** regression mode passes (`jest <test file>` through the
launcher, below); the output lists the fuzz test with an `<empty>` case.
If it fails: "Common failures" at the end of this file.

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
- Options as the third argument: a number (timeout in ms) or
  `{ timeout, sync, fuzzerOptions, dictionaryEntries }`. `sync: true` for
  synchronous targets (`run.md`).
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
- `JAZZER_VERBOSE=true` prints `Instrumentation error` lines for Jest's own
  Babel dependencies: noise.

## Engine output locations in the project tree

For the gate's working-tree comparison:
- `.cifuzz-corpus/<corpus_dir>/` in the package root (the nearest
  directory above the test file with a `package.json`): the **generated
  corpus**, the harness's persistent corpus, where fuzzing mode writes new
  inputs (excluded by `init.py`);
- `<corpus_dir>/` next to the test file: the **regression directory**
  (`<regression_dir>` in the commands: its path from `$P`), where fuzzing
  mode also writes crash files unless the command passes
  `-exact_artifact_path` (Runs);
- `coverage/` if coverage runs without `--coverageDirectory`.

`<corpus_dir>` is the test file's name without its extension, then the
describe and test titles with spaces turned into `_`: for the example
above, `header-parser.fuzz/parseHeader/parses_untrusted_headers`.
Neither input directory can be moved. Fuzzing mode reads both, and its
log's `INFO: using inputs from:` lines show them. Regression mode (a normal
`jest` run) runs the empty input and each file of the regression
directory as its own test, named after the file; with `--coverage`, the
generated corpus's files too.

## Runs

Call `$P/node_modules/.bin/jest`; it runs from scratch with
`--config <pkg>/<jest config or package.json> --rootDir <pkg>`.

| Setting | Option |
|---|---|
| `memory_mb` | `-rss_limit_mb=<m>` in `JAZZER_FUZZER_OPTIONS` |
| `input_timeout_s` | `JAZZER_TIMEOUT=<s × 1000>` (milliseconds) |
| run time (the launcher's `--time`) | `-max_total_time=<s>` in `JAZZER_FUZZER_OPTIONS` |

**Develop runs** (gate, rounds): one process.

<!-- ci: jest-develop -->
```sh
$L start --id r<round>-$H --harness $H --time <run_s> --corpus "$P/.cifuzz-corpus/<corpus_dir>" \
  --env PATH="$RUN_PATH" --env JAZZER_FUZZ=1 --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env JAZZER_FUZZER_OPTIONS="[\"-max_total_time=<run_s>\",\"-rss_limit_mb=<memory_mb>\",\"-print_final_stats=1\",\"-dict=$F/dict/$H.dict\",\"-exact_artifact_path={run}/artifacts/copy-{copy}/finding\"]" -- \
  "$P/node_modules/.bin/jest" --config "$P/<jest_config>" --rootDir "$P" "$P/<test_file>" \
  --testNamePattern '^<test_title>$'
```

`<corpus_dir>` is as above, and `<test_title>` is `<describe> <test name>`
with regex characters escaped.

- **Crash files:** `-exact_artifact_path` keeps them out of the regression
  directory, where every later run would stop at them and they could get
  committed by accident; `-artifact_prefix` can't, since the runner
  overrides it. A timeout lands at `finding-timeout`.
- **The name pattern** is anchored and escaped (regex characters in the
  titles, e.g. `parse()`): `^<describe> <test name>$` selects exactly one
  fuzz test, in fuzzing and regression mode alike.
- **One fuzz test per process;** the others are skipped with
  `FuzzerStartError`. The run stops at the first finding.
- Status lines are libFuzzer's (`#<n> NEW cov: <n> ft: <n> ... exec/s:
  <n>`). Jest exits 1 both for a finding and for a configuration error:
  read the log.
- Extra seed directories (e.g. `$F/corpus/$H/`, or the checkout's
  `.cifuzz-corpus/...` for a worktree run) go as absolute paths into
  `JAZZER_FUZZER_OPTIONS`; they're only read.

**Long runs:** N copies of the same command (`--copies`), sharing the
package's `.cifuzz-corpus` directory; each picks up the others' inputs
(`RELOAD` lines) and stops at its first finding. Add the
checkout's `.cifuzz-corpus/...` as an extra seed directory. Only one Jest
long run per harness per tree, since the directory is fixed. Before the
worktree is removed (`fuzz-campaign`'s long-runs.md), copy its new files
into the checkout's. libFuzzer names the files by their content, so
overwriting is harmless, while `cp -n` exits 1 on macOS when it skips a file:

<!-- ci-skip: no scenario runs a Jest long run in a worktree -->
```sh
cp -R "<worktree>/<package>/.cifuzz-corpus/." "<checkout>/<package>/.cifuzz-corpus/"
```

**No `-fork`, `-jobs` or `-merge`:** fork jobs "crash" with zero
executions and the run exits 0; a merge adds nothing. No merge is needed:
new inputs already land in the generated corpus. To minimize on request,
write a temporary CLI target in scratch that calls the same code as the
fuzz test (only if it needs no Jest mocks), merge with it into a new
directory (`run.md`), and replace the test's generated corpus with it.

## Coverage

Replay in regression mode (no `JAZZER_FUZZ`), run id `cov-<h>-<n>`. With
`--coverage`, it runs every input of the generated corpus and the
regression directory once:

<!-- ci: jest-cov-replay -->
```sh
$L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env NODE_OPTIONS=--max-old-space-size=<memory_mb> -- \
  "$P/node_modules/.bin/jest" --config "$P/<jest_config>" --rootDir "$P" "$P/<test_file>" \
  --testNamePattern '^<test_title>$' --coverage --coverageReporters=lcov --coverageDirectory="{run}/raw"
```

- An input that throws only fails its own test (Jest exits 1); the report
  is still written. Hand failing inputs to `fuzz-triage`.
- The report includes every file the test loaded, including the test file;
  `coverage.py --scope` filters to the scope files.
- Export as for CLI targets (`coverage.md`, "Export").
- **A prototype pollution stays:** every later input fails with the same
  report and runs on the polluted prototype. Replay without the first
  failing input before trusting the report.
- **A synchronous hang** blocks the event loop, so `JAZZER_TIMEOUT` can't
  fire: the launcher kills the replay (`timed out`) and the report is lost.
  Find the hanging input by halving the subset (exact subset below), a
  blocking timeout bug; exclude it with the pattern, replay, and mark the
  snapshot partial.
- TypeScript through `ts-jest`: coverage names the `.ts` files; their
  line numbers match the source only with `"sourceMap": true` in
  `tsconfig.json` (not yet run in a campaign).

**Exact subset** (`reach.py`, the gate's state-leak check): the same command
with the pattern `^<describe> <test name> ({names})(?!.)`, where `{names}`
are the escaped input file names joined with `|`. A pattern ending in `$`
runs no input, and an unanchored one also runs names containing the chosen
ones. With `reach.py`, pass both of Jest's directories as `--corpus`, and
`--ok-exit 0,1` (Jest exits 1 with a complete report when an input throws):

<!-- ci: jest-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$P/.cifuzz-corpus/<corpus_dir>" --corpus "$P/<regression_dir>" \
  --file <file> --line <line> --env PATH="$RUN_PATH" --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env NODE_OPTIONS=--max-old-space-size=<memory_mb> --ok-exit 0,1 --timeout <timeout_s> \
  --replay "$P/node_modules/.bin/jest --config $P/<jest_config> --rootDir $P $P/<test_file> --testNamePattern '^<test_title> ({names})(?!.)' --coverage --coverageReporters=lcov --coverageDirectory={out}" \
  --export "python3 $F/bin/coverage.py merge {out}/lcov.info --sf-base {scratch} --root $F/../.. -o {lcov}"
```

**The gate's state-leak check** (`coverage.md`, "The gate's state-leak check"):
run the exact-subset pattern once with all the sample's names and once per
name.

## Triage

A long run's finding: `P` set to `<tree_package>` (`run.md`, "CLI target").

**Reproduce:** regression mode never reads `{run}/artifacts/`, so copy
the finding into the regression directory under a name of its own, run
Jest in regression mode (without `JAZZER_FUZZ`) for just that file, and
remove the copy again right after:

<!-- ci: jest-repro -->
```sh
$L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" --env JAZZER_TIMEOUT=<input_timeout_ms> \
  --env NODE_OPTIONS=--max-old-space-size=<memory_mb> -- \
  "$P/node_modules/.bin/jest" --config "$P/<jest_config>" --rootDir "$P" "$P/<test_file>" \
  --testNamePattern '^<test_title> (<input_name>)(?!.)'
```

- Each file runs as its own test named after the file; `exited 1` with
  that test failed means the finding reproduced. Escape regex characters
  in the whole pattern (titles like `parse()` match nothing otherwise); a
  pattern ending in `$` runs nothing.
- Jest adds its own failure block with a code frame; the `==<pid>==` block
  comes first.
- **Frame names can be off:** source maps name a frame after the
  identifier at its location (`at length (src/parser.js:5:52)` for
  `o.length` inside `parse`). Trust `file:line`, not the name.
- If the input is already in the generated corpus: it replays with
  `--coverage` added.
- **A synchronous hang** ignores `JAZZER_TIMEOUT` in regression mode: the
  state `timed out` counts as a reproduced hang. No log has its JS stack:
  `crash_buckets.py` reads its kind from the fuzzing run's log, and a
  temporary CLI target (below) names its loop (`triage.md`, "Timeouts").
- **Minimize** through a temporary CLI target in scratch that calls the
  same code (`triage.md`).

## Regression tests and upstreaming

- Once upstreamed, the project's CI runs the fuzz tests in regression mode
  (`npm run test:fuzz`), next to its normal test job. That replays each
  test's regression directory (above), which checks the harness on its
  seeds; a fixed bug gets its own test instead (`regression.md`).
- `.cifuzz-corpus/` isn't committed; seeds worth keeping go into the
  regression directories (only the project's own data or generated inputs,
  and never inputs reaching a held-back bug).
- **Never leave a crash file in a regression directory:** the project's CI
  replays it, failing until the fix lands and revealing a held-back bug.
  The reproducer is the regression test (`regression.md`).

## Common failures

| Symptom | Cause and fix |
|---|---|
| `it.fuzz is not a function` | The test ran with the project's own config; pass `--config <jest_config>`, and check `testMatch` and `testRunner` |
| ESM imports fail under Jest | Jest transforms ESM to CommonJS; follow `docs/fuzz-targets.md` for ESM |
