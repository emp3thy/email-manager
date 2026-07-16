#!/usr/bin/env bash
# autonomous-run.sh — one unattended email-processing run (macOS/Linux).
#
# Schedule with cron every ~15 minutes, e.g.:
#   */15 * * * * /path/to/email-manager/scripts/autonomous-run.sh >> /path/to/email-manager/autonomous.log 2>&1
#
# Prereqs on this machine:
#   - Claude Code CLI installed and on PATH (`claude`)
#   - .mcp.json configured with gmail-manager / outlook-manager / whatsapp-manager
#   - Gmail/Outlook/WhatsApp already authenticated at least once
#
# Permissions: an unattended run cannot answer permission prompts, so tools are
# pre-authorised. `--dangerously-skip-permissions` is used for simplicity and is
# acceptable here because autonomous mode never deletes/trashes mail (see
# autonomous-processing.md > Guardrails). For a tighter setup, drop that flag and
# add an allowlist to .claude/settings.json instead:
#   { "permissions": { "allow": [
#       "mcp__gmail-manager__*", "mcp__outlook-manager__*", "mcp__whatsapp-manager__*"
#   ] } }

set -euo pipefail

# Repo root = parent of this script's directory.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

read -r -d '' PROMPT <<'EOF' || true
Run the autonomous email processing workflow (autonomous-processing.md).
This is an unattended scheduled run: do not ask any questions, apply only the
conservative/safe actions defined there, and send the digest to me on WhatsApp.
EOF

claude -p "$PROMPT" --dangerously-skip-permissions
