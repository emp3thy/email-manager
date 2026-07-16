# autonomous-run.ps1 — one unattended email-processing run (Windows).
#
# Invoked by Task Scheduler every ~15 minutes (see register-task-windows.ps1).
# It cd's into the repo and runs Claude Code headlessly against the autonomous
# workflow defined in autonomous-processing.md.
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
# instead add an allowlist to .claude/settings.json, e.g.:
#   { "permissions": { "allow": [
#       "mcp__gmail-manager__*", "mcp__outlook-manager__*", "mcp__whatsapp-manager__*"
#   ] } }

$ErrorActionPreference = 'Stop'

# Repo root = parent of this script's folder.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Prompt = @'
Run the autonomous email processing workflow (autonomous-processing.md).
This is an unattended scheduled run: do not ask any questions, apply only the
conservative/safe actions defined there, and send the digest to me on WhatsApp.
'@

# Headless/print mode. Add `--model` etc. here if you want to pin a model.
claude -p $Prompt --dangerously-skip-permissions
