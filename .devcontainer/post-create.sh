#!/usr/bin/env bash
# Registers this checkout as a local plugin marketplace for Copilot CLI,
# installs the skills from it, and checks that Copilot CLI lists every skill
# in plugin.json. Copilot loads them live from the checkout, so skill edits
# take effect after /restart. Claude Code and Codex aren't set up here.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p /workspaces/targets 2> /dev/null \
  || { sudo mkdir -p /workspaces/targets && sudo chown "$(id -u):$(id -g)" /workspaces/targets; }

missing=""
if command -v copilot > /dev/null; then
  copilot plugin marketplace add "$repo" || true
  copilot plugin install seclab-skills@seclab || true
  listed="$(copilot skill list --json 2> /dev/null || true)"
  for skill in $(python3 -c 'import json, os, sys
print(" ".join(os.path.basename(p) for p in json.load(open(sys.argv[1]))["skills"]))' "$repo/.claude-plugin/plugin.json"); do
    case "$listed" in *"\"$skill\""*) ;; *) missing="$missing $skill" ;; esac
  done
else
  missing=" all (Copilot CLI not found)"
fi

if [ -n "$missing" ]; then
  cat >&2 <<EOF

SETUP INCOMPLETE: Copilot CLI doesn't list these skills:$missing
Install them by hand, then check:
  copilot plugin marketplace add $repo
  copilot plugin install seclab-skills@seclab
  copilot skill list
EOF
  exit 1
fi

cat <<'EOF'

Fuzzing codespace ready, with the skills installed for Copilot CLI.
- Clone projects to fuzz into /workspaces/targets/.
- Start Copilot CLI with `copilot` and sign in when asked (if it fails
  without asking, run /login).
- Codespaces stop after the idle timeout and live at most 12 hours; see README.
EOF
