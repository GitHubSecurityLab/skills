# Advisory draft template

Fill every field; write "unknown" rather than guessing.

```markdown
# <Bug class> in <component> of <project>

**Found with AI-assisted fuzzing; reproduced and reviewed by <name>.**   <!-- removable -->

## Summary
<one or two sentences: what the bug is, where, what an attacker gets>

## Affected
- **Package:** <ecosystem>/<name> (or repository URL)
- **Affected versions:** <range>; tested on <release tag> and <commit sha> (HEAD on <date>)
- **Patched versions:** none yet
- **Component:** <file/function>

## Severity
- **CVSS 3.1:** `CVSS:3.1/AV:_/AC:_/PR:_/UI:_/S:_/C:_/I:_/A:_` (score <x.y>)
  - AV: <why> · AC: <why> · PR: <why> · UI: <why> · S: <why> · C/I/A: <why>
- **CWE:** CWE-<id> <name>

## Details
<root cause: the broken invariant, where attacker data enters, why existing
checks don't stop it; link code as path:line at the tested commit>

## Proof of concept
1. Build the reproducer against the library: <commands, with sanitizers>
2. Run: <command>
3. Expected output: <the key lines of the sanitizer report or the failed check>

Attached: the reproducer `<path>` (calls the project's API directly; no
fuzzer needed), and for reference the fuzzer input `<input file>` (hex:
`<short hex>` or base64 below) and the harness `<path>`.

## Impact
<who is affected in which deployment; what an attacker can do; limits>

## Suggested fix
<the patch, as a diff, with a note that it's a suggestion>

Tested: the reproducer fails before the fix and passes after; the
project's tests pass.

## Credits
<reporter name>
```

For several bugs in one project, write one draft per bug unless they share
a root cause.
