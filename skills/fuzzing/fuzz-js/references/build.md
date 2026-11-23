# Jazzer.js setup

Jest `it.fuzz` tests: install as below, then follow `jest.md`, "Setup",
instead of the CLI targets section.

## Install (dev dependencies only)

Installing runs package scripts: the first-build notice applies.

<!-- ci-skip: the toys' package.json files pin the same version -->
```sh
npm install --save-dev --save-exact @jazzer.js/core@4.0.0
```

Use the project's package manager, and save exact versions
(`pnpm add -D -E`, `yarn add -D -E`). Pin every `@jazzer.js/*` package to
the same version. Commit the manifest and lockfile changes as
`fuzz(build): ...`.

## CLI targets

Add CLI targets under the directory the project uses for tests or tools
(e.g. `fuzz/<thing>.fuzz.js`), plus:
- an npm script:
  `"fuzz:<thing>": "mkdir -p fuzz/corpus/<thing> && jazzer fuzz/<thing>.fuzz.js fuzz/corpus/<thing> -- -max_total_time=60"`;
- a replay test in the project's runner (`regression.md`).

**TypeScript:** compile the sources to CommonJS, one-to-one and
unminified, into `.seclab/fuzz/build/ts/`. The target loads them from
there (`F` and `P` as in `run.md`).
- Not a bundle or a minified build: its lcov no longer matches the source
  files.
- Why CommonJS: it loads whatever the project's own module settings are.
  ESM output with `bundler` resolution keeps extensionless imports, which
  Node can't load.
- A config of ours extends the project's. Its `files` lists the modules
  the target imports, and tsc adds what they import. It compiles nothing
  else, since test runners also search `.seclab/` and would run compiled
  copies of the project's tests.

<!-- ci: js-ts-build -->
```sh
printf '{"extends": "%s", "include": [], "files": ["%s"]}\n' "$P/tsconfig.json" "$P/<entry>" > "$F/build/tsconfig.json"
"$P/node_modules/.bin/tsc" -p "$F/build/tsconfig.json" --noEmit false --emitDeclarationOnly false --rootDir "$P" \
  --outDir "$F/build/ts" --sourceMap --module commonjs || echo "tsc exited $?: check the .js files"
echo '{"type": "commonjs"}' > "$F/build/ts/package.json"
```

`<entry>` is the path from `$P` of the module the target imports. For
several modules, give `files` one `"%s"` per module, as in
`["%s", "%s"]`, and pass one `"$P/<module>"` argument per module.

`tsc` exits 2 on a type error, or on a project option that only fits ESM
(TypeScript 5 with `bundler`), and still writes the files. So check that
the target's `.js` files are there: a library that builds its JS with
another tool may set only declarations to emit. The `package.json` keeps
an ESM package's `"type": "module"` from applying to the compiled files.
The source maps give stack traces the `.ts` lines.

- If the sources import `.ts` paths: add
  `--rewriteRelativeImportExtensions`.
- If the code needs ESM (top-level `await`, `import.meta`): compile it,
  and only it, without `--module commonjs`, with `{"type": "module"}` and
  imports that name their `.js` files.
- When upstreaming: the target loads the project's own build output
  instead.

## Options

Give options as `jazzer` arguments (`npx jazzer --help`), or as
environment variables with a JSON value, such as `JAZZER_CUSTOM_HOOKS` and
`JAZZER_INCLUDES`. Full list: `docs/fuzz-settings.md`.

## Instrumentation checks

- **Files:** the gate's lcov, replayed with the fuzzing runs' `includes`
  and `excludes`, has `SF:` lines for the target's files. If it doesn't,
  those settings miss the files (`harness.md`, "What gets instrumented").
- **Feedback:** the gate run's status lines show `cov:`. If they don't,
  `dryRun` is on, as an option or in Jest's `.jazzerjsrc.json`
  (`jest.md`). `dryRun` turns off the fuzzing instrumentation but not
  Istanbul's, so the `SF:` lines still appear.
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
