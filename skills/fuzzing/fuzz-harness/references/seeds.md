# Seeds and dictionaries

## When to use

- After a new harness passes the build, before the gate and the first runs.
- "Make seeds and a dictionary for this harness."

Not for harness changes (`SKILL.md`) or running fuzzers (runs.md).

## Reads

- `targets/<id>.md`: Harness (format, corpus and dictionary paths), Scope
  files, Sinks. `sinks/<id>.tsv` for boundary-value seeds.
- The language skill for seed and dictionary mechanics of the engine.

## Steps

1. **Collect seeds,** in this order. Put them in `corpus/<harness>/`
   (Jest: the package's `.cifuzz-corpus/<file>/<describe>/<test>/`; see the
   language skill).
   1. The previous campaign's corpus for this harness (`archive/`, or a
      merged corpus carried into a new campaign).
   2. Inputs committed by existing harnesses (seed directories, regression
      inputs, `*_seed_corpus.zip` or what an OSS-Fuzz `build.sh` zips).
   3. ClusterFuzzLite or CI corpus artifacts, when the project has them.
   4. The project's own test data, examples and fixtures (`tests/`,
      `testdata/`, `examples/`, `fixtures/`, `corpus/`).
   5. For a project on OSS-Fuzz, its harness's public corpus, for local
      use only (minimize it after the import): `https://storage.googleapis.com/<project>-backup.clusterfuzz-external.appspot.com/corpus/libFuzzer/<fuzzer>/public.zip`,
      where `<fuzzer>` is the harness's name with a `<project>_` prefix
      unless it already starts with one (`cjson_read_fuzzer`,
      `md4c_fuzz-mdhtml`). The project's OSS-Fuzz `Dockerfile` and
      `build.sh` name its seed archives and dictionaries.
   6. Well-licensed test suites for the format.
   7. Small valid inputs written from the format's specification: one per
      message type, opcode, record kind or `case` of the parser's dispatch
      code. For complex formats where mutation rarely produces valid
      structure, a generator script in
      `.seclab/fuzz/generators/<harness>.py` that writes many well-formed
      variants, deterministically (fixed seed).
   Add deliberately broken variants too (truncated, wrong magic, fields out
   of range, deep nesting): error-handling code that still processes
   attacker data is attack surface. Sources from outside the project (5, 6)
   stay local and are never proposed upstream.
   - If the imported corpus is large: minimize it before the gate
     (`fuzz-coverage`'s corpus.md).
   - After an import, list the files above the harness's input cap
     (`find <dir> -type f -size +<cap>c`): they slow every execution; keep
     them only if the format needs that size, and then raise the cap.
2. **Write seeds in the harness's input layout,** so each one means what
   it says:
   - raw-bytes harnesses (a format parser taking `bytes`, `byte[]`,
     `Buffer`, or one Java `String` with a length above the largest seed)
     take the format's files as they are;
   - a byte-provider split (`FuzzedDataProvider` in any language) reads
     integers and booleans from the end of the input, the first call the
     last bytes: the generator appends them to each file (or prepends
     them, for a harness that reads its options from a fixed prefix);
   - typed harnesses (FuzzTest domains, Jazzer typed parameters) don't map
     files to arguments: use the engine's seed mechanism (`.WithSeeds`,
     Jazzer's `@ValuePool`; the language skill).
   The gate's seeds-only replay (gate.md, "Reaches the target") shows a mismatch.
3. **Dictionary** in `dict/<harness>.dict`, existing ones first:
   - the project's own (`*.dict` next to its fuzzers or in its OSS-Fuzz
     project); for C/C++, the tokens the compiler extracts
     (`AFL_LLVM_DICT2FILE`, `fuzz-c-cpp`) and libFuzzer's "Recommended
     dictionary" at the end of a round's log (without `# Uses:`);
   - string literals in the scope files, every quote style, without
     format strings, messages, includes and module paths; keep those the
     code compares:
     <!-- ci: dict-literals -->
     ```sh
     echo '# literals from the scope files' >> .seclab/fuzz/dict/<harness>.dict
     grep -h -v -E -e '^[[:space:]]*(import[[:space:]]|#[[:space:]]*include|export[[:space:]].*[[:space:]]from[[:space:]])' \
       -e '(require|import)\(' <scope_files> \
       | grep -o -E -e '"([^"\\]|\\.){2,32}"' -e "'([^'\\\\]|\\\\.){2,32}'" -e '`[^`$\\]{2,32}`' \
       | LC_ALL=C grep -v -e '%' -e ' ' -e '[^ -~]' | sed -E 's/^.(.*).$/"\1"/' \
       | LC_ALL=C grep -x -E '"([^"\\]|\\["\\]|\\x[0-9A-Fa-f]{2})*"' | sort -u >> .seclab/fuzz/dict/<harness>.dict || :
     ```
   - keywords and tag names from the format spec, field separators;
   - for a common format, AFL++'s bundled dictionaries (`dictionaries/` in
     its installation) are a reference for tokens. Read them; don't copy
     their files into the workspace (AGPL);
   - keep it focused: dozens to a few hundred entries from this format.
   File syntax (libFuzzer, AFL++, Jazzer, Jazzer.js): one entry per line,
   `name="value"` or `"value"`, `#` comments, printable ASCII, escapes
   `\\`, `\"`, `\xAB`; any other line, or an empty file, stops libFuzzer.
4. **Record** the seed sources, generator and dictionary in the target file.

## Seeds proposed upstream

Only the project's own files or files the generator produced, with a clear
license. Never an input that reaches a held-back bug.

## Done when

- The corpus has valid and broken seeds for the main variants of the format
  (or the engine's structured equivalent), and the dictionary covers the
  format's keywords and the magic values in scope.
- Sources and paths are recorded.

## Pitfalls and shortcuts to reject

- Large seeds. They slow every execution; trim to the smallest input that
  shows the feature.
- Removing a seed that crashes. A crash on the seeds is a blocking bug:
  triage it now (`fuzz-triage`) and keep the seed, which checks the fix.
- Copying sample files of unknown origin into the project's repository.
- Huge generic dictionaries. Tokens from other formats dilute mutation.

## Writes

- `corpus/<harness>/`, `dict/<harness>.dict`, `generators/`.
- `targets/<id>.md`: Harness (corpus, dictionary), Notes (seed sources).
