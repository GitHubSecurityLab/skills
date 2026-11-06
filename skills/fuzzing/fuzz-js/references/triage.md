# Jazzer.js triage

Every command below executes the target: run it through the launcher
(`repro-<h>-<n>`), with `L`, `F`, `H` and `P` as in `run.md` next to this
file. The report is in `runs/repro-<h>-<n>/copy-0/log`.

## Reproduce

Pass the file instead of a corpus directory; it runs once:
<!-- ci: js-cli-repro -->
```sh
$L start --id repro-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms> \
  -- -rss_limit_mb=<memory_mb> -artifact_prefix="{run}/artifacts/copy-{copy}/"
```
The state `exited 77` (70 for a timeout) means the finding reproduced.
If it's flaky: `-runs=100` after the `--` runs it 100 times in one process
(`fuzz-triage`, "Reproduce"); look for module-level state, timers, and
promises the harness doesn't await.

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
  input validation; check for denial of service (a crashing server
  process) and corrupted state.
- **Detector findings** have a title line instead of `Uncaught Exception`:
  `Command Injection`, `Path Traversal`, `Remote Code Execution`, `Server
  Side Request Forgery (SSRF)`, `Prototype Pollution`, each followed by a
  detail line (e.g. `in execSync(): called with 'jaz_zer'`).
- **No stack** for thrown values that aren't `Error`s (`throw "boom"`):
  search the code for the message.
- **Prototype pollution** is detected after the input ran, so its report
  has no stack either, only the prototype and the property (`Prototype of
  Object changed. Additional properties in object1: { 'polluted': ... }`).
  Replay with a tripwire on that property: it prints the stack of the
  write, which then doesn't happen, so the run ends `finished`.
  <!-- ci: js-pp-tripwire -->
  ```sh
  cat > "$F/tripwire.js" <<'EOF'
  const proto = <prototype>.prototype, key = "<property>";
  Object.defineProperty(proto, key, { configurable: true, set(value) {
    // Other objects get their own property, as without the tripwire.
    if (this !== proto) return void Object.defineProperty(this, key, { value, writable: true, enumerable: true, configurable: true });
    console.error(new Error("<prototype>.prototype." + key + " written").stack);
  } });
  EOF
  $L start --id pp-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
    node -r "$F/tripwire.js" "$P/node_modules/.bin/jazzer" "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms>
  ```
- **CLI targets in ES modules: `file:line` is off,** pointing into the
  instrumented code (`parse.js:12` for line 7), and so are
  `crash_buckets.py`'s locations; the names are right. For the real lines,
  add `--excludes='*'` before the `--` of the reproduction command: nothing
  is instrumented, and the detectors still apply.
- **Timeouts** (CLI exit status 70) have no JS stack: the log ends at
  libFuzzer's timeout line, so `crash_buckets.py` groups them by kind
  alone. One that finishes near the limit (`Executed … in <n> ms`) is
  still slow: replay it with `--timeout` at half its time. Name each
  one's loop from a profile of its replay: the top of the bottom-up
  profile is the hot function or `RegExp: <pattern>`.
  <!-- ci: js-hang-profile -->
  ```sh
  $L start --id prof-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
    node --prof --no-logfile-per-isolate --logfile="{run}/v8.log" "$P/node_modules/.bin/jazzer" \
    "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms> || :
  node --prof-process "$F/runs/prof-$H-<n>/v8.log" | sed -n '/Bottom up/,+12p'
  ```
  Then grow the relevant part of the input over several sizes (1x, 2x,
  4x, 8x) and compare run times. Growth faster than the size shows an
  algorithmic DoS (linear code also doubles), and so does a small input
  that already hits the timeout: e.g. a regular expression with
  catastrophic backtracking.

## Minimize

Through the CLI target, with `--sync` for a synchronous target (the
target file records which, `run.md` next to this file): in async mode,
minimization can keep an input that doesn't crash.

<!-- ci: js-cli-minimize -->
```sh
mkdir -p "$F/crashes/<bucket>"
$L start --id min-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
  "$P/node_modules/.bin/jazzer" "$P/<target_file>" "<finding>" --timeout=<input_timeout_ms> \
  --sync -- -minimize_crash=1 -runs=10000 -rss_limit_mb=<memory_mb> \
  -exact_artifact_path="$F/crashes/<bucket>/minimized"
```

Replay `minimized` either way (`fuzz-triage`, "Minimize").

If the finding is a short string: minimizing by
hand (`xxd -g1`, then shorten and replay) is often as quick.

## Detector findings: confirm the impact

- **Command injection, remote code execution:** check that the input
  reaches the sink as code or command syntax through a real entry point.
- **Path traversal:** check against the application's base directory and
  whether the path is used for reading, writing or deleting.
- **SSRF:** check that the host or port comes from attacker input, not from
  configuration.
- **Prototype pollution:** if the input chooses the key (change it in the
  input and replay), pollution of a built-in prototype is a vulnerability
  by itself (CWE-1321): Node.js has known gadgets that read such keys as
  options (`child_process`'s `shell` and `env`). A fixed key, or the
  project's own prototype, needs the code that reads it.

## Debugging

`node --inspect-brk` on the CLI target, or `console.error` in the harness
temporarily. Remove both before committing.
