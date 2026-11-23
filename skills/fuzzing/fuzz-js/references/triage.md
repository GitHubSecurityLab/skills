# Jazzer.js triage

Jest `it.fuzz` tests: reproduce as `jest.md`, "Triage", says. The rest of
this file applies to them too. For them, its commands run a temporary CLI
target that calls the same code (`jest.md`, "Runs").

Every command below executes the target. Run it through the launcher,
with its block's run id (`repro-<h>-<n>`, `pp-<h>-<n>`, `prof-<h>-<n>` or
`min-<h>-<n>`), and with `L`, `F`, `H` and `P` as in `run.md` next to
this file. The report is in `runs/<id>/copy-0/log`. For a long run's
finding, set `P` to `<tree_package>` (`run.md`, "CLI target").

## Reproduce

Pass the finding file instead of a corpus directory; it runs once:
<!-- ci: js-cli-repro -->
```sh
$L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms> \
  -- -rss_limit_mb=<memory_mb> -artifact_prefix="{run}/artifacts/copy-{copy}/"
```

- The state `exited 77` (70 for a timeout) means the finding reproduced.
- If the finding is flaky: `-runs=100` after the `--` runs it 100 times
  in one process (`fuzz-triage`, "Reproduce"). Look for module-level
  state, timers, and promises that the harness doesn't await.

## Reading findings

Jazzer.js prints the finding to stderr, and `crash_buckets.py` reads
these reports:

```
==<pid>== Uncaught Exception: TypeError: Cannot read properties of null (reading 'length')
    at <name> (<file>:<line>:<column>)
```

- **Uncaught exceptions** are `unexpected_exception` unless the API
  documents them for invalid input (then the harness should catch them:
  `harness_bug`). A `TypeError` from attacker data often means missing
  input validation. Check for denial of service (a crashing server
  process) and corrupted state.
- **Detector findings** have a title line instead of
  `Uncaught Exception`: `Command Injection`, `Path Traversal`,
  `Remote Code Execution`, `Server Side Request Forgery (SSRF)` or
  `Prototype Pollution`. A detail line follows the title, e.g.
  `in execSync(): called with 'jaz_zer'`.
- **No stack** for thrown values that aren't `Error`s (`throw "boom"`):
  search the code for the message.
- **Prototype pollution** is detected after the input ran, so its report
  has no stack either. It shows only the prototype and the property:
  `Prototype of Object changed. Additional properties in object1: { 'polluted': ... }`.
  Replay with a tripwire on that property. The tripwire prints the stack
  of the write, and the write then doesn't happen, so the run ends
  `finished`.
  <!-- ci: js-pp-tripwire -->
  ```sh
  printf '%s\n' 'const proto = <prototype>.prototype, key = "<property>";
  Object.defineProperty(proto, key, { configurable: true, set(value) {
    // Other objects get their own property, as without the tripwire.
    if (this !== proto) return void Object.defineProperty(this, key, { value, writable: true, enumerable: true, configurable: true });
    console.error(new Error("<prototype>.prototype." + key + " written").stack);
  } });' > "$F/tripwire.js"
  $L start --id pp-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
    node -r "$F/tripwire.js" "$P/node_modules/.bin/jazzer" "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms>
  ```
- **CLI targets in ES modules: `file:line` is off,** pointing into the
  instrumented code (`parse.js:12` for line 7). `crash_buckets.py`'s
  locations are off too; the function names are right. For the real
  lines, add `--excludes='*'` before the `--` of the reproduction
  command. Then nothing is instrumented, and the detectors still apply.
- **Timeouts** (CLI exit status 70) have no JS stack: the log ends at
  libFuzzer's timeout line, so `crash_buckets.py` groups them by kind
  alone. An input that finishes near the limit (`Executed … in <n> ms`)
  is still slow: Jazzer.js passes `--timeout` to libFuzzer rounded up to
  whole seconds, and libFuzzer checks for timeouts only every
  `-timeout`/2 + 1 s. Replay such an input with `--timeout` at half its
  run time. Name each timeout's loop from a profile of its replay. The
  top of the bottom-up profile is the hot function or `RegExp: <pattern>`.
  <!-- ci: js-hang-profile -->
  ```sh
  $L start --id prof-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
    node --prof --no-logfile-per-isolate --logfile="{run}/v8.log" "$P/node_modules/.bin/jazzer" \
    "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms> || :
  node --prof-process "$F/runs/prof-$H-<n>/v8.log" | sed -n '/Bottom up/,+12p'
  ```
  Then run the growth test (`fuzz-triage`, "Find the root cause").

## Minimize

Minimize through the CLI target. The command below has `--sync`: drop it
for an async target. The target file records which kind the target is
(`run.md` next to this file). In async mode, minimization can keep an
input that doesn't crash.

<!-- ci: js-cli-minimize -->
```sh
mkdir -p "$F/crashes/<bucket>"
$L start --id min-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms> \
  --sync -- -minimize_crash=1 -runs=10000 -rss_limit_mb=<memory_mb> \
  -exact_artifact_path="$F/crashes/<bucket>/minimized"
```

Replay `minimized` either way (`fuzz-triage`, "Minimize").

If the finding is a short string: minimizing by hand (`xxd -g1`, then
shorten and replay) is often as quick.

## Detector findings: confirm the impact

- **Command injection, remote code execution:** check that the input
  reaches the sink as code or command syntax through a real entry point.
- **Path traversal:** check the path the application used against its
  base directory, and check whether the path is used for reading,
  writing or deleting.
- **SSRF:** check that the host or port comes from attacker input, not
  from configuration.
- **Prototype pollution:** to check whether the input chooses the key,
  change the key in the input and replay. If the input chooses the key,
  pollution of a built-in prototype is a vulnerability by itself
  (CWE-1321). Node.js has known gadgets that read such keys as options
  (`child_process`'s `shell` and `env`). A fixed key, or pollution of the
  project's own prototype, needs code that reads the polluted key through
  the prototype, from an object that doesn't set the key itself.

## Debugging

Run the CLI target under `node --inspect-brk`, or add `console.error`
calls to the harness temporarily. Remove both before committing.
