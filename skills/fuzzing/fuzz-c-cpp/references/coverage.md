# C/C++ coverage

Coverage comes from the `coverage` variant (`build.md`): source-based, no
sanitizers, with the fuzzing build's fuzzing-mode macro and patches.

## libFuzzer harnesses

1. **Replay** the corpus in the coverage build, through the launcher,
   with run id `cov-<h>-<n>`. `L`, `F`, `H` and the environment are as in
   `run.md`
   next to this file:
   <!-- ci: c-cov-replay -->
   ```sh
   $L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
     --env LLVM_PROFILE_FILE="{run}/profraw/%c%p-%m.profraw" -- \
     "$F/build/coverage/<binary>" -merge=1 -rss_limit_mb=<memory_mb> -timeout=<input_timeout_s> \
     -artifact_prefix="{run}/artifacts/copy-{copy}/" "{scratch}/out" "$F/corpus/$H"
   ```
   - `-merge=1` into the empty `{scratch}/out` runs every input once, in
     child processes. It **continues past an input that crashes**: the
     crash is a finding for `fuzz-triage`, the input stays in the corpus,
     and the snapshot is still complete.
   - The `LLVM_PROFILE_FILE` pattern:
     - `%c` (continuous mode) writes counters as they change, so the
       crashed child's coverage is kept.
     - `%p` gives each process its own file.
     - `%m` gives each module its own file; a shared library is one
       module. Continuous mode needs `%m` on Linux.
   - If the host is Linux: `%c` needs the coverage variant's
     `-mllvm -runtime-counter-relocation` (`build.md`).
   - If other corpora count too, add their directories at the end. For
     example, add a long run's corpus or AFL++ `queue/` directories to
     measure what a run found.
2. **Check** the result line: `finished` (exit 0), and any findings.
   `LLVM Profile Error` in the log means the profile is empty: rebuild and
   replay. For example, an error that names
   `__llvm_profile_counter_bias` means the build lacks the relocation
   flag.
3. **Export** the profile to lcov (reads files only, no target code).
   `<objects>` is `-object=<lib>` for each library the target lives in,
   from the coverage build:
   <!-- ci: c-cov-export -->
   ```sh
   R="$F/runs/cov-$H-<n>"; mkdir -p "$F/coverage/$H"
   llvm-profdata merge -sparse "$R"/profraw/*.profraw -o "$F/coverage/$H/r<round>.profdata"
   llvm-cov export -format=lcov -instr-profile="$F/coverage/$H/r<round>.profdata" \
     "$F/build/coverage/<binary>" <objects> > "$F/coverage/$H/r<round>.lcov"
   ```
   The binary's coverage map has only the code linked into it. It lacks a
   shared library's code. It also lacks a static `.a`'s objects that the
   binary doesn't reference, since the link drops them. Without
   `-object`, the files of that code would be missing (`summary`: no
   coverage data).
   - If LLVM is older than 12: the export has no branch records (`BRDA`),
     which need LLVM 12 or later.

   Then continue with `coverage.py` as the calling skill says.

**Exact subset** (`reach.py`): to run exactly the inputs in `{dir}`, use
step 1's command with `{dir}` instead of the corpus directories, i.e.
`-merge=1 "{scratch}/out" "{dir}"`. A `-runs=0` directory replay adds an
empty input, and `-merge=1` refuses file arguments.

**Inputs that reach a line**: `reach.py` finds them, for targeted inputs
(`fuzz-coverage`'s corpus.md). It splits the `--replay` and `--export`
strings into arguments as a shell would. So single-quote each path in
them, as the block does, and in `<objects>`: `-object='<lib>'` for each
library of step 3:

<!-- ci: c-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --env "LLVM_PROFILE_FILE={out}/%c%p-%m.profraw" --timeout <timeout_s> \
  --replay "'$F/build/coverage/<binary>' -merge=1 -rss_limit_mb=<memory_mb> -timeout=<input_timeout_s> -artifact_prefix={run}/artifacts/copy-{copy}/ {scratch}/out {dir}" \
  --export "sh -c 'l=\$1; shift; llvm-profdata merge -sparse \"\$0\"/*.profraw -o \"\$0/p.profdata\" && llvm-cov export -format=lcov -instr-profile=\"\$0/p.profdata\" \"\$@\" > \"\$l\"' {out} {lcov} '$F/build/coverage/<binary>' <objects>"
```

**The gate's state-leak check** is the block below, for `fuzz-harness`'s
gate.md, "No state leaking between inputs". It replays a sample in one
process (side `one`). Then it replays each input of the sample in a fresh
process (side `fresh`). Both sides pass the inputs as files, which run
exactly those inputs. `<objects>` is as in step 3:

<!-- ci: c-leak-check -->
```sh
G="$F/runs/gate-$H-<n>"; S="$G/sample"; B="$F/build/coverage/<binary>"; mkdir -p "$S" "$F/coverage/$H"; ok=1
find "$F/corpus/$H" -type f -size +0c | sort -R | awk 'NR <= <sample_size>' | while IFS= read -r f; do i=$((i+1)); cp "$f" "$S/$i-${f##*/}"; done
for side in one fresh; do
  $L start --id "gate-$H-<n>-$side" --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" \
    --env LLVM_PROFILE_FILE="{run}/profraw/%p.profraw" -- sh -c '
      b=$1; side=$2; shift 2; o="-rss_limit_mb=<memory_mb> -timeout=<input_timeout_s>"; a="-artifact_prefix={run}/artifacts/copy-{copy}/"
      if [ "$side" = one ]; then exec "$b" $o "$a" "$@"; fi
      for f; do "$b" $o "$a" "$f" || exit; done' sh "$B" "$side" "$S"/* > /dev/null ||
    { echo "inconclusive: the $side side didn't finish (status $?, log $F/runs/gate-$H-<n>-$side/copy-0/log)"; ok=0; break; }
  llvm-profdata merge -sparse "$F/runs/gate-$H-<n>-$side"/profraw/*.profraw -o "$G/$side.profdata"
  llvm-cov export -format=lcov -instr-profile="$G/$side.profdata" "$B" <objects> > "$F/coverage/$H/gate-$side.lcov"
done
[ $ok = 1 ] && python3 "$F/bin/coverage.py" overlap "$F/coverage/$H/gate-one.lcov" "$F/coverage/$H/gate-fresh.lcov" \
  --counts --processes $(ls "$S" | wc -l) --root .
```

**One function,** with its branch counts:
`llvm-cov show -instr-profile=<profdata> <binary> <objects> -name=<function> -show-branches=count`.
The command shows a template's instantiations apart.
- If LLVM is 20 or older and `gaps` lists a template's branch as blocked:
  check that branch with this command. Those versions also export the
  instantiations' branches apart, so another instantiation may take the
  branch.

## FuzzTest

For replay, exact subsets and the state-leak check, see `fuzztest.md`,
"Coverage", next to this file.

## Optional tools

Two optional tools come from the AFL++ project:
- `fuzz-reachability`: the functions an entry point can statically reach.
- `cov-analysis`: replays, stability, report diffs.

They're not run here. They're AGPL-3.0, so only invoke them; never commit
or propose code they generate.
- If `fuzz-reachability` is installed: it separates "reachable but not
  reached", an input problem, from "unreachable from this harness", a
  harness problem.
