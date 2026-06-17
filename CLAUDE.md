# Email Manager

Multi-account email management system (Gmail + Outlook). Uses MCP servers for email operations and `rules.yaml` for classification rules.

## Key Files

- `rules.yaml` — classification rules and settings
- `state.json` — per-account last_run timestamps and initial cleanup status

## Inbox Cleaning

When the user asks to clean their inbox, manage emails, or work with rules, invoke the `clean-inbox` skill. It contains the full workflow, MCP tool reference, operations mapping, and all procedures.

# currentDate
Today's date is 2026-03-15.
