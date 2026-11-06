# Jazzer.js setup

## Install (dev dependencies only)

Installing runs package scripts: the first-build notice applies.

<!-- ci-skip: the toys' package.json files pin the same version -->
```sh
npm install --save-dev --save-exact @jazzer.js/core@4.0.0
```

Use the project's package manager, saving exact versions (`pnpm add -D -E`,
`yarn add -D -E`), and pin every `@jazzer.js/*` package to the same
version. Commit the manifest and lockfile changes as `fuzz(build): ...`.

## CLI targets

Add CLI targets under the directory the project uses for tests or tools
(e.g. `fuzz/<thing>.fuzz.js`), plus an npm script:
`"fuzz:<thing>": "mkdir -p fuzz/corpus/<thing> && jazzer fuzz/<thing>.fuzz.js fuzz/corpus/<thing> -- -max_total_time=60"`,
and a replay test in the project's runner (`regression.md`).

**TypeScript:** compile the sources one-to-one and unminified, to CommonJS,
into `.seclab/fuzz/build/ts/`, and the target loads them from there (`F`
and `P` as in `run.md`). Not a bundle or a minified build: its lcov no
longer matches the source files. CommonJS loads whatever the project's own
module settings: ESM output with `bundler` resolution keeps extensionless
imports, which Node can't load. A config of ours extends the project's
and compiles only the modules the target imports (`files`, one per
module; tsc adds what they import): test runners also search `.seclab/`,
and would run compiled copies of the project's tests.

<!-- ci: js-ts-build -->
```sh
printf '{"extends": "%s", "include": [], "files": ["%s"]}\n' "$P/tsconfig.json" "$P/<entry>" > "$F/build/tsconfig.json"
"$P/node_modules/.bin/tsc" -p "$F/build/tsconfig.json" --noEmit false --emitDeclarationOnly false --rootDir "$P" \
  --outDir "$F/build/ts" --sourceMap --module commonjs || echo "tsc exited $?: check the .js files"
echo '{"type": "commonjs"}' > "$F/build/ts/package.json"
```
`tsc` exits 2 on a type error, or on an option of the project's that only
fits ESM (TypeScript 5 with `bundler`), and still writes the files: check
that the target's `.js` files are there (a library that builds its JS with
another tool may set only declarations to emit). The `package.json` keeps
an ESM package's `"type": "module"` from applying to them. The source maps
give stack traces the `.ts` lines.
- If the sources import `.ts` paths: add
  `--rewriteRelativeImportExtensions`.
- If the code needs ESM (top-level `await`, `import.meta`): compile it,
  and only it, without `--module commonjs`, with `{"type": "module"}` and
  imports that name their `.js` files.
- At the hand-off: the target loads the project's own build output
  instead.

## Options

As `jazzer` arguments (`npx jazzer --help`), or as environment variables
with a JSON value (`JAZZER_CUSTOM_HOOKS`, `JAZZER_INCLUDES`, ...). Full
list: `docs/fuzz-settings.md`.

## Instrumentation checks

- **Files:** the gate's lcov, replayed with the fuzzing runs' `includes`
  and `excludes`, has `SF:` lines for the target's files; if it doesn't,
  they miss them (`harness.md`, "What gets instrumented").
- **Feedback:** the gate run's status lines show `cov:`. Without it,
  `dryRun` is on (an option, or Jest's `.jazzerjsrc.json`: `jest.md`): it
  turns off the fuzzing instrumentation but not Istanbul's, so the `SF:`
  lines still appear.
  <!-- ci: js-feedback-check -->
  ```sh
  grep -q '^#[0-9].* cov: ' "$F/runs/<run_id>/copy-0/log" || { echo "no coverage feedback: dryRun is on"; false; }
  ```
  Beyond that, a flat `cov:` counter proves nothing: a saturated corpus
  doesn't grow it either.
- **Detectors and hooks:** `JAZZER_VERBOSE=true` lists the loaded bug
  detectors and applied hooks.

## Common failures

| Symptom | Cause and fix |
|---|---|
| `Invalid type for Jazzer.js option` | An environment value isn't JSON of the right type (`true`, not `1`) |
| `` version `GLIBC_2.38' not found `` when Jazzer.js loads | Linux with glibc below 2.38: no prebuilt addon works, and there's no source build (`install.md`) |
