# GitHub Security Lab Skills

Agent skills from the [GitHub Security Lab](https://securitylab.github.com/)
for security research and vulnerability discovery. The goal is to distill
our experience into a form that's ready to use with the coding agent you
already have: GitHub Copilot CLI, Claude Code or OpenAI Codex.

| Topic | What the skills do |
|---|---|
| [Fuzzing](#fuzzing) | Run a whole fuzzing campaign on C/C++, Java or JavaScript/TypeScript source code, from choosing targets to reporting the bugs found. |
| [Threat modeling](#threat-modeling) | Map where untrusted data enters a project and which dangerous operations it reaches. |

## Install

### GitHub Copilot CLI

```sh
copilot plugin marketplace add GitHubSecurityLab/skills
copilot plugin install seclab-skills@seclab
copilot skill list                       # check
```

To update:

```sh
copilot plugin update seclab-skills
```

### Claude Code

```sh
claude plugin marketplace add GitHubSecurityLab/skills
claude plugin install seclab-skills@seclab
claude plugin details seclab-skills      # check
```

To update:

```sh
claude plugin update seclab-skills
```

### OpenAI Codex

```sh
codex plugin marketplace add GitHubSecurityLab/skills
codex plugin add seclab-skills@seclab
# check: run /skills in a new Codex session
```

To update:

```sh
codex plugin marketplace upgrade
```

Some models' safety guardrails flag security work such as fuzzing or
vulnerability analysis. They refuse, or stop a session partway with a
safety error. If that happens, switch to another model.

## Fuzzing

The fuzzing skills take an agent through a whole campaign:

- **Existing fuzz harnesses.** It measures what the project's fuzz
  harnesses (fuzz tests) already cover, so new work goes where they don't
  reach.
- **Targets.** It ranks entry points by how much code that handles
  untrusted input they reach, using a threat model.
- **Harnesses.** It writes new harnesses or extends existing ones in the
  project's style, and checks that each reaches its target before any
  long run.
- **Coverage analysis.** It finds out why code stays uncovered and applies
  the most valuable fix, such as seeds, a dictionary or a harness change.
- **Crashes.** It reproduces each crash, finds the root cause, tries to
  fix the bug with a regression test and drafts the report.
- **Hand-off.** It prepares the harnesses and CI integration as changes
  maintainers can merge.

The agent checks with you before key decisions, such as which targets to
fuzz or changes to the build, or runs autonomously if you ask. Each step
also works on its own, for example triaging crashes from OSS-Fuzz.

The fuzzing skills run on macOS and Linux; on Windows, use WSL2, a VM or
a [codespace](#codespace).

Ask in plain language:

| Skill | Use it for | Example requests |
|---|---|---|
| `fuzz-campaign` | A whole campaign. Start here. | "Fuzz this project", "Analyze the long runs" |
| `fuzz-harness` | Writing, building and checking one harness | "Write a fuzz test for `parse_header`" |
| `fuzz-coverage` | One harness's coverage | "Why is coverage stuck?" |
| `fuzz-triage` | Crashes: root cause, fix, report | "Triage these crashes", "Fix this crash" |
| `fuzz-c-cpp`, `fuzz-java`, `fuzz-js` | Engine references the skills above use: libFuzzer, AFL++, FuzzTest; Jazzer; Jazzer.js | |

If the agent picks another skill, name the one you want, e.g. "Use
fuzz-triage on these crashes."

For long runs, the agent hands you a command to run in a terminal; when
it ends, ask the agent to analyze it. The work goes to `.seclab/` in the
project (kept out of git) and to commits on a local `seclab/fuzz-<date>`
branch, so a later session can resume it.

To run unattended, ask to "fuzz this project autonomously", optionally
with a limit ("3 targets", "for 4 hours"; at most 8 hours by default).
The agent then shows the command to restart it with full permissions:
do this only in a codespace or VM without push credentials.

How the fuzzing skills work and why: [design document](docs/design/fuzzing-skills.md).

## Threat modeling

`security-threat-model` maps what's untrusted, where it enters and which
dangerous operations it reaches, in `.seclab/threat-model.md`. The fuzzing
skills use it to pick targets. Ask for "Threat model this repo".

## Safety

- **Use a codespace or VM for projects you don't trust.** The skills
  build the project and run its code, its build scripts and its tests.
  Fuzzed code gets only a minimal environment, so tokens in your
  environment variables (such as `GITHUB_TOKEN`) aren't passed to it. It runs
  with a time limit per input and, where the engine supports one, a
  memory limit. It isn't sandboxed, though: it runs as you and can read
  your files and use the network. Builds, installs and tests run with
  your full environment. [Details](docs/design/fuzzing-skills.md#5-safety).
- **Check the project's agent instructions.** Your agent follows the
  project's `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`
  and project skills. In a project you don't trust, read them first or
  turn them off (Copilot CLI: `--no-custom-instructions`; Codex:
  `-c project_doc_max_bytes=0`).
- **Findings stay private.** The skills never push, open issues or pull
  requests, or post anything. When a finding may be a vulnerability, its
  fix, tests and harnesses stay on local branches until the fix is
  public; every summary lists the branches not to push. This holds for
  your own project too: fix the bug in a draft security advisory's
  [private fork](https://docs.github.com/en/code-security/tutorials/fix-reported-vulnerabilities/collaborate-in-a-fork),
  publish the advisory, then ask to "upstream my harnesses".

## Codespace

Create a codespace on this repository (8 cores): its
[devcontainer](.devcontainer/) installs the skills for Copilot CLI. Clone
the projects to fuzz into `/workspaces/targets/` and run `copilot` there.
For Claude Code or Codex, install the agent, then the plugin as in
[Install](#install). Codespaces stop after 30 idle minutes by default and
live at most 12 hours, which ends any long run still going.

## Contributing

Contributions are welcome. See [CONTRIBUTING](./CONTRIBUTING.md).

## License

This project is licensed under the terms of the
[MIT](https://spdx.org/licenses/MIT.html) license. Please refer to the
[LICENSE](./LICENSE) file for the full terms.

## Maintainers

[CODEOWNERS](./CODEOWNERS)

## Support

[SUPPORT](./SUPPORT.md)
