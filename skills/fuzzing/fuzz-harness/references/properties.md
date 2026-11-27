# Checks beyond crashes

**Assert only what the spec or the documentation guarantees.** A wrong
property produces `harness_bug` verdicts, not findings.

**A check must be able to fail:** it compares values computed
independently, and its failure reaches the engine.

**Reuse first:** a property-based test of the project states a property
its maintainers accept. The threat model lists these tests, in the
Property tests column of Sources (focused mode: in the entry point's
section). Run a test's assertion on the harness's decoded value before
writing new ones, and add its example values to the seeds.

How to signal a failure: `abort()` (or `__builtin_trap()`) in C/C++, a
thrown exception or failed assertion in Java and JS, a failing `EXPECT_*` or
`ASSERT_*` in FuzzTest. The language skill shows the idiom. In Java and JS,
raise the check's failure outside the `try` that catches the target's
documented exceptions; otherwise that catch swallows the failure.

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
implementation, strict vs. lenient mode on inputs the strict mode accepts,
two libraries for the same format.

```
a = parse_streaming(input); b = parse_whole(input)
assert outcome(a) == outcome(b)        # both fail, or both succeed with equal values
```

Give both sides the same input and options. One side rejecting what the
other accepts is what this check finds, so a failure isn't a reason to
skip the comparison. Skip it only where a difference is expected, or the
check reports noise:
- a side succeeded without reading to the end of the input, e.g. a
  parser that reads only the first value;
- a side is meant to reject more, e.g. a strict mode against a lenient
  one: skip only the inputs that side rejects.

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

A sink group that no sanitizer, engine detector or such check can see is
uncheckable: record that state in the target's Sinks table, with what goes
unseen in its Note (`fuzz-coverage`, "Check the sinks").

## Preconditions and comparators

- **Preconditions:** a property usually holds only for some inputs: a
  complete document without trailing data, unique keys, finite numbers,
  valid escapes, inputs a strict mode accepts. Check the precondition in
  the harness and skip only the assertion, never the call. Record each
  one in the target file, so the user sees what isn't checked.
- **Comparators:** use an equality whose semantics you know for every
  value. A library's own `compare` or `equals` may differ on duplicate
  keys, NaN or numeric tolerance. Where you can't write such an equality
  and the serializer is deterministic, compare encodings instead:
  `encode(decode(encode(v))) == encode(v)`. That can't see data `encode`
  drops every time, such as a field it never writes. Lossy or
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
