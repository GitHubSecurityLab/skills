# Jazzer.js fuzz tests

Jest `it.fuzz` tests: write them as `jest.md`, "Writing a fuzz test",
says. That section also says where their seeds and dictionaries go. The
rest of this file applies to them too; an example's body goes inside
`it.fuzz`.

## CLI targets

```javascript
// fuzz/header.fuzz.js
const { parseHeader, HeaderError } = require("../src/header");

module.exports.fuzz = function (data) {
  try {
    parseHeader(data.toString("utf8"));
  } catch (e) {
    if (!(e instanceof HeaderError)) throw e;
  }
};
```

Libraries often throw plain `Error` for invalid input. Filter on the
class, `if (e?.constructor !== Error) throw e;`. A filter on
`instanceof Error` also catches `TypeError` and `RangeError`, stack
overflows included, and those are the bugs worth finding.

**ES modules:** ESM targets use `export function fuzz(data)`. In ES
modules, named imports of Node's built-ins, such as
`import { execSync } from "node:child_process"`, bypass the detectors and
hooks, and the real call runs. To prevent that, the harness calls
`syncBuiltinESMExports()` first and then loads the target with
`import()`, since a static import would load the target before that call:

```javascript
// fuzz/header.fuzz.mjs
import { syncBuiltinESMExports } from "node:module";
syncBuiltinESMExports();
const { parseHeader, HeaderError } = await import("../src/header.js");
export function fuzz(data) { /* as above */ }
```

If a CommonJS harness `require`s an ES module, it calls
`require("node:module").syncBuiltinESMExports()` first. The ESM loader
leaves a module without branches uninstrumented, compare hooks
included. So the fuzzer never finds `magic` in
`export const isMagic = (s) => s === "magic"` unless the dictionary has
it. CommonJS instruments such a module.

**TypeScript:** target the compiled JavaScript (`build.md`). Under
Jazzer.js, a `require` of a `.ts` file fails with a `SyntaxError` on every
Node version. Node 22.18 and later `import` `.ts` files natively, but
uninstrumented, and older versions refuse them. The gate's `SF:` check
shows the missing instrumentation.

## HTTP handlers

Inject one request per input in process, without a socket.
`light-my-request`, a pinned dev dependency, takes an Express app or any
`(req, res)` handler; Fastify has `app.inject()`. Frameworks answer an
exception with a 500, so check the status. The target is async, so run
it without `--sync`. With `--sync`, requests pile up until memory runs
out.

<!-- ci: js-http -->
```javascript
// fuzz/api.js
const inject = require("light-my-request");
const app = require("../src/app"); // an Express app, or any (req, res) handler

module.exports.fuzz = async function (data) {
  const res = await inject(app, { method: "POST", url: "/records", payload: data,
    headers: { "content-type": "application/json" } });
  if (res.statusCode >= 500) throw new Error("HTTP " + res.statusCode + "\n" + res.payload.slice(0, 200));
};
```

## Objects

An API that takes an object, such as options or a document to merge or
set, gets the parsed input. Give the harness seeds in that shape (the
project's test fixtures). `JSON.parse` keeps a `__proto__` key as an own
property, as an attacker's JSON does, so merge and set APIs meet the
prototype-pollution detector. Functions, `undefined` and class instances
need a `FuzzedDataProvider`.

<!-- ci: js-object-input -->
```javascript
// fuzz/merge.js
const { merge } = require("../src/merge");

module.exports.fuzz = function (data) {
  let doc;
  try { doc = JSON.parse(data.toString("utf8")); } catch { return; }
  if (doc !== null && typeof doc === "object") merge({}, doc);
};
```

**Shadowing keys:** where the API promises plain data keys, check its
result for own keys that shadow `Object.prototype`, such as `toString` or
`hasOwnProperty` (qs drops them by default). Such a key breaks callers
that use the shadowed property. The detector doesn't see it, since no
prototype changed.

<!-- ci: js-shadowing -->
```javascript
// fuzz/query.js
const { parse } = require("../src/query");
const shadowing = new Set(Object.getOwnPropertyNames(Object.prototype));

function checkKeys(value, seen = new Set()) {
  if (value === null || typeof value !== "object" || seen.has(value)) return;
  seen.add(value);
  for (const key of Object.keys(value)) {
    if (shadowing.has(key)) throw new Error("Own key shadows Object.prototype\n    key: " + key);
    checkKeys(value[key], seen);
  }
}

module.exports.fuzz = function (data) {
  checkKeys(parse(data.toString("utf8")));
};
```

## `FuzzedDataProvider` (`@jazzer.js/core`)

The provider offers `consumeBoolean()`,
`consumeIntegralInRange(min, max)`, `consumeNumberInRange(min, max)`,
`consumeString(maxLength)`, `consumeBytes(maxLength)`,
`consumeRemainingAsString()`, `consumeRemainingAsBytes()`,
`consumeStringArray(maxCount, maxLength)` and `remainingBytes`.
- `consumeString(maxLength)` takes one argument. It's always UTF-8
  since 4.0. Older docs show a second argument.
- Nothing can be read after a `consumeRemaining*` call.
- The `*Bytes` methods return arrays of numbers. Use `Buffer.from(...)`
  where the code needs a buffer.
- The provider's input layout: `fuzz-harness`, "Design the harness".

## Seeds and dictionaries

- Seeds are written in the harness's layout (`fuzz-harness`'s seeds.md,
  "Write seeds in the harness's input layout").
- Seeds go into the corpus directory argument (`corpus/<h>/`).
- Dictionaries: `-dict=<file>` in the fuzzer options.

## Bug detectors

Bug detectors are on by default. A sink that a detector sees only partly
needs a custom check (`fuzz-coverage`, "Check the sinks"). What each
detector checks, as of Jazzer.js 4.0.0:

| Detector | Reports when | Doesn't see |
|---|---|---|
| Command injection: `child_process` `exec`, `execFile`, `spawn`, `fork` and their `Sync` forms | the first argument (the command or file) contains `jaz_zer` | the arguments: `spawnSync("sh", ["-c", input])`, `shell: true` with arguments, option injection. **Partly** |
| Path traversal: `fs`, `fs/promises`, `path.normalize`, `path.resolve` | the first path argument (the second, too, for copy, rename and links) contains `../../jaz_zer` | a path already resolved: `readFileSync(path.resolve(base, input))`. **Partly** |
| Remote code execution: `eval`, `Function` | the code contains `jaz_zer` | `vm`, `require` or `import` of an attacker path |
| SSRF: `net`, `tls`, `http(s)`, `http2`, `dgram` | any connection not allowed | `socket.connect(port, host)` without a callback |
| Prototype pollution | a built-in prototype (`Object`, `Array`, `String`, `Number`, `Boolean`, `Function`) changed, checked after every input | the project's own prototypes; `__proto__` assignments only with the opt-in check (slow, noisy): `getBugDetectorConfiguration("prototype-pollution")?.instrumentAssignmentsAndVariableDeclarations()` in a custom-hooks file |

A call without the trigger value runs for real.

- SSRF: allow the harness's own local services in a custom-hooks file:
  ```javascript
  const { getBugDetectorConfiguration } = require("@jazzer.js/bug-detectors");
  getBugDetectorConfiguration("ssrf")?.addPermittedTCPConnection("localhost", 8080);
  ```
- Disable a detector only with a recorded reason:
  `JAZZER_DISABLE_BUG_DETECTORS='["prototype-pollution"]'`, or
  `"disableBugDetectors"` in `.jazzerjsrc.json`.

## Hooks

Add a hook only after reading the code, for a known reason: e.g. a random
or time-dependent value made constant, or a checksum check
(`fuzz-harness`'s build.md, "Fuzzing-only patches").

- `registerBeforeHook`, `registerReplaceHook` and `registerAfterHook`
  from `@jazzer.js/hooking` take these arguments:
  - the target function, a dotted path for nested functions;
  - the package name, `""` for globals;
  - the async flag;
  - the hook function.
- An after hook gets the result as its fourth argument. On a Node
  built-in, the after hook's return value replaces the result, so return
  the result.
- Load a hooks file with `--env JAZZER_CUSTOM_HOOKS="[\"<abs path>\"]"`.
  Hooks must be JavaScript, even in TypeScript projects.
- A replace hook also silences the built-in detector on that function.
- Report from a hook with `reportAndThrowFinding(message)` from
  `@jazzer.js/core`. Put values on the second line of the message, so
  that findings group by the first line.
- An in-process fake must keep the oracle: a stub that never fails hides
  the bug that the code it stands in for would reveal.
- `JAZZER_VERBOSE=true` or `--verbose` lists the applied hooks
  (`DEBUG: Applied Before-hook in ...`). `JAZZER_VERBOSE` takes a JSON
  value; `1` is rejected.

## What gets instrumented

`includes` and `excludes` are substrings of the absolute file path, and
excludes win. `includes` defaults to `["*"]`, and `excludes` to
`["node_modules"]`. Set both:
- Setting only `includes` empties `excludes`.
- Setting only `excludes` empties `includes`, so nothing is
  instrumented.

To fuzz a dependency too, add it to `includes`, and don't exclude
`node_modules`:
- List the project's files with absolute paths: `"src/"` alone also
  matches every package's `src/`. That includes another package of the
  workspace: list its directory, since Node loads it from there, not
  from its link in `node_modules`.
- List the dependency as `/node_modules/<dep>/`, or
  `/node_modules/@<scope>/<dep>/` for a scoped one. That matches it
  wherever the package manager put it: in an npm workspace, usually the
  root's `node_modules`; with pnpm, under `node_modules/.pnpm/`.

Pass both settings to every run of the harness, as launcher options:
`--env JAZZER_INCLUDES="[\"$P/src/\",\"/node_modules/<dep>/\"]"`
and `--env JAZZER_EXCLUDES='[]'`. Instrumenting all of `node_modules`
slows fuzzing a lot. Environment values are JSON. Check that the gate's
lcov has `SF:` lines for the dependency's files too (`build.md`,
"Instrumentation checks").
