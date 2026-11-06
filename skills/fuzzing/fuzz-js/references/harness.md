# Jazzer.js fuzz tests

Jest `it.fuzz` tests: `jest.md`.

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

Libraries often throw plain `Error` for invalid input. Filter on the class,
`if (e?.constructor !== Error) throw e;`: `instanceof Error` also catches
`TypeError` and `RangeError` (stack overflows too), the bugs worth finding.

**ES modules:** ESM targets use `export function fuzz(data)` and need
Node 20.6+ (20.11+ for hooks and detectors). In ES modules, named imports
of Node's built-ins (`import { execSync } from "node:child_process"`)
bypass the detectors and hooks, and the real call runs, unless the harness
calls `syncBuiltinESMExports()` before loading the target with `import()`
(a static import would load it first):

```javascript
// fuzz/header.fuzz.mjs
import { syncBuiltinESMExports } from "node:module";
syncBuiltinESMExports();
const { parseHeader, HeaderError } = await import("../src/header.js");
export function fuzz(data) { /* as above */ }
```

If a CommonJS harness `require`s an ES module: it calls
`require("node:module").syncBuiltinESMExports()` first. The ESM loader
leaves a module without branches uninstrumented, compare hooks included:
`export const isMagic = (s) => s === "magic"` never finds
`magic` unless the dictionary has it; CommonJS instruments it.

**TypeScript:** target the compiled JavaScript (`build.md`). Node 22.18 and
later import `.ts` files natively, uninstrumented (the gate's `SF:` check
shows it), and a `require` of one fails with a `SyntaxError`.

## HTTP handlers

Inject one request per input in process, without a socket:
`light-my-request` (a pinned dev dependency) takes an Express app or any
`(req, res)` handler (Fastify has `app.inject()`). Frameworks answer an
exception with a 500, so check the status. The target is async: run it
without `--sync`, with which requests pile up until memory runs out.

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

An API that takes an object (options, a document to merge or set) gets
the parsed input, with seeds in that shape (the project's test fixtures).
`JSON.parse` keeps a `__proto__` key as an own property, as an attacker's
JSON does, so merge and set APIs meet the prototype-pollution detector.
Functions, `undefined` and class instances need a `FuzzedDataProvider`.

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

**Shadowing keys:** where the API promises plain data keys (qs drops such
keys by default), check its result. An own key that shadows
`Object.prototype` (`toString`, `hasOwnProperty`) breaks callers that use
it, and the detector doesn't see it: no prototype changed.

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

`consumeBoolean()`, `consumeIntegralInRange(min, max)`,
`consumeNumberInRange(min, max)`, `consumeString(maxLength)` (one
argument, always UTF-8 since 4.0; older docs show a second argument),
`consumeBytes(maxLength)`, `consumeRemainingAsString()`,
`consumeRemainingAsBytes()`, `consumeStringArray(maxCount, maxLength)`,
`remainingBytes`. Nothing can be read after a `consumeRemaining*` call.
The `*Bytes` methods return arrays of numbers: `Buffer.from(...)` where
the code needs a buffer.
Integral and boolean values come from the end of the input, so any new
call reinterprets old seeds; the corpus is replayed for a new baseline
(`fuzz-coverage`).

## Seeds and dictionaries

- Seeds are written in the harness's layout: a harness passing `data`
  straight to a format parser takes the format's files one to one; with a
  `FuzzedDataProvider`, append the bytes of its integral and boolean values
  in reverse call order.
- Seeds go into the corpus directory argument (`corpus/<h>/`).
- Dictionaries: `-dict=<file>` in the fuzzer options.

## Bug detectors

On by default. What each one checks (Jazzer.js 4.0.0); a sink it sees
only partly needs a custom check (`fuzz-coverage`, "Check the sinks"):

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
- Disable one only with a recorded reason:
  `JAZZER_DISABLE_BUG_DETECTORS='["prototype-pollution"]'`, or
  `"disableBugDetectors"` in `.jazzerjsrc.json`.

## Hooks

Add a hook only after reading the code, for a known reason: e.g. a random
or time-dependent value made constant, or a checksum check
(`fuzz-harness`'s build.md, "Fuzzing-only patches").

- `registerBeforeHook`, `registerReplaceHook`, `registerAfterHook` from
  `@jazzer.js/hooking`: target function (dotted path for nested
  functions), package name (`""` for globals), async flag, hook function.
  An after hook gets the result as its fourth argument; on a Node
  built-in, its return value replaces the result, so return it.
- Load a hooks file with `--env JAZZER_CUSTOM_HOOKS="[\"<abs path>\"]"`.
  Hooks must be JavaScript, also in TypeScript projects.
- A replace hook also silences the built-in detector on that function.
- Report from a hook with `reportAndThrowFinding(message)` from
  `@jazzer.js/core`; put values on the second line of the message so
  findings group by the first.
- An in-process fake must keep the oracle: a stub that never fails hides
  the bug it stands in for.
- `JAZZER_VERBOSE=true` (a JSON value; `1` is rejected) or `--verbose`
  lists the applied hooks (`DEBUG: Applied Before-hook in ...`).

## What gets instrumented

`includes` (default `["*"]`) and `excludes` (default `["node_modules"]`)
are substrings of the absolute file path, and excludes win. Set both:
setting only `includes` empties `excludes`, and setting only `excludes`
empties `includes`, so nothing is instrumented. To fuzz a dependency too,
list it with absolute paths (`"src/"` alone also matches every package's
`src/`), and don't exclude `node_modules`:
`JAZZER_INCLUDES='["<pkg>/src/","<pkg>/node_modules/<dep>/"]' JAZZER_EXCLUDES='[]'`.
Instrumenting all of `node_modules` slows fuzzing a lot. Environment values
are JSON.
