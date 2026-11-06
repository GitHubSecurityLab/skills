# Report

A report is useful when a maintainer can confirm the bug in minutes, see why
it matters, and apply a tested fix. It must not be stale (already fixed),
already known, or sent to the wrong project. This skill never sends
anything: the user reports it, through the project's private channel for
a vulnerability.

## When to use

- After the fix (`fuzz-triage`; or after triage when no safe fix exists) for buckets with
  a real-bug verdict.
- "Write an advisory/report for this crash."

Not for triage or the fix itself (`SKILL.md`, fix.md).

## Reads

- `crashes/<bucket>.md` and `crashes/<bucket>/`: verdict, root cause,
  minimized input, reproducer, regression test, fix commit and validation.
- `.seclab/threat-model.md`: deployment and attacker, for the impact.
- `CAMPAIGN.md`: Settings (`report_format`), base commit.

## Steps

1. **Freshness,** before any fix work, and again here if upstream moved
   since. Run the bug's reproducer (`fuzz-triage`; the language
   skill's regression.md, "The bug's reproducer") on current upstream HEAD and on the
   latest release, each in a worktree next to the
   campaign's others, built with what the reproducer needs
   (`fuzz-harness`'s build.md and runs.md). The reproducer doesn't need
   our harness, which these trees don't have. Take the latest release
   from release metadata
   (`gh release list --limit 5 --json tagName,publishedAt` covers every
   branch; or the project's changelog, or tags sorted by date):
   `git describe` on HEAD misses releases cut from maintenance branches.
   The project's remote is `upstream` in a fork's clone, else `origin`:
   <!-- ci: fresh-worktrees -->
   ```sh
   R=origin; git remote get-url upstream > /dev/null 2>&1 && R=upstream
   git fetch --tags "$R" && git remote set-head "$R" -a > /dev/null
   W="<checkout>/../<repo>.seclab-worktrees"
   [ -d "$W/fresh-head" ] || git worktree add --detach "$W/fresh-head" "$R/HEAD"
   git -C "$W/fresh-head" checkout -q --detach "$R/HEAD"
   [ -d "$W/fresh-release" ] || git worktree add --detach "$W/fresh-release" <release_tag>
   git -C "$W/fresh-release" checkout -q --detach <release_tag>
   ```
   Later reports reuse both worktrees at the new commits. Record the
   affected versions.
   - If the reproducer passes on HEAD: find the fixing commit (`git log`
     on the files involved) and record it (Fixed upstream). The verdict
     stays: being fixed isn't being public, so a held-back bug's report
     and hold-back still apply (`fuzz-campaign`, safety rules). If the
     latest release is still affected, raise "fixed upstream, not yet
     released" at the report checkpoint; it may be a silent security fix
     that users don't have yet.
2. **Known-bug search** in:
   - public issues and pull requests
     (`gh search issues --repo OWNER/REPO "<function>"`, the same with
     `gh search prs`);
   - recent commits touching the function;
   - OSV
     (`curl -s -d '{"package":{"name":"<pkg>","ecosystem":"<ecosystem>"}}' https://api.osv.dev/v1/query`;
     for C/C++ libraries without a package ecosystem, query by commit:
     `curl -s -d '{"commit":"<tested sha>"}' https://api.osv.dev/v1/query`);
   - GitHub Security Advisories
     (`gh api 'advisories?ecosystem=<ecosystem>&affects=<pkg>'`).

   If the project is in OSS-Fuzz: note that OSS-Fuzz issues stay private
   until fixed, so a shallow bug may already be known to the maintainers.
3. **Ownership.** If the root cause is in a dependency (vendored or not), the
   report goes to that project; note the downstream impact on this one.
4. **Project rules.** Read `SECURITY.md`, CONTRIBUTING and any policy on
   AI-assisted reports or contributions, and follow them (reporting channel,
   required details, language rules). Some projects ask for reports written
   in the reporter's own words; raise such rules at the report checkpoint.
5. **Write the draft** to `.seclab/fuzz/reports/<id>.md`. A
   `vulnerability` gets a security report:
   - default (`report_format: advisory`): the fields of a GitHub Security
     Advisory, with the template in advisory.md next to this file;
   - `report_format: ghsl`: GitHub Security Lab's report format, see
     ghsl.md next to this file.

   Every other real bug goes into one "bugs found by fuzzing" draft per
   project, an ordinary bug report without CVSS: sent privately anyway
   when the hold-back rule covers the bug (`fuzz-campaign`, safety rules;
   step 9's channels), since the attacker-path judgment can be wrong, and
   otherwise as an issue or pull request, following the project's rules.
   All drafts draw on the same facts. Keep it short and factual: what,
   where, how to reproduce, impact, fix. No long generated prose. A fix
   the user hasn't reviewed is labeled "mechanically validated; semantic
   review pending". Include the removable disclosure line, e.g. "Found
   with AI-assisted fuzzing; reproduced and reviewed by <name>."
6. **Severity** (vulnerabilities). Start from the crash type's impact
   (`verdicts.md`), then write a CVSS 3.1 vector based on the threat
   model's deployment (attack vector, privileges, user interaction), with
   each choice justified in one line. The score follows from the vector
   (FIRST calculator: https://www.first.org/cvss/calculator/3.1).
   - If the project uses CVSS 4.0: use it; GitHub advisories also accept
     it.
7. **Reproducer package** in `crashes/<bucket>/`, referenced from the draft.
   It leads with the harness-independent reproducer, which a maintainer
   can build and run against the tested commit without our harness (C/C++:
   with a short build script that builds the library and the reproducer
   with the campaign's sanitizer flags, `fuzz-c-cpp`'s regression.md), and
   its output. Then the tested commit, the harness and how to build it,
   and the crashing input as a file and as hex or base64.
8. **Report checkpoint** in interactive mode: show the draft, the patch
   with the review's answers (fix.md, "Review what the patch means"), the
   open points (fixed upstream but unreleased, project rules, severity
   choices) and the next steps. Once the user has reviewed the patch, drop the label and record
   the review in the bucket's Fix section.
   - If the mode is autonomous: list them in the end-of-campaign summary;
     the label stays.
9. **Next steps for the user** (in the summary, not in the draft):
   - maintainers of the project: create a draft repository security advisory
     (https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/fix-reported-vulnerabilities/create-repository-advisory),
     and develop the fix in its temporary private fork
     (https://docs.github.com/en/code-security/tutorials/fix-reported-vulnerabilities/collaborate-in-a-fork),
     not in the public repository: the `fuzz(fix)` commit from the
     `seclab/held-back/<bucket>` branch goes there. Release the fix and
     publish the advisory; the held-back harnesses and tests land after
     that (`fuzz-campaign`'s handoff.md);
   - everyone else: the project's private vulnerability reporting
     (https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)
     or the `SECURITY.md` contact, following GitHub Security Lab's
     coordinated disclosure policy (https://securitylab.github.com/advisories/#policy)
     as a reference.


## Done when

- Freshness, known-bug search and ownership are checked and recorded.
- The draft exists in the chosen format with severity, reproducer package and
  fix (or "Suggested fix: none" and the reason), and the user has the next
  steps.

## Pitfalls and shortcuts to reject

- Skipping the freshness check.
- Inflating severity. Justify each CVSS metric from the threat model.
- Long, generic text.
- Sending, posting or filing anything. The user does that, privately.
- Putting a held-back bug's regression test or fix (`fuzz-campaign`,
  safety rules) into a public pull request before it's public and fixed
  upstream.

## Writes

- `reports/<id>.md`, the reproducer package in `crashes/<bucket>/`.
- `crashes/<bucket>.md`: Report, Disclosure. `CAMPAIGN.md`: Open crashes
  (Report `drafted`).
