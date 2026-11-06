# CI fuzzing job templates

Offer one workflow for pull requests: a **blocking** replay of each
harness's corpus (a change that crashes a committed input is a
regression), then a short **non-blocking** fuzz run: a new finding may be
an old bug the pull request didn't cause, so it's uploaded, not failed on.
No corpus is kept between runs. A scheduled discovery run (the same with
a longer fuzz run) comes only as the maintainers' choice: see Public
repositories below.

Reproducers made into regression tests run in the project's normal test
job. Upload every artifact kind the engine writes (crashes, timeouts, OOMs,
leaks), kept for a few days only (`retention-days`). Pin actions to commit
SHAs if the project does so.

## Public repositories

A public repository's workflow runs are public: the uploaded artifacts and
the job logs (libFuzzer and Jazzer print small crashing inputs as
`Base64:`) show every bug the fuzz step finds, before anyone triages it.
Say so in the offer and in the PR text that adds the workflow:
- **Default:** the pull-request jobs only. Their short runs rarely find a
  new bug, and the uploads expire after 7 days.
- **Scheduled discovery** only when the maintainers choose it after
  reading this: uncomment the `schedule:` trigger. Its findings are public
  until they expire. For continuous fuzzing with private reports, point
  them to OSS-Fuzz (CIFuzz below); ClusterFuzzLite keeps a corpus between
  runs, but on a public repository its runs are public too.

## C/C++ project in OSS-Fuzz: CIFuzz

OSS-Fuzz documents CIFuzz for integrated projects
(https://google.github.io/oss-fuzz/getting-started/continuous-integration/).
Copy the current example from that page and set the project name.
Projects outside OSS-Fuzz can use ClusterFuzzLite
(https://google.github.io/clusterfuzzlite/), which also keeps a corpus
between runs, instead of the template below.

## C/C++ with libFuzzer

```yaml
name: Fuzzing
on:
  pull_request:
  # Scheduled discovery, only if the maintainers choose it (Public repositories):
  # schedule:
  #   - cron: '0 3 * * 1'
permissions:
  contents: read
jobs:
  fuzz:
    runs-on: ubuntu-latest
    env:
      CC: clang
      CXX: clang++
      SECONDS_PER_HARNESS: ${{ github.event_name == 'schedule' && 1800 || 120 }}
    steps:
      - uses: actions/checkout@v7
      - name: Sanitizer flags
        # Sanitizers, libFuzzer's coverage instrumentation and the fuzzing macro for
        # the library, with the campaign's UBSan list; link the harnesses with
        # -fsanitize=fuzzer. C turns `function` off, last.
        run: |
          UB=array-bounds,bool,builtin,enum,function,integer-divide-by-zero,null,object-size,return,returns-nonnull-attribute,shift,signed-integer-overflow,unreachable,vla-bound,vptr
          HARDEN="-D_GLIBCXX_ASSERTIONS -D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_DEBUG"
          FLAGS="-g -O1 -fno-omit-frame-pointer -fsanitize=fuzzer-no-link,address,$UB -fno-sanitize-recover=$UB $HARDEN -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION"
          echo "CFLAGS=$FLAGS -fno-sanitize=function" >> "$GITHUB_ENV"
          echo "CXXFLAGS=$FLAGS" >> "$GITHUB_ENV"
      - name: Build fuzzers
        # The empty Release flags keep a forced Release build type from adding -O3 -DNDEBUG.
        run: <the project's fuzzing build with these flags, e.g. cmake -B build-fuzz -DCMAKE_C_FLAGS_RELEASE= -DCMAKE_CXX_FLAGS_RELEASE= -D<PROJECT>_BUILD_FUZZERS=ON && cmake --build build-fuzz>
      - name: Replay the corpus
        run: |
          for f in build-fuzz/fuzz/*_fuzzer; do
            h=$(basename "$f"); mkdir -p "fuzz/corpus/$h"
            "$f" -runs=0 -timeout=10 -rss_limit_mb=2048 "fuzz/corpus/$h"
          done
      - name: Fuzz
        continue-on-error: true
        run: |
          mkdir -p fuzz-artifacts; status=0
          for f in build-fuzz/fuzz/*_fuzzer; do
            h=$(basename "$f"); mkdir -p "new-corpus/$h"
            "$f" -max_total_time=$SECONDS_PER_HARNESS -timeout=10 -rss_limit_mb=2048 \
              -artifact_prefix="fuzz-artifacts/$h-" "new-corpus/$h" "fuzz/corpus/$h" || status=1
          done
          exit $status
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: fuzz-artifacts
          path: fuzz-artifacts/
          if-no-files-found: ignore
          retention-days: 7
```

`new-corpus/` gets the inputs the run adds; commit useful ones to the
corpus by hand.

## Java with Jazzer (JUnit `@FuzzTest`)

Steps for the C/C++ template's job, after its checkout. The project's
`mvn test` already replays each fuzz test's inputs directory (regression
mode, `fuzz-java`'s regression.md), which is the blocking part. The fuzz
step runs Jazzer's standalone driver on the test classes; it adds the
fuzz test's inputs directory and seed sources to the corpus itself.
Unlike `JAZZER_FUZZ=1 mvn test`, it takes the per-input
timeout and heap cap, and writes every finding (`crash-*`, `timeout-*`)
to one directory. A JUnit timeout setting isn't a per-input timeout:
JUnit fails the whole fuzzing run once it's longer.

```yaml
      - uses: actions/setup-java@v6
        with: { distribution: temurin, java-version: '21' }
      - name: Build the fuzz classpath
        # Multi-module: from the root, with -pl <module> -am, and that module's target/ below.
        run: mvn -B -q test-compile dependency:build-classpath -Dmdep.includeScope=test -Dmdep.outputFile=target/fuzz.deps
      - name: Fuzz
        continue-on-error: true
        run: |
          mkdir -p fuzz-artifacts new-corpus
          java -Xmx2048m -XX:-OmitStackTraceInFastThrow -cp "target/test-classes:target/classes:$(cat target/fuzz.deps)" \
            com.code_intelligence.jazzer.Jazzer --target_class=<package>.<FuzzTestClass> --target_method=<method> \
            -max_total_time=${{ github.event_name == 'schedule' && 1800 || 120 }} -timeout=10 -rss_limit_mb=0 \
            -artifact_prefix=fuzz-artifacts/ new-corpus
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: fuzz-artifacts
          path: fuzz-artifacts/
          if-no-files-found: ignore
          retention-days: 7
```

One fuzz test per step (or matrix entry); inputs worth keeping from
`new-corpus/` go into its inputs directory by hand. `-rss_limit_mb=0`
because the JVM's own footprint would trip libFuzzer's limit; `-Xmx` caps
the heap.
Gradle: the test runtime classpath from the init script in `fuzz-java`
(build.md) instead of the Maven step.

## JS/TS with Jazzer.js (CLI targets)

Steps for the C/C++ template's job, after its checkout, one target per
step. The blocking replay runs Jazzer.js, so its bug detectors apply; the
replay test (`fuzz-js`'s regression.md) runs without them in the
project's normal test job.

```yaml
      - uses: actions/setup-node@v7
        with: { node-version: 24 }
      - name: Install
        # Without a lockfile, npm install; pnpm and Yarn: their frozen-lockfile install.
        run: npm ci
      - name: Build
        run: <the project's TypeScript build, e.g. npm run build, which the targets load (none for JavaScript)>
      - name: Replay the corpus
        run: |
          mkdir -p fuzz/corpus/<thing>
          npx jazzer fuzz/<thing>.fuzz.js fuzz/corpus/<thing> --mode=regression --timeout=10000 --sync -- -rss_limit_mb=2048
      - name: Fuzz
        continue-on-error: true
        run: |
          mkdir -p fuzz-artifacts new-corpus
          npx jazzer fuzz/<thing>.fuzz.js new-corpus fuzz/corpus/<thing> --timeout=10000 --sync -- \
            -max_total_time=${{ github.event_name == 'schedule' && 1800 || 120 }} \
            -rss_limit_mb=2048 -artifact_prefix=fuzz-artifacts/
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: fuzz-artifacts
          path: fuzz-artifacts/
          if-no-files-found: ignore
          retention-days: 7
```

`--sync` for a synchronous target (`fuzz-js`'s run.md). Node 24: Node 26
deprecates the hook Jazzer.js uses for ES modules.
