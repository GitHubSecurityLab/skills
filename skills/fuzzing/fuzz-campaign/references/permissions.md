# Agent permissions

The following worktrees and builds go to
`<checkout>/../<repo>.seclab-worktrees/`, outside the checkout, so the
project's own tools don't see a second copy (long-runs.md, "Which tree"):
- builds for long runs;
- the `base` build;
- builds of C/C++ projects that build only in-tree;
- upstreaming's worktrees and their builds;
- `fuzz-triage`'s freshness and bisect worktrees.

Copilot CLI and Claude Code ask before every write outside the directory
they started in; Codex's sandbox refuses such writes. Autonomous mode
needs no extra command: its start command grants everything
(autonomous.md).

A skill can't grant access, so in interactive mode, show the user the
command for their agent once. Show it on the setup card, or in the first
message of a request that skips Setup and writes there: a single-step
request, or a skill used on its own. The command creates the directory
and restarts the agent in the checkout with access to it, so showing it
first loses no work. Fill in the checkout's directory name for `<repo>`.

| Agent | Command, run in the checkout |
|---|---|
| GitHub Copilot CLI | `mkdir -p ../<repo>.seclab-worktrees && copilot --add-dir ../<repo>.seclab-worktrees` |
| Claude Code | `mkdir -p ../<repo>.seclab-worktrees && claude --add-dir ../<repo>.seclab-worktrees` |
| OpenAI Codex | `mkdir -p ../<repo>.seclab-worktrees && codex --sandbox workspace-write --ask-for-approval on-request -c sandbox_workspace_write.network_access=true --add-dir ../<repo>.seclab-worktrees` |

For Codex, also say:
- For Java and JS projects, add `--add-dir ~/.m2 --add-dir ~/.gradle
  --add-dir ~/.npm`. Maven, Gradle and npm keep their caches there,
  outside the sandbox. The directories needn't exist yet.
- Network access lets installs and builds download dependencies.
- The sandbox keeps `.git` read-only, so Codex still asks before each
  commit, branch and worktree.
