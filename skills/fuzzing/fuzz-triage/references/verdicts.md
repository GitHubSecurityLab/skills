# Verdicts

Assign exactly one verdict per bucket, with a confidence (high, medium,
low) and the evidence behind it. The verdict says what the bug is. The
State (below) says where the finding stands, and the Fixed upstream field
says whether upstream fixed it.

| Verdict | Meaning | Evidence needed |
|---|---|---|
| `vulnerability` | Reachable by an attacker under the threat model, with security impact | the real entry point and deployment that pass attacker data here; the impact (memory corruption, injection, DoS from a small input, ...) |
| `library_hardening` | A real bug with no realistic attacker path; the library should still defend itself | why no attacker path exists (e.g. only reachable with trusted configuration) |
| `harness_bug` | The bug is in our harness | the harness line at fault, or the API contract it violates |
| `oom` | Out of memory | input size vs. allocation size; a vulnerability only if the attacker controls an unbounded size from a small input |
| `timeout` | Hang or algorithmic blow-up (DoS); exponential ReDoS shows up here (quadratic: `fuzz-coverage`'s regex check) | input size vs. run time; the loop or pattern responsible |
| `assertion_failure` | An `assert()` or `abort()` in the target fired | what it guards; whether production builds keep it (a crash in production is DoS; a removed assert may hide memory corruption behind it) |
| `unexpected_exception` | An exception the API doesn't document (C++: uncaught, libFuzzer's `deadly signal`) | the exception and where; a robustness finding, not a vulnerability unless it causes DoS or corrupted state |
| `property_violation` | A property checked by the harness failed | the property, the input and the outputs; a vulnerability only when it shows security impact (e.g. a parser differential that enables a bypass) |

## State and Fixed upstream

| State | When | Evidence needed |
|---|---|---|
| `open` | the default | |
| `duplicate of <bucket>` | same root cause as another bucket; it takes that bucket's verdict | the shared root cause |
| `introduced by our fix for <bucket>` | the bug is in our fix for that bucket: the reproducer passes without our fixes (`SKILL.md`, "Rule out our own fixes") | the `base` run, and the fix commit after which the reproducer fails |
| `non-reproducible` | the inputs don't crash in `SKILL.md`'s "Reproduce" attempts | the suspected cause (threads, time, state) |
| `needs investigation` | unresolved: needs a human | what was tried and what is unclear |

A State doesn't change a bucket's Kind (its crash type) or its verdict,
except that a duplicate takes the verdict of the bucket it duplicates. A
bucket without a verdict yet, such as a `non-reproducible` one, has `-`;
its Kind still counts for the hold-back rule.

**Fixed upstream** records the fixing commit, from the reproducer passing
on upstream HEAD, and whether the latest release is still affected. If
the release is still affected, raise "fixed upstream, not yet released"
with the user. Being fixed isn't being public, so nothing else changes.

Once the hold-back rule (`fuzz-campaign`, safety rules) covers a finding,
a later triage never reclassifies the finding out of the rule until it's
public and fixed upstream; it adds a note instead.

Our own local fixes (fix.md) go in the bucket's Fix section.

## Impact by crash type

The table below is the starting point for step 5 of "Deciding" and for
the report's severity. It follows ClusterFuzz's default classification:
memory corruption is a security bug; near-null dereferences, stack
overflows, OOMs, timeouts, aborts and leaks aren't. ASan's `SCARINESS`
line gives a similar first hint. Read the report, not only its title: a
`SEGV` on a wild address can be a write.

| Crash type | Default impact | Questions |
|---|---|---|
| Out-of-bounds or use-after-free **write**, double free | memory corruption, potentially code execution | the write's size and distance; does the attacker control offset and value? |
| Out-of-bounds or use-after-free **read** | information disclosure, or a crash | does the read data reach the attacker? how far, how much? |
| `SEGV` near address 0 (below 4096), stack overflow from recursion, OOM, timeout, `assert`/`abort`, leak | availability only | a long-lived process that serves others? from a small input? |
| UBSan without a memory error (shift, signed overflow) | usually none by itself | is the value used as a size, an index, an allocation? |
| Java/JS detector finding (injection, path traversal, SSRF, deserialization, prototype pollution) | the detector's class | does the input reach the sink as syntax, through a real entry point? For prototype pollution: does the input choose the key (`fuzz-js`)? |
| Uncaught exception, property violation | robustness | does it corrupt state or bypass a check (a parser differential)? |

## Deciding

1. **Is it our harness?** The crash is in harness code, or the harness
   breaks a documented precondition (wrong length, non-terminated string,
   freed object, call order) → `harness_bug`. Whether the data is trusted
   is step 5's question, not this one. For example, a harness that passes
   3 bytes to `parse_header(buf, len)`, documented to need at least 4, has
   a `harness_bug`. A harness that fuzzes the configuration file parser,
   while the threat model says only administrators write that file, has
   no `harness_bug`: it respects the API. A crash there is a real bug
   without an attacker path (`library_hardening`, or the type-specific
   verdict).
2. **Does it reproduce?** No after `SKILL.md`'s "Reproduce" attempts →
   State `non-reproducible`.
3. **Same root cause as a known bucket?** → State `duplicate of <bucket>`
   (which bucket keeps its State: `SKILL.md`, "Find the root cause").
4. **Type of failure:** memory error, detector finding, property, exception,
   resource exhaustion, assertion.
5. **Attacker path:** using the threat model, name the real entry point
   and deployment.
   - A path exists and the impact (table above) matters there →
     `vulnerability`, whatever the crash type; the bucket's Kind keeps
     the crash type. That covers memory corruption, injection findings
     and confirmed impact. An availability-only bug is a `vulnerability`
     only where a DoS matters: a small input triggers it in a server or
     shared service that parses untrusted input.
   - Otherwise → `library_hardening` for memory errors, or the
     type-specific verdict.

   **Before `vulnerability`:** the bug's reproducer (`SKILL.md`, "Write
   the reproducer") calls the project's CLI or public API, and still
   fails on a build without fuzzing-only code. For C/C++, that's a build
   without the fuzzing-mode macro, like the project's regression tests:
   the macro turns on our fuzzing-only patches and the project's own
   `FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION` blocks. Then ask three
   devil's-advocate questions, and write down the answers:
   - Would a real caller ever pass these values?
   - Does a check before this entry point already reject them?
   - Is the impact more than a crash of a process the attacker already
     controls?
6. **Unsure after this?** → State `needs investigation`, with the open
   question; the verdict says what's known so far.

## Special cases

- **Fuzzing-only code** (a checksum or signature bypassed by our patch or
  the project's own `FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION` block): a
  crash behind the bypass gets the check in `SKILL.md`, "Find the root
  cause", before a verdict.
- **Leaks:** `library_hardening` by default; `vulnerability` only when an
  attacker can grow memory without bound in a long-lived process. Leaks
  from the fuzzing engine only: `SKILL.md`, "Group".
- **C/C++ reports that change the verdict** (a C++ standard-library
  check's abort, MSan, UBSan integer checks): `fuzz-c-cpp`'s triage.md,
  "Reading reports".
- **Dependencies:** if the root cause is in a dependency, vendored or
  not, the verdict stands, but the report goes to that project
  (report.md).
