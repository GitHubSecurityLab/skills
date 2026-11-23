# Report

A report is useful when a maintainer can confirm the bug in minutes, see
why it matters, and apply a tested fix. It must not be stale (already
fixed), already known, or sent to the wrong project. This skill never
sends anything: the user sends the report; for a vulnerability, through
the project's private channel.

## Reads

What the steps in this file read. Paths are relative to `.seclab/fuzz/`
unless they start with `.seclab/`:
- `crashes/<bucket>.md` and `crashes/<bucket>/`: verdict, root cause,
  minimized input, reproducer, regression test, fix commit and validation,
  Fixed upstream.
- `.seclab/threat-model.md`: deployment and attacker, for the impact.
- `CAMPAIGN.md`: Settings (`report_format`), base commit.

## Steps

1. **Freshness:** run fix.md's check ("Check the precondition, then
   freshness") again if upstream moved since it last ran. If it never ran,
   as for a report asked for before any fix, run it now. Here the check
   only records what it finds; it never leads to a fix or a commit. If
   upstream fixed the bug, step 5 says what the draft gives as the fix.
2. **Known-bug search** in:
   - public issues and pull requests
     (`gh search issues --repo OWNER/REPO "<function>"`, the same with
     `gh search prs`);
   - recent commits touching the function;
   - OSV:
     `curl -s -d '{"package":{"name":"<pkg>","ecosystem":"<ecosystem>"}}' https://api.osv.dev/v1/query`.
     For C/C++ libraries without a package ecosystem, query by commit:
     `curl -s -d '{"commit":"<tested sha>"}' https://api.osv.dev/v1/query`;
   - GitHub Security Advisories
     (`gh api 'advisories?ecosystem=<ecosystem>&affects=<pkg>'`).

   If the project is in OSS-Fuzz: note that OSS-Fuzz issues stay private
   until fixed, so a shallow bug may already be known to the maintainers.
3. **Ownership.** If the root cause is in a dependency, vendored or not,
   the report goes to that project; note the downstream impact on this
   one.
4. **Project rules.** Read `SECURITY.md`, CONTRIBUTING and any policy on
   AI-assisted reports or contributions, and follow them (reporting
   channel, required details, language rules). Some projects ask for
   reports written in the reporter's own words; raise such rules at the
   report checkpoint.
5. **Write the draft** to `.seclab/fuzz/reports/<id>.md`. A
   `vulnerability` gets a security report:
   - default (`report_format: advisory`): the fields of a GitHub Security
     Advisory, with the template in advisory.md next to this file;
   - `report_format: ghsl`: GitHub Security Lab's report format, see
     ghsl.md next to this file.

   Every other real bug goes into a "bugs found by fuzzing" draft for its
   project: an ordinary bug report without CVSS. A project gets at most
   two such drafts, one for the bugs sent privately and one for those sent
   in public:
   - If the hold-back rule (`fuzz-campaign`, safety rules) covers the
     bug, the report is still sent privately, through step 9's channels,
     since the attacker-path judgment can be wrong.
   - Otherwise it's sent in public, through step 9's channels.

   All drafts draw on the same facts. Keep each one short and factual:
   what, where, how to reproduce, impact, fix. No long generated prose.
   Include the removable disclosure line, e.g. "Found with AI-assisted
   fuzzing; reproduced and reviewed by <name>." The draft's fix:
   - A fix of ours that the user hasn't reviewed is labeled "mechanically
     validated; semantic review pending".
   - If the bucket is Fixed upstream, the fix is upstream's commit, not a
     patch of ours, and gets no review label. Give that commit where the
     draft has the fix (advisory.md: Suggested fix; ghsl.md:
     Remediation), with the fix tested by the reproducer failing on the
     tested commit and passing on HEAD. Give the first release with the
     fix as the patched version, or "none yet". If no release has the
     fix, ask the maintainers to release it. In a security report, ask
     them to publish an advisory.
   - If the bug has no fix of ours and isn't Fixed upstream, the draft's
     fix is one of these:
     - "Suggested fix: none" and the reason, when fix.md found no safe
       fix;
     - otherwise "Suggested fix: not attempted", as for a report asked
       for before any fix.
6. **Severity** (vulnerabilities). Start from the crash type's impact in
   `verdicts.md`. Then write a CVSS 3.1 vector based on the threat
   model's deployment (attack vector, privileges, user interaction), and
   justify each choice in one line. The score follows from the vector
   (FIRST calculator: https://www.first.org/cvss/calculator/3.1).
   - If the project uses CVSS 4.0, use it; GitHub advisories also accept
     it.
7. **Reproducer package** in `crashes/<bucket>/`, referenced from the
   draft. It holds, in this order:
   - the harness-independent reproducer and its output. A maintainer can
     build and run the reproducer against the tested commit without our
     harness. For C/C++, it comes with a short build script that builds
     the library and the reproducer with the campaign's sanitizer flags
     (`fuzz-c-cpp`'s regression.md);
   - the tested commit;
   - the harness and how to build it;
   - the crashing input, as a file and as hex or base64.
8. **Report checkpoint** in interactive mode. Show the user:
   - the draft;
   - the patch with the review's answers (fix.md, "Review what the patch
     means");
   - the open points, such as fixed upstream but unreleased, project
     rules and severity choices;
   - the next steps.

   Once the user has reviewed the patch, drop the draft's review label
   and record the review in the bucket's Fix section.
   - If the mode is autonomous: list these items in the end-of-campaign
     summary instead; the label stays.
   - If the draft's fix is "not attempted": offer the fix ("fix this
     crash", fix.md), then update the draft.
9. **Next steps for the user** (in the summary, not in the draft). For a
   vulnerability, or another bug sent privately (step 5):
   - For maintainers of the project: create a draft repository security
     advisory
     (https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/fix-reported-vulnerabilities/create-repository-advisory).
     Develop the fix in the advisory's temporary private fork
     (https://docs.github.com/en/code-security/tutorials/fix-reported-vulnerabilities/collaborate-in-a-fork),
     not in the public repository. The `fuzz(fix)` commit from the
     `seclab/held-back/<bucket>` branch goes there. A bug fixed upstream
     needs no fork: its fix is already public. Release the fix and
     publish the advisory. The held-back harnesses and tests land after
     that (`fuzz-campaign`'s upstreaming.md).
   - For everyone else: report through the project's private
     vulnerability reporting
     (https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately)
     or the `SECURITY.md` contact. Follow GitHub Security Lab's
     coordinated disclosure policy
     (https://securitylab.github.com/advisories/#policy) as a reference.

   For a bug sent in public: the user files its draft as an issue or pull
   request, following the project's rules. Each fix of ours goes upstream
   on its own branch, which `fuzz-campaign`'s upstreaming.md prepares.

## Done when

- Freshness, known-bug search and ownership are checked and recorded.
- The draft exists, with reproducer package and fix. The fix is one of
  these: ours, upstream's commit, "Suggested fix: not attempted", or
  "Suggested fix: none" and the reason.
- A vulnerability's draft is in the chosen format, with severity.
- The user has the next steps.

## Pitfalls and shortcuts to reject

- Skipping the freshness check.
- Inflating severity. Justify each CVSS metric from the threat model.
- Long, generic text.
- Sending, posting or filing anything. The user does that.
- Putting a held-back bug's regression test or fix (`fuzz-campaign`,
  safety rules) into a public pull request before the bug is public and
  fixed upstream.

## Writes

What the steps in this file write or change. Paths are relative to
`.seclab/fuzz/` unless they start with `.seclab/`:
- `reports/<id>.md`, the reproducer package in `crashes/<bucket>/`.
- `crashes/<bucket>.md`: Report, Disclosure. `CAMPAIGN.md`: Open crashes
  (Report `drafted`).
