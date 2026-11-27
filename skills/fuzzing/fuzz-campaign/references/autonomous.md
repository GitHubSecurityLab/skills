# Autonomous mode

For an unattended campaign, the user starts the agent with full
permissions: a skill can't grant them. Explain this, and show the start
command for the user's agent. Warn once that the fuzzed code, its build
scripts and its tests will run without further confirmation.

| Agent | Start command | Notes |
|---|---|---|
| GitHub Copilot CLI | `copilot --yolo --autopilot --max-autopilot-continues 1000 --deny-tool 'shell(git push)' --deny-tool 'shell(gh issue)' --deny-tool 'shell(gh pr)' --deny-tool 'shell(gh gist)'` | `--yolo` allows all tools, paths and URLs, except the denied ones. Autopilot continues on its own; some versions stop after 5 continuations by default, so set a high limit. |
| Claude Code | `claude --dangerously-skip-permissions --disallowedTools 'Bash(git push *)' 'Bash(gh issue *)' 'Bash(gh pr *)' 'Bash(gh gist *)'` | Skips permission prompts, except for the denied commands. |
| OpenAI Codex | `codex --dangerously-bypass-approvals-and-sandbox` (alias `--yolo`) | Skips approvals and the sandbox; has no deny rules. |

Recommend running autonomous campaigns in a codespace or VM, never on a
machine with credentials that matter. The deny rules only guard against
accidents, since they match the command's text. Without push
credentials, nothing can be pushed.

## Behaviour

- Take the top candidate at every choice and log it in `decisions.md`.
- The stopping rule comes from the request: "autonomously, 3 targets"
  means the top 3, and "for 4 hours" means 4 hours of develop. If the
  request gives none, develop gets the default 8 hours (`SKILL.md`, the
  stopping rule). Record the rule in Settings (`stop_rule`).
- At the start, the first-build notice becomes a warning that also
  states the stopping rule.
- Before stopping, check every picked target against the Done when
  sections of `fuzz-harness` and `fuzz-coverage`, and the target's
  findings against `fuzz-triage`'s Done when. For a missing item, do the
  step behind it, or record why it can't be done.
- The safety rules (`SKILL.md`) still apply.
- Never wait for input. When a decision truly needs the user (for example,
  no safe fix exists), record it as `needs-decision` and move on.
- Long runs: when develop is done, give the long-run commands and stop.
  The status stays `running`: only the user ends a campaign. Never poll
  or sleep for the runs; the user runs them and asks for the analysis.
- Keep tool output short (tails, summaries) to save context.
