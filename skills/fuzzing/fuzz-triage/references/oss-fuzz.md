# Reproducing an OSS-Fuzz testcase

OSS-Fuzz's own build decides whether the testcase reproduces: a local
build that doesn't crash isn't decisive, since another compiler, OS or CPU
can take other code paths. Its Docker images take several GB (in
interactive mode, ask first, as for the install plan).

1. **Build at the crash revision.** In a clone of `google/oss-fuzz`
   (Docker), build with the report's sanitizer and architecture
   (`--architecture i386` for i386 jobs) and a checkout at its crash
   revision:
   `python3 infra/helper.py build_fuzzers --sanitizer <sanitizer> <project> <checkout>`.
2. **Reproduce** it through the launcher, as run `oss-fuzz-<h>-<n>`.
   The launcher's empty environment hides the Docker client's settings:
   pass in its configuration directory, and any other `DOCKER_*` variable
   your shell sets (`env | grep '^DOCKER_'`). `<oss_fuzz>` (the clone) and
   `<testcase>` are absolute paths, since runs start in their scratch
   directory; `--architecture` is step 1's, if it had one:
   ```sh
   $L start --id oss-fuzz-<h>-<n> --harness <h> --timeout 1800 --env DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}" -- \
     python3 "<oss_fuzz>/infra/helper.py" reproduce [--architecture <arch>] <project> <fuzzer> "<testcase>"
   ```
   OSS-Fuzz's own per-input limits apply, not Settings'. The first run
   also pulls OSS-Fuzz's runner image (over 2 GB), hence the long
   `--timeout`: add `--detach` if the agent can't wait that long
   (`fuzz-harness`'s runs.md).
   It reproduced if the log shows the Crash Type and Crash State of
   OSS-Fuzz's report; the run's state is `exited 1` for that and for a
   Docker error alike.

Without Docker, or if the user declines, skip both steps; `SKILL.md`,
"Reproduce", gives the State when the local build doesn't crash.
