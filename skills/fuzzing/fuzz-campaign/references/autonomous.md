# Autonomous mode

For an unattended campaign, the user starts the agent with full
permissions: a skill can't grant them. Explain this, show the matching
command, and warn once that the fuzzed code, its build scripts and its
tests will run without further confirmation.

| Agent | Start command | Notes |
|---|---|---|
| GitHub Copilot CLI | `copilot --yolo --autopilot --max-autopilot-continues 1000 --deny-tool 'shell(git push)' --deny-tool 'shell(gh issue)' --deny-tool 'shell(gh pr)' --deny-tool 'shell(gh gist)'` | `--yolo` allows all tools, paths and URLs, except the denied ones. Autopilot continues on its own; some versions stop after 5 continuations by default, so set a high limit. |
| Claude Code | `claude --dangerously-skip-permissions --disallowedTools 'Bash(git push *)' 'Bash(gh issue *)' 'Bash(gh pr *)' 'Bash(gh gist *)'` | Skips permission prompts, except for the denied commands. |
| OpenAI Codex | `codex --dangerously-bypass-approvals-and-sandbox` (alias `--yolo`) | Skips approvals and the sandbox; has no deny rules. |

Recommend running autonomous campaigns in a codespace or VM, never on a
machine with credentials that matter. The deny rules only guard against
accidents (they match the command's text); without push credentials,
nothing can be pushed.

## Behaviour

- Take the top candidate at every choice and log it in `decisions.md`.
- The stopping rule comes from the request ("autonomously, 3 targets": the
  top 3; "for 4 hours": 4 hours of develop), otherwise the 10% cut-off
  (`SKILL.md`, targets checkpoint) with at most 8 hours of develop. Record
  it in Settings (`stop_rule`).
- The first-build notice becomes a warning at the start, which also states
  the stopping rule.
- Develop ends by the stopping rule: a time box picks no new target once
  it's used up, and the current harness finishes its gate.
- Before stopping, check every picked target against `fuzz-harness`'s and
  `fuzz-coverage`'s Done when, and its findings against `fuzz-triage`'s: do
  the step behind a missing item, or record why it can't be done.
- The safety rules (`SKILL.md`) still apply.
- Never wait for input. When a decision truly needs the user (for example,
  no safe fix exists), record it as `needs-decision` and move on.
- Long runs: when develop is done, give their commands and stop. The
  status stays `running`: only the user ends a campaign. Never poll or
  sleep for them; the user runs them and asks for the analysis.
- Re-read the ledger index at the start of every loop iteration.
- Keep tool output short (tails, summaries) to save context.
