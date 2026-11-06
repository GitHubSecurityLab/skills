# Reproducing an OSS-Fuzz testcase

OSS-Fuzz's own build decides whether the testcase reproduces: a local
build that doesn't crash isn't decisive, least of all on arm64.

1. **Build at the crash revision.** In a clone of `google/oss-fuzz`
   (Docker), build with the report's sanitizer and architecture
   (`--architecture i386` for i386 jobs) and a checkout at its crash
   revision:
   `python3 infra/helper.py build_fuzzers --sanitizer <sanitizer> <project> <checkout>`.
2. **Reproduce.** Run
   `python3 infra/helper.py reproduce <project> <fuzzer> <testcase>`
   as the reproduction run (`SKILL.md`, "Reproduce").
