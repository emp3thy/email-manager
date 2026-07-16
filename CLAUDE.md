# Email Manager

Multi-account email management system (Gmail + Outlook). Uses MCP servers for email operations and `rules.yaml` for classification rules.

## Key Files

- `rules.yaml` — classification rules and settings
- `state.json` — per-account last_run timestamps and initial cleanup status

## Inbox Cleaning

When the user asks to clean their inbox, manage emails, or work with rules, invoke the `clean-inbox` skill. It contains the full workflow, MCP tool reference, operations mapping, and all procedures.

## Always-On (Autonomous) Processing

When the request is to **run the autonomous email processing workflow** (this is how the
scheduler invokes headless runs; the prompt references `autonomous-processing.md`), follow
`autonomous-processing.md` instead of the interactive `clean-inbox` skill. That mode is
**non-interactive and conservative**: it processes only mail since `last_run`, applies only
safe actions (never deletes/trashes — trash-rules are held for review), creates no new rules,
and sends a short digest over WhatsApp (`whatsapp-send-message`, defaults to messaging
yourself). Setup and scheduling live in `autonomous-processing.md` and `scripts/`.

# currentDate
Today's date is 2026-03-15.
