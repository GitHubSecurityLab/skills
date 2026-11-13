# Upstreaming

Upstreaming turns the campaign's harnesses, seeds and build changes into
contributions maintainers can merge: in the project's own style, on clean
branches from upstream, with nothing the hold-back rule covers before
it's public and fixed upstream.
The agent prepares everything; the user opens the pull requests.

## When to use

- "Upstream my harnesses", "add fuzzing to CI", "turn these crashes into
  regression tests".

Not for reporting vulnerabilities (`fuzz-triage`).

## Reads

- `CAMPAIGN.md`: Harnesses, Open crashes. `crashes/*.md`: verdicts,
  Disclosure, Fix. `targets/*.md`: Harness, build variants.
- The project's CONTRIBUTING, `SECURITY.md`, any AI-assistance policy, and
  commit conventions (`git log --oneline -20`).
- The language skill: regression mode and where harnesses live.

## Steps

1. **Inventory and hold-back.** List harnesses, seeds, dictionaries, build
   changes, fuzzing-mode patches, regression tests and fixes. For every
   finding the hold-back rule covers (`SKILL.md`, Safety rules), hold back
   until it's public (Disclosure `public`) and fixed upstream:
   - a harness that reaches it (OSS-Fuzz or the project's CI would
     rediscover it in public);
   - seeds and regression tests that trigger it;
   - its `fuzz(fix)` commits.

   Every harness that reaches a held-back bug counts, not only the one
   that found it: list each held-back bucket's root-cause line (in the
   source the harnesses' last lcov was recorded on: after a local fix,
   the fixed line) in `.seclab/fuzz/upstreaming/held-back.tsv` (columns `id`,
   `file`, `line`), and check every harness's last lcov:
   <!-- ci: upstreaming-held-back -->
   ```sh
   python3 .seclab/fuzz/bin/coverage.py sites .seclab/fuzz/coverage/<h>/<last>.lcov --root . \
     --sites .seclab/fuzz/upstreaming/held-back.tsv
   ```
   Any state but `not reached` holds that harness back.
   - If `sites` prints `unknown (no line data)`: list a nearby line with
     data instead.
   - If it prints `unknown (file not in coverage)`, common for a
     dependency's file (JS coverage leaves out `node_modules`, Java's
     lists only the classes given): the harness is released only with
     coverage that includes the file, or a recorded reason the harness
     can't load it.

   Record what is held back and the condition for releasing it.
2. **Regression tests** for every bug that isn't held back: the
   test made from each bug's reproducer (`fuzz-triage`'s fix, in its `fuzz(fix)`
   commit), in the project's test framework. Never add crash inputs to
   regression directories: after an input-layout change they may test
   nothing. The harnesses' seed replays (the language skill's regression.md,
   "Seed replay: the harness as a test") stay, as checks that the
   harnesses still run.
   - If the project fixed the bug its own way: check that the test passes
     with that fix.
3. **Choose the destination:** the project's own repository, or, if the
   project keeps its harnesses in OSS-Fuzz instead, its folder in
   `google/oss-fuzz`.
   - Java and JS/TS fuzz tests in the project's repository also run as
     regression tests in its CI.
   - OSS-Fuzz builds FuzzTest harnesses with Bazel; a CMake project needs
     libavif's patched compatibility-mode build (`fuzz-c-cpp`'s
     fuzztest.md): say so.
4. **Build upstreaming branches** from current upstream HEAD (the `upstream`
   remote in a fork's clone, else `origin`), each in its own worktree: the
   checkout and its builds stay on the campaign branch.
   ```sh
   R=origin; git remote get-url upstream > /dev/null 2>&1 && R=upstream
   git fetch "$R" && git remote set-head "$R" -a > /dev/null
   W="<checkout>/../<repo>.seclab-worktrees/upstream-<topic>"
   git worktree add -b seclab/upstream-<topic> "$W" "$R/HEAD"
   git -C "$W" cherry-pick <fuzz(harness) and fuzz(build) commits>
   ```
   Add fuzzing-mode patches only if the maintainers want them. Reword
   commits to the project's conventions (and `git commit -s` if it requires
   DCO sign-off; mention a CLA if it requires one). Then give the worktree a
   workspace of its own, from the checkout: the gate runs there on its
   builds, without our fixes, and its runs and snapshots stay apart from
   the campaign's. Its `env.sh` points `F`, and JS's `P`, into the worktree;
   `P` is resolved through symlinks, as `init.py` and the agent may spell the
   checkout's path differently:
   <!-- ci: upstreaming-workspace -->
   ```sh
   W="<checkout>/../<repo>.seclab-worktrees/upstream-<topic>"; t=$(cd "$W" && pwd); T="$t/.seclab/fuzz"
   mkdir -p "$T/build" && for d in bin targets dict leaks corpus build/jacoco; do [ ! -d "$F/$d" ] || cp -R "$F/$d" "$T/$(dirname "$d")/"; done
   p=$([ -z "${P-}" ] || { r=$(cd "$P" && pwd -P); echo "${r#"$(pwd -P)"}"; })
   sed -e "s|^F=.*|F=\"$T\"|" -e "s|^P=.*|P=\"$t$p\"|" "$F/env.sh" > "$T/env.sh"
   ```
   Jest: also copy the package's `.cifuzz-corpus` there. Every command for
   the worktree then starts with `cd "<worktree>" && . .seclab/fuzz/env.sh;`
   (`<worktree>`: `W` above): rebuild there (`fuzz-harness`'s build.md),
   rerun the gate (`fuzz-harness`'s gate.md), and record its result in the
   checkout's target files. Run the checks the project's CI
   runs on pull requests there too, its linters, formatter and
   license-header check (e.g. `pre-commit run --all-files`, `mvn verify`,
   `./gradlew check`, `npm run lint`), and fix what they flag in our
   files. Each bug that isn't held back and isn't fixed upstream gets its
   own branch the same way, `seclab/upstream-fix-<bucket>`, with its
   `fuzz(fix)` commit and regression test. Remove a worktree once its
   branch is ready and `git -C "$W" status --porcelain` prints nothing
   (nothing uncommitted or untracked), with any `upstream-<topic>-build/`
   worktrees (long-runs.md, "Cleanup"); the branch stays.
   - If the gate crashes: bucket the crash, from the checkout, together
     with the held-back buckets' reproduction logs (`crash_buckets.py`, as
     in `fuzz-triage`, with the worktree as a `--root` too) before writing
     any PR text. In a held-back bucket, the harness and its seeds are
     held back too; in no known bucket, it's a new finding
     to triage first (held back while unresolved); in another known
     bucket, say so in the PR text.
5. **OSS-Fuzz changes,** if that's the destination: update the project's
   folder (`project.yaml`, `Dockerfile`, `build.sh`) in a clone of
   `google/oss-fuzz`, and validate with
   `python3 infra/helper.py build_fuzzers <project>` and
   `python3 infra/helper.py check_build <project>`. These need Docker; if it
   isn't available, give the user the commands.
6. **CI fuzzing job (offer it)** from the templates in ci.md next to this
   file: on pull requests, a blocking replay of the corpus and a short
   non-blocking fuzz run that uploads its findings.
   - If the repository is public: the offer and the PR text carry ci.md's
     visibility warning, and a scheduled discovery run is added only if
     the maintainers choose it.
7. **Pull request texts** in `.seclab/fuzz/upstreaming/<name>.md` for the
   branch `seclab/<name>`: what is added, how to build and run it, the
   coverage it reaches, and how to turn a new finding into a regression
   test (a small test that calls the API with the values from the input).
   Nothing about held-back bugs.
   Follow the project's AI-assistance policy; include the removable
   disclosure line unless the policy says otherwise.
8. **Summary for the user:** branches ready to push, their PR texts, held-back
   items and when they can be released, the branches that must not be
   pushed (the hold-back rule: `git branch --list 'seclab/held-back/*'`
   and the campaign's branches), and the next steps (the user pushes and
   opens the pull requests).

## Done when

- Every harness is either on an upstreaming branch that builds and passes the
  gate and the project's checks, or recorded as held back with a release
  condition.
- PR texts and, if accepted, the CI job and OSS-Fuzz changes are prepared.

## Pitfalls and shortcuts to reject

- Opening pull requests or pushing branches. The user does that.
- Cherry-picking a held-back bug's fix, regression test or seeds into any
  upstreaming branch.
- Contributing harnesses in our own style instead of the project's.
- Adding the campaign's `.seclab/` files, corpora or logs to a branch.

## Writes

- `seclab/upstream-*` branches (local), `.seclab/fuzz/upstreaming/*.md`.
- `CAMPAIGN.md`: held-back list in Next actions.
