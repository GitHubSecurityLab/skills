# Jazzer.js coverage

Jest `it.fuzz` tests: see `jest.md`, "Coverage", which points back here
for the export and the state-leak check.

Jazzer.js writes lcov through Istanbul. Coverage instrumentation slows
fuzzing, so it's only used for replays. `L`, `F`, `H` and `P` are as in
`run.md` next to this file.

## CLI targets

1. **Replay** the corpus with coverage:
   <!-- ci: js-cli-cov-replay -->
   ```sh
   $L start --id cov-$H-<n> --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- \
     "$P/node_modules/.bin/jazzer" "$P/<target_file>" "$F/corpus/$H" \
     --timeout=<input_timeout_ms> --mode=regression --coverage --coverageReporters=lcov \
     --coverageDirectory="{run}/raw" -- -rss_limit_mb=<memory_mb> -artifact_prefix="{run}/artifacts/copy-{copy}/"
   ```
   - `--mode=regression` runs every input once. More corpus directories go
     after the first.
   - **A finding stops it** with a partial report. A timeout or a native
     abort loses the report. Either way, the finding file names the
     input, and the finding is a blocking bug. Replay a copy of the
     corpus without that input, and mark the snapshot partial until the
     bug is fixed.
2. **Export** (reads files only). Istanbul writes `SF:` paths relative to
   the replay's working directory, the copy's scratch. Resolve them
   against that directory, and make them relative to the repository
   root. Paths relative to the root still work after the scratch is
   deleted:
   <!-- ci: js-cov-export -->
   ```sh
   R="$F/runs/cov-$H-<n>"; mkdir -p "$F/coverage/$H"
   python3 "$F/bin/coverage.py" merge "$R/raw/lcov.info" --sf-base "$R/copy-0/work" \
     --root "$F/../.." -o "$F/coverage/$H/r<round>.lcov"
   ```
   - `gaps` lists some functions as `(anonymous_<n>)`, with their start
     line. To show such a function, pass `coverage.py show` its lines,
     `--lines <first>-<last>`, instead of `--function`. Istanbul gives
     that name to class methods, object methods and arrow functions.

**Exact subset** (`reach.py`, the gate's state-leak check): to run
exactly the inputs in `{files}`, use step 1's command with `{files}`
instead of the corpus directories.

**Inputs that reach a line**: `reach.py` finds them, for targeted inputs
(`fuzz-coverage`'s corpus.md):

<!-- ci: js-cli-reach -->
```sh
python3 "$F/bin/reach.py" --harness $H --corpus "$F/corpus/$H" --file <file> --line <line> \
  --env PATH="$RUN_PATH" --timeout <timeout_s> \
  --replay "$P/node_modules/.bin/jazzer $P/<target_file> {files} --timeout=<input_timeout_ms> --mode=regression --coverage --coverageReporters=lcov --coverageDirectory={out} -- -rss_limit_mb=<memory_mb>" \
  --export "python3 $F/bin/coverage.py merge {out}/lcov.info --sf-base {scratch} --root $F/../.. -o {lcov}"
```

**The gate's state-leak check** is the block below, for `fuzz-harness`'s
gate.md, "No state leaking between inputs". It replays a sample in one
process, then each input of the sample in a fresh process:

<!-- ci: js-cli-leak-check -->
```sh
G="$F/runs/gate-$H-<n>"; S="$G/sample"; mkdir -p "$S" "$F/coverage/$H"
find "$F/corpus/$H" -type f -size +0c | sort -R | awk 'NR <= <sample_size>' | while IFS= read -r f; do i=$((i+1)); cp "$f" "$S/$i-${f##*/}"; done
for side in one fresh; do
  $L start --id "gate-$H-<n>-$side" --harness $H --timeout <timeout_s> --env PATH="$RUN_PATH" -- sh -c '
      jazzer=$1 target=$2 run=$3 side=$4; shift 4; i=0
      j() { "$jazzer" "$target" "$@" --timeout=<input_timeout_ms> --mode=regression --coverage \
        --coverageReporters=lcov --coverageDirectory="$run/raw-$i" -- -rss_limit_mb=<memory_mb>; }
      if [ "$side" = one ]; then j "$@"; exit; fi
      for f; do i=$((i+1)); j "$f" || exit; done' sh "$P/node_modules/.bin/jazzer" "$P/<target_file>" "{run}" "$side" "$S"/* > /dev/null
  R="$F/runs/gate-$H-<n>-$side"
  python3 "$F/bin/coverage.py" merge "$R"/raw-*/lcov.info --sf-base "$R/copy-0/work" --root "$F/../.." \
    -o "$F/coverage/$H/gate-$side.lcov"
done
python3 "$F/bin/coverage.py" overlap "$F/coverage/$H/gate-one.lcov" "$F/coverage/$H/gate-fresh.lcov" --counts \
  --processes $(ls "$S" | wc -l) --root .
```

## TypeScript

lcov names the compiled files (`.seclab/fuzz/build/ts/src/<file>.js`,
`build.md`), even with source maps. Scope files, sinks and `reach.py`
use those paths and their line numbers. The target file's reasons name
the `.ts` files.
