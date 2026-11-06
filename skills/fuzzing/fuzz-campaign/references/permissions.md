# Agent permissions

Builds for long runs, the `base` build, and develop's builds of C/C++
projects that build only in-tree go to
`<checkout>/../<repo>.seclab-worktrees/`, outside the checkout, so the
project's own tools don't see a second copy (long-runs.md, "Which tree").
Copilot CLI and Claude Code ask before every write outside the directory
they started in; Codex's sandbox refuses it. A skill can't grant access,
so in interactive mode, show the user once, on the setup card, the command
for their agent: it creates the directory and restarts the agent in the
checkout with access to it. Fill in the checkout's directory name for
`<repo>`. Autonomous mode needs no extra command: its start command
grants everything (autonomous.md).

| Agent | Command, run in the checkout |
|---|---|
| GitHub Copilot CLI | `mkdir -p ../<repo>.seclab-worktrees && copilot --add-dir ../<repo>.seclab-worktrees` |
| Claude Code | `mkdir -p ../<repo>.seclab-worktrees && claude --add-dir ../<repo>.seclab-worktrees` |
| OpenAI Codex | `mkdir -p ../<repo>.seclab-worktrees && codex --sandbox workspace-write --ask-for-approval on-request -c sandbox_workspace_write.network_access=true --add-dir ../<repo>.seclab-worktrees` |

For Codex, also say:
- For Java and JS projects, add `--add-dir ~/.m2 --add-dir ~/.gradle
  --add-dir ~/.npm` (they needn't exist yet): Maven, Gradle and npm keep
  their caches there, outside the sandbox.
- Network access lets installs and builds download dependencies.
- The sandbox keeps `.git` read-only, so Codex still asks before each
  commit, branch and worktree.
