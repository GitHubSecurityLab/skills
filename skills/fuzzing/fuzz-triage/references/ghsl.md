# GitHub Security Lab report format

Use this format when the user asks for it (`report_format: ghsl`), e.g. for
reports sent on behalf of GitHub Security Lab.

GitHub Security Lab publishes its report template at
https://github.com/github/securitylab/blob/main/docs/report-template.md
(MIT). Follow its structure and fill it from the same facts as the advisory
draft:

- **Summary:** the problem and its impact in a few sentences.
- **Product** and **Tested Version:** project, version or commit tested.
- **Details:** the root cause with links to the code. For several issues,
  one subsection per issue with its own identifier, each with impact, CWEs
  and resources (proof of concept).
- **PoC:** the harness-independent reproducer, with complete instructions
  to build it (with sanitizers) and run it; the fuzzer input and harness
  only for reference.
- **Impact.**
- **Remediation:** the tested fix, marked as a suggestion.
- **GitHub Security Advisories:** ask the maintainers to open a private
  advisory, so the fix can be reviewed together and a CVE requested.
- **Credit**, **Contact**, and **Disclosure Policy** (GitHub Security Lab's
  policy: https://securitylab.github.com/advisories/#policy).

The coordinated disclosure timeline is added at publication, not in the
draft.

Identifiers such as `GHSL-YYYY-NNN` are assigned by GitHub Security Lab;
leave a placeholder.
