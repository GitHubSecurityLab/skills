# Reproducing an OSS-Fuzz testcase

OSS-Fuzz's own build decides whether the testcase reproduces: a local
build that doesn't crash isn't decisive, since another compiler, OS or CPU
can take other code paths. OSS-Fuzz's Docker images take several GB: in
interactive mode, ask first, as for the install plan.

1. **Build at the crash revision.** In a clone of `google/oss-fuzz`
   (Docker), build with the report's sanitizer and architecture, from a
   checkout at the report's crash revision:
   `python3 infra/helper.py build_fuzzers --sanitizer <sanitizer> <project> <checkout>`.
   For i386 jobs, add `--architecture i386`.
2. **Reproduce** the testcase through the launcher, as run
   `oss-fuzz-<h>-<n>`:
   - The launcher's empty environment hides the Docker client's settings.
     Pass in the client's configuration directory, and any other
     `DOCKER_*` variable your shell sets (`env | grep '^DOCKER_'`).
   - `<oss_fuzz>` (the clone) and `<testcase>` are absolute paths, since
     runs start in their scratch directory.
   - `--architecture` is step 1's, if it had one.

   ```sh
   $L start --id oss-fuzz-<h>-<n> --harness <h> --timeout 1800 --env DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}" -- \
     python3 "<oss_fuzz>/infra/helper.py" reproduce [--architecture <arch>] <project> <fuzzer> "<testcase>"
   ```
   OSS-Fuzz's own per-input limits apply, not Settings'. The first run
   also pulls OSS-Fuzz's runner image, over 2 GB, hence the long
   `--timeout`. Add `--detach` if the agent can't wait that long
   (`fuzz-harness`'s runs.md).

   The testcase reproduced if the log shows the Crash Type and Crash
   State of OSS-Fuzz's report. The run's state is `exited 1` both when it
   reproduced and on a Docker error.

Without Docker, or if the user declines, skip both steps. `SKILL.md`,
"Reproduce", gives the State when the local build doesn't crash.
