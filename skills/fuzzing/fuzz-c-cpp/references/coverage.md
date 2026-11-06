# C/C++ coverage

Coverage comes from the `coverage` variant (`build.md`): source-based, no
sanitizers, with the fuzzing build's fuzzing-mode macro and patches.

## libFuzzer harnesses

1. **Replay** the corpus in the coverage build, through the launcher (run
   id `cov-<h>-<n>`), with `L`, `F`, `H` and the environment as in `run.md`
   next to this file:
   <!-- ci: c-cov-replay -->
   ```sh
   $L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
     --env LLVM_PROFILE_FILE="{run}/profraw/%c%p.profraw" -- \
     "$F/build/coverage/<binary>" -merge=1 -rss_limit_mb=<memory_mb> -timeout=<input_timeout_s> \
     -artifact_prefix="{run}/artifacts/copy-{copy}/" "{scratch}/out" "$F/corpus/$H"
   ```
   - `-merge=1` into the empty `{scratch}/out` runs every input once, in
     child processes, and **continues past an input that crashes**: the
     crash is a finding for `fuzz-triage`, the input stays in the corpus,
     and the snapshot is still complete.
   - `%c` (continuous mode) writes counters as they change, so the crashed
     child's coverage is kept; `%p` gives each process its own file.
   - If the host is Linux: `%c` needs the coverage variant's
     `-mllvm -runtime-counter-relocation` (`build.md`).
   - If other corpora count too (e.g. a long run's corpus, or AFL++
     `queue/` directories, to measure what it found): add their
     directories at the end.
2. **Check** the result line: `finished` (exit 0), and any findings.
   `LLVM Profile Error` in the log (e.g. one naming
   `__llvm_profile_counter_bias`: built without the relocation flag)
   means the profile is empty: rebuild and replay.
3. **Export** the profile to lcov (reads files only, no target code). Add
   `-object=<lib>` after the binary for each library the target lives in:
   <!-- ci: c-cov-export -->
   ```sh
   R="$F/runs/cov-$H-<n>"; mkdir -p "$F/coverage/$H"
   llvm-profdata merge -sparse "$R"/profraw/*.profraw -o "$F/coverage/$H/r<round>.profdata"
   llvm-cov export -format=lcov -instr-profile="$F/coverage/$H/r<round>.profdata" \
     "$F/build/coverage/<binary>" > "$F/coverage/$H/r<round>.lcov"
   ```
   A shared library, or a static `.a`, whose objects the binary doesn't
   reference are dropped at link time, so without `-object` their files
   would be missing (`summary`: no coverage data). Then continue with
   `coverage.py` as the calling skill says.
   - If LLVM is older than 12: the export has no branch records (`BRDA`);
     they need LLVM 12 or later.

**Exact subset** (`reach.py`, the gate's state-leak check): to run
exactly the inputs in `{dir}`, use the same command with `{dir}` instead
of the corpus directories, i.e. `-merge=1 "{scratch}/out" "{dir}"`. A
`-runs=0` directory replay adds an empty input, and `-merge=1` refuses
file arguments.

**Inputs that reach a line** (`reach.py`, directed inputs in
`fuzz-coverage`):

<!-- ci: c-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --env "LLVM_PROFILE_FILE={out}/%c%p.profraw" --timeout <timeout_s> \
  --replay "$F/build/coverage/<binary> -merge=1 -rss_limit_mb=<memory_mb> -timeout=<input_timeout_s> -artifact_prefix={run}/artifacts/copy-{copy}/ {scratch}/out {dir}" \
  --export "sh -c 'llvm-profdata merge -sparse \$0/*.profraw -o \$0/p.profdata && llvm-cov export -format=lcov -instr-profile=\$0/p.profdata \$1 > \$2' {out} $F/build/coverage/<binary> {lcov}"
```

**The gate's state-leak check** (`fuzz-harness`'s gate.md, "No state leaking between inputs"):
a sample replayed in one process (A), then each input in a fresh process
(B), as files, which run exactly those inputs:

<!-- ci: c-leak-check -->
```sh
G="$F/runs/gate-$H-<n>"; S="$G/sample"; B="$F/build/coverage/<binary>"; mkdir -p "$S" "$F/coverage/$H"
find "$F/corpus/$H" -type f -size +0c | sort -R | awk 'NR <= <sample_size>' | while IFS= read -r f; do i=$((i+1)); cp "$f" "$S/$i-${f##*/}"; done
for side in one fresh; do
  $L start --id "gate-$H-<n>-$side" --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
    --env LLVM_PROFILE_FILE="{run}/profraw/%p.profraw" -- sh -c '
      b=$1; side=$2; shift 2; o="-rss_limit_mb=<memory_mb> -timeout=<input_timeout_s>"; a="-artifact_prefix={run}/artifacts/copy-{copy}/"
      if [ "$side" = one ]; then exec "$b" $o "$a" "$@"; fi
      for f; do "$b" $o "$a" "$f" || exit; done' sh "$B" "$side" "$S"/* > /dev/null
  llvm-profdata merge -sparse "$F/runs/gate-$H-<n>-$side"/profraw/*.profraw -o "$G/$side.profdata"
  llvm-cov export -format=lcov -instr-profile="$G/$side.profdata" "$B" > "$F/coverage/$H/gate-$side.lcov"
done
python3 "$F/bin/coverage.py" overlap "$F/coverage/$H/gate-one.lcov" "$F/coverage/$H/gate-fresh.lcov" --counts \
  --processes $(ls "$S" | wc -l) --root .
```

**One function,** with its branch counts:
`llvm-cov show -instr-profile=<profdata> <binary> -name=<function> -show-branches=count`.
It shows a template's instantiations apart.
- If `gaps` lists a template's branch as blocked, on LLVM 20 or older:
  check that branch with this command. Those versions also export the
  instantiations' branches apart, so another instantiation may take it.

**Macro branches** count at every expansion (macro-heavy files:
`fuzz-coverage`, "Correct the scope").

## FuzzTest

Replay, exact subsets and the state-leak check: `fuzztest.md`, "Coverage",
next to this file.

## Optional tools

`fuzz-reachability` (functions an entry point can statically reach) and
`cov-analysis` (replays, stability, report diffs), both from the AFL++
project: not run here, and AGPL-3.0, so only invoke them; never commit or
propose code they generate.
- If `fuzz-reachability` is installed: it separates "reachable but not
  reached" (an input problem) from "unreachable from this harness" (a
  harness problem).
