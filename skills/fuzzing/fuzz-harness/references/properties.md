# Checks beyond crashes

**Assert only what the spec or the documentation guarantees.** A wrong
property produces `harness_bug` verdicts, not findings.

**A check must be able to fail:** it compares values computed
independently, and its failure reaches the engine.

**Reuse first:** a property-based test of the project (the threat model
notes them) states a property its maintainers accept. Run its assertion
on the harness's decoded value before writing new ones, and add its
example values to the seeds.

How to signal a failure: `abort()` (or `__builtin_trap()`) in C/C++, a
thrown exception or failed assertion in Java and JS, a failing `EXPECT_*` or
`ASSERT_*` in FuzzTest. The language skill shows the idiom. Java/JS: raise
the check's failure outside the `try` that catches the target's documented
exceptions, or the catch swallows it.

## Patterns

**Round-trip.** Decode the input; if it decodes, encode and decode again and
compare the decoded values.

```
v = decode(input)
if v is ok:
    assert decode(encode(v)) == v
```

Compare the decoded value with the decoded re-encoding, not bytes: many
formats have several encodings for one value, and comparing `encode(v)`
with itself can't fail. The same pattern fits parse → serialize → parse,
compress → decompress, escape → unescape.

**Differential.** Two implementations or modes of the same operation must
agree: streaming vs. one-shot parsing, fast path vs. reference
implementation, strict vs. lenient mode on inputs both accept, two libraries
for the same format.

```
a = parse_streaming(input); b = parse_whole(input)
assert outcome(a) == outcome(b)        # both fail, or both succeed with equal values
```

Compare only when both sides consumed the whole input with the same
options; otherwise a difference is expected and the check reports noise.

**Invariants that hold for all inputs:**
- idempotence: `normalize(normalize(x)) == normalize(x)`;
- bounds: output size and allocations stay proportionate to the input
  (catches decompression bombs and quadratic blow-ups before they become
  timeouts);
- API consistency: the length the API reports equals the bytes it wrote;
  `size()` matches the number of elements iterated; slices and offsets
  the parser reports stay inside the input.

**Security checks at sinks without an engine detector, or with one that
sees them only partly** (from the target's sink list):
- the resolved path stays inside the base directory;
- a sanitizer's output contains no markup it should have removed;
- a template or query receives the input only as data, not as syntax;
- an integrity check never accepts modified data (flip a byte of a signed
  input and assert verification fails).

## Preconditions and comparators

- **Preconditions:** a property usually holds only for some inputs: a
  complete document without trailing data, unique keys, finite numbers,
  valid escapes, inputs both sides accept. Check the precondition in the
  harness and skip only the assertion, never the call. Record each one in
  the target file, so the user sees what isn't checked.
- **Comparators:** use an equality whose semantics you know for every
  value. A library's own `compare` or `equals` may differ on duplicate
  keys, NaN or numeric tolerance. With a deterministic serializer,
  `encode(decode(encode(v))) == encode(v)` compares two independently
  computed encodings and needs no equality function. Lossy or
  non-canonical formats: start comparisons from the decoded value.
- **Expect refinements:** a new property usually needs one to three, each
  from a minimized violation: decide whether the property or the code is
  wrong. Refine for the cause, not the instance: the precondition or
  comparator excludes the whole class of inputs the guarantee doesn't
  cover, not the one input that tripped it.

## Rules

- Add checks to the existing harness for that workflow, unless the check
  is slow or noisy: a slow or noisy property may get its own harness, so
  the fast one keeps its speed and isn't stopped by the property's
  findings (`SKILL.md`, "No unjustified duplication").
