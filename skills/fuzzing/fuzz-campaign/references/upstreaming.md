# Upstreaming

Upstreaming turns the campaign's harnesses, seeds and build changes into
contributions maintainers can merge. They follow the project's own style
and go on clean branches from upstream. Anything the hold-back rule
covers stays out until its finding is public and fixed upstream.
The agent prepares everything; the user opens the pull requests.

## Reads

What the steps in this file read. Paths are relative to `.seclab/fuzz/`
unless they start with `.seclab/`:
- `CAMPAIGN.md`: Harnesses, Open crashes. `crashes/*.md`: verdicts,
  Disclosure, Fix, Fixed upstream. `targets/*.md`: Harness, build
  variants.
- `coverage/<h>/<last>.lcov`: each harness's last coverage.
- `runs/repro-<h>-<n>/`: the held-back buckets' reproduction logs.
- `bin/`, `targets/`, `dict/`, `leaks/`, `corpus/` and `build/jacoco/`,
  copied into each worktree's workspace, and `env.sh`, which that
  workspace's own is made from.
- The project's CONTRIBUTING and `SECURITY.md`, from its checkout rather
  than `.seclab/fuzz/`.
- Any AI-assistance policy of the project, and its commit conventions
  (`git log --oneline -20`).
- The language skill: regression mode and where harnesses live.

## Steps

1. **Inventory and hold-back.** List harnesses, seeds, dictionaries, build
   changes, fuzzing-mode patches, regression tests and fixes. For every
   finding the hold-back rule covers (`SKILL.md`, Safety rules), hold back
   the following until the finding's Disclosure is `public` and it's
   fixed upstream:
   - a harness that reaches it (OSS-Fuzz or the project's CI would
     rediscover it in public);
   - seeds and regression tests that trigger it;
   - its `fuzz(fix)` commits.

   Every harness that reaches a held-back bug is held back, not only the
   one that found it. To find these harnesses, list each held-back
   bucket's root-cause line in `.seclab/fuzz/upstreaming/held-back.tsv`
   (columns `id`, `file`, `line`). Take the line from the source that the
   harnesses' last lcov was recorded on: after a local fix, the fixed
   line. Then check every harness's last lcov:
   <!-- ci: upstreaming-held-back -->
   ```sh
   python3 .seclab/fuzz/bin/coverage.py sites .seclab/fuzz/coverage/<h>/<last>.lcov --root . \
     --sites .seclab/fuzz/upstreaming/held-back.tsv
   ```
   Any state but `not reached` holds that harness back.
   - If `sites` prints `unknown (no line data)`: list a nearby line with
     data instead.
   - If it prints `unknown (file not in coverage)`: release the harness
     only with coverage that includes the file, or with a recorded reason
     the harness can't load the file. This is common for a dependency's
     file: JS coverage leaves out `node_modules`, and Java's lists only
     the classes given.

   Record what is held back, and the condition for releasing it, in
   `CAMPAIGN.md`'s Next actions.
2. **Regression tests** for every bug that isn't held back. Each is the
   test that `fuzz-triage`'s fix made from the bug's reproducer, in the
   project's test framework; it's in the bug's `fuzz(fix)` commit.

   Never add crash inputs to a directory the project's CI replays, such
   as seeds, inputs or regression inputs. CI would fail until the fix
   lands, and after an input-layout change the inputs may test nothing.
   The harnesses' seed replays (the language skill's regression.md, "Seed
   replay: the harness as a test") stay, as checks that the harnesses
   still run.
   - If the project fixed the bug its own way: check that the regression
     test passes with that fix.
3. **Choose the destination:** the project's own repository, or the
   project's folder in `google/oss-fuzz` if the project keeps its
   harnesses in OSS-Fuzz instead.
   - Java and JS/TS fuzz tests in the project's repository also run as
     regression tests in its CI.
   - OSS-Fuzz builds FuzzTest harnesses with Bazel. A CMake project's
     FuzzTest harnesses need libavif's patched compatibility-mode build
     there (`fuzz-c-cpp`'s fuzztest.md): say so.
4. **Build upstreaming branches** from the current upstream HEAD, each in
   its own worktree, so the checkout and its builds stay on the campaign
   branch. Upstream is the `upstream` remote in a fork's clone, else
   `origin`:
   ```sh
   R=origin; git remote get-url upstream > /dev/null 2>&1 && R=upstream
   git fetch "$R" && git remote set-head "$R" -a > /dev/null
   W="<checkout>/../<repo>.seclab-worktrees/upstream-<topic>"
   git worktree add -b seclab/upstream-<topic> "$W" "$R/HEAD"
   git -C "$W" cherry-pick <fuzz(harness) and fuzz(build) commits>
   ```
   Add fuzzing-mode patches only if the maintainers want them. Reword
   commits to the project's conventions. If the project requires DCO
   sign-off, use `git commit -s`; if it requires a CLA, mention the CLA.

   Then give the worktree a workspace of its own, from the checkout.
   There, the gate runs on the worktree's builds, without our fixes, and
   the worktree's runs and snapshots stay apart from the campaign's. The
   workspace's `env.sh` points `F`, and JS's `P`, into the worktree. `P`
   is resolved through symlinks, since `init.py` and the agent may spell
   the checkout's path differently:
   <!-- ci: upstreaming-workspace -->
   ```sh
   W="<checkout>/../<repo>.seclab-worktrees/upstream-<topic>"; t=$(cd "$W" && pwd); T="$t/.seclab/fuzz"
   mkdir -p "$T/build" && for d in bin targets dict leaks corpus build/jacoco; do [ ! -d "$F/$d" ] || cp -R "$F/$d" "$T/$(dirname "$d")/"; done
   p=$([ -z "${P-}" ] || { r=$(cd "$P" && pwd -P); echo "${r#"$(pwd -P)"}"; })
   sed -e "s|^F=.*|F=\"$T\"|" -e "s|^P=.*|P=\"$t$p\"|" "$F/env.sh" > "$T/env.sh"
   ```
   Jest: also copy the package's `.cifuzz-corpus` there.

   Every command for the worktree then starts with
   `cd "<worktree>" && . .seclab/fuzz/env.sh;`, where `<worktree>` is `W`
   above. Rebuild there (`fuzz-harness`'s build.md). Rerun the gate
   (`fuzz-harness`'s gate.md), and record its result in the checkout's
   target files. Also run the project's pull-request CI checks there,
   along with its linters, formatter and license-header check (e.g.
   `pre-commit run --all-files`, `mvn verify`, `./gradlew check`,
   `npm run lint`). Fix what they flag in our files.

   Each bug that isn't held back and isn't fixed upstream gets its own
   branch, `seclab/upstream-fix-<bucket>`, built the same way. The branch
   holds the bug's `fuzz(fix)` commit and regression test.

   A worktree is done once its branch is ready and nothing in it is
   uncommitted or untracked: `git -C "$W" status --porcelain` prints
   nothing. Then remove it, with any `upstream-<topic>-build/` worktrees
   (long-runs.md, "Cleanup"); the branch stays.
   - If the gate crashes: before writing any PR text, bucket the crash
     together with the held-back buckets' reproduction logs. Run
     `crash_buckets.py` from the checkout, as in `fuzz-triage`, with the
     worktree as a `--root` too. Then:
     - If the crash is in a held-back bucket, the harness and its seeds
       are held back too.
     - If it's in no known bucket, it's a new finding: triage it first.
       It's held back while unresolved.
     - If it's in another known bucket, say so in the PR text.
5. **OSS-Fuzz changes,** if that's the destination: update the project's
   folder (`project.yaml`, `Dockerfile`, `build.sh`) in a clone of
   `google/oss-fuzz`. Validate with
   `python3 infra/helper.py build_fuzzers <project>` and
   `python3 infra/helper.py check_build <project>`. These need Docker; if
   it isn't available, give the user the commands.
6. **CI fuzzing job (offer it)** from the templates in ci.md next to this
   file. On pull requests, it runs a blocking replay of the corpus and a
   short non-blocking fuzz run that uploads its findings.
   - If the repository is public: the offer and the PR text carry ci.md's
     visibility warning. A scheduled discovery run is added only if the
     maintainers choose it.
7. **Pull request texts** in `.seclab/fuzz/upstreaming/<name>.md` for the
   branch `seclab/<name>`. Each says:
   - what is added, and how to build and run it;
   - the coverage it reaches;
   - how to turn a new finding into a regression test: a small test that
     calls the API with the values from the input.

   Say nothing about held-back bugs. Follow the project's AI-assistance
   policy; include the removable disclosure line unless the policy says
   otherwise.
8. **Summary for the user:**
   - the branches ready to push, and their PR texts;
   - held-back items, and when they can be released;
   - the branches and refs that must not be pushed, by the hold-back
     rule: `git branch --list 'seclab/held-back/*'`, the campaign's
     branches, and the archive refs of earlier campaigns
     (`git for-each-ref refs/seclab/`) whose commits reach or reveal a
     finding the rule covers;
   - the next steps: the user pushes and opens the pull requests.

## Done when

- Every harness is either on an upstreaming branch that builds and passes
  the gate and the project's checks, or recorded as held back with a
  release condition.
- The PR texts are prepared. So are the OSS-Fuzz changes if OSS-Fuzz is
  the destination, and the CI job if the user accepted the offer.

## Pitfalls and shortcuts to reject

- Opening pull requests or pushing branches. The user does that.
- Cherry-picking a held-back bug's fix, regression test or seeds into any
  upstreaming branch.
- Contributing harnesses in our own style instead of the project's.
- Adding the campaign's `.seclab/` files, corpora or logs to a branch.

## Writes

What the steps in this file write or change. Paths are relative to
`.seclab/fuzz/` unless they start with `.seclab/`:
- `seclab/upstream-*` branches (local), `upstreaming/*.md`,
  `upstreaming/held-back.tsv`.
- `targets/*.md`: Gate, from the gate runs in the worktrees.
- `CAMPAIGN.md`: held-back list in Next actions.
