# Email Manager — Design Spec

## Overview

An on-demand email management system with two interfaces: **Claude Desktop** (claude.ai Project) for conversational cleanup and digest, and **Claude Code** (CLI) for rule editing and cleanup. Uses the [Gmail AutoAuth MCP Server](https://github.com/GongRzhe/Gmail-MCP-Server) (`@gongrzhe/server-gmail-autoauth-mcp`) for full Gmail read/write access. Both interfaces read `rules.yaml` to classify emails, apply labels and actions via MCP tools, and present a digest of important emails. No Python scripts or external dependencies beyond the MCP server.

## Goals

1. **Initial cleanup** — triage and classify 5,000–20,000 existing emails
2. **Ongoing management** — on-demand runs that classify, label, archive, and digest new mail
3. **Extensible rules** — rules editable conversationally from either interface

## Two Interfaces

### Claude Desktop (claude.ai Project)

- **Primary interface** for running cleanups and viewing digests
- Project custom instructions contain the workflow and heuristics
- `rules.yaml` and `state.json` uploaded as **knowledge files** (read-only from Claude's perspective)
- When Claude suggests rule changes, it outputs the updated YAML for the user to re-upload
- When state changes (new `last_run`), Claude outputs the updated JSON for the user to re-upload

### Claude Code (CLI)

- **Primary interface** for editing `rules.yaml` (direct file read/write)
- Can also run cleanups — same workflow, same MCP server
- Reads/writes `rules.yaml` and `state.json` directly in `email-manager/`
- After editing rules, user re-uploads `rules.yaml` to the Desktop project

### Sync Between Environments

- `rules.yaml` source of truth: the file in `email-manager/` (managed by Claude Code)
- Claude Desktop gets a copy as a knowledge file upload
- After Claude Code edits rules → user re-uploads `rules.yaml` to Desktop project
- After a Desktop cleanup run → Claude outputs updated `state.json` → user can optionally paste it back into `email-manager/state.json` via Claude Code, or just let each environment track its own last run

## Gmail MCP Server

**Package:** `@gongrzhe/server-gmail-autoauth-mcp`

**Install:**
```bash
npx -y @smithery/cli install @gongrzhe/server-gmail-autoauth-mcp --client claude
```

Must be configured in **both** Claude Desktop and Claude Code MCP settings.

**Capabilities used by this system:**

| Tool | Purpose |
|------|---------|
| `search_email` | Search with Gmail query syntax, paginated |
| `read_email` | Read single message by ID (headers + body) |
| `list_labels` | List all labels (system + user) |
| `create_label` | Create new labels |
| `move_email` | Move emails to labels/folders |
| `mark_as_read` / `mark_as_unread` | Read status management |
| `delete_email` | Move to trash |
| `batch_operations` | Process multiple emails at once |
| `send_email` | Send emails (used only for digest delivery if configured) |

**Setup:** Requires a Google Cloud Project with Gmail API enabled and OAuth2 credentials. The MCP server handles the auth flow automatically — opens a browser for consent, stores credentials in `~/.gmail-mcp/`. Credentials are shared between Desktop and Code since they use the same local store.

## Labeling System

Fresh label scheme with `em/` prefix:

| Label              | Purpose                                      |
|--------------------|----------------------------------------------|
| `em/important`     | Needs user response or attention             |
| `em/newsletters`   | Subscriptions, digests, mailing lists        |
| `em/receipts`      | Purchase confirmations, invoices             |
| `em/notifications` | Automated alerts (GitHub, social, apps)      |
| `em/promotions`    | Marketing, sales, deals                      |
| `em/spam`          | Junk that got past Gmail's filter            |
| `em/review`        | Ambiguous — flagged for LLM classification   |

Auto-archive targets: `em/newsletters`, `em/notifications`, `em/promotions` after configurable retention period (default 7 days).

**Label creation:** On first run (from either interface), check existing labels via `list_labels` and create any missing `em/*` labels via `create_label`.

## Rules Engine

Claude reads `rules.yaml` and applies the classification logic directly — no external scripts. Rules are evaluated in order; first match wins.

Pattern syntax: glob-style matching (case-insensitive). `*` matches any characters, `?` matches a single character.

### rules.yaml

```yaml
schema_version: 1

settings:
  retention_days:
    em/newsletters: 7
    em/notifications: 7
    em/promotions: 7
  privacy_mode: headers_first  # headers_first | full

rules:
  # Sender-based rules
  - pattern: "*@github.com"
    match_field: sender
    label: em/notifications
    action: archive

  - pattern: "*@medium.com"
    match_field: sender
    label: em/newsletters
    action: archive

  - pattern: "noreply@amazon.*"
    match_field: sender
    label: em/receipts
    action: keep

  - pattern: "*@spammydomain.com"
    match_field: sender
    label: em/spam
    action: trash

  # Subject-based rules
  - pattern: "*unsubscribe*"
    match_field: subject
    label: em/promotions
    action: archive

  # Header-based rules (presence check when pattern is "*")
  - pattern: "*"
    match_field: header
    header_name: List-Unsubscribe
    label: em/newsletters
    action: archive
```

**Supported `match_field` values:**
- `sender` — matches against the From header
- `subject` — matches against the Subject header
- `header` — matches against an arbitrary header specified by `header_name`

**Supported `action` values:**
- `keep` — label only, leave in inbox
- `archive` — label + remove from inbox
- `trash` — label + move to trash

**Editing rules:**
- **Claude Code:** reads and writes `rules.yaml` directly. User says "add a rule for linkedin emails" and Claude updates the file.
- **Claude Desktop:** Claude outputs the updated YAML block. User copies it and re-uploads to the project.

### Built-in Heuristics

For emails not matched by user-defined rules, Claude applies these defaults (embedded in the project instructions / CLAUDE.md, not in `rules.yaml`):

| Condition | Label | Action |
|-----------|-------|--------|
| `List-Unsubscribe` header present | `em/newsletters` | archive |
| `Precedence: bulk` header | `em/promotions` | archive |
| Subject matches order/receipt patterns | `em/receipts` | keep |

User rules always take precedence over built-in heuristics.

### Unmatched

Emails not classified by rules or heuristics get labeled `em/review`. Claude uses LLM judgment to classify these — reading headers first, body only if needed based on `privacy_mode`.

## Initial Cleanup

Four phases, executed conversationally from either Claude Desktop or Claude Code:

### Phase 1 — Profiling

- Fetch email headers in batches using `search_email` with pagination
- Group by sender domain, generate frequency report
- Present top senders to user for triage decisions (keep, archive, trash)

### Phase 2 — Rule Generation

- Based on triage decisions, Claude generates starter entries in `rules.yaml`
- User reviews generated rules in conversation before execution
- In Claude Code: written directly to file
- In Claude Desktop: output as YAML for user to upload

### Phase 3 — Batch Classification

- Claude applies rules to existing emails in batches using `batch_operations`
- Matched emails get labeled and archived/trashed
- Unmatched emails go to `em/review`

### Phase 4 — LLM Escalation

- Claude processes `em/review` emails in batches (~100 at a time, headers first, body if needed)
- Classifies and labels them
- Suggests new rules for patterns discovered — user approves, Claude updates `rules.yaml`

**Safety:** Everything goes to Trash or Archive first. No permanent deletion. Claude presents a summary of planned actions and asks for confirmation before executing, especially during initial cleanup.

## Ongoing Management

### On-demand Cleanup

Identical workflow in both interfaces. User says "clean my inbox" (or similar):

1. Read `state.json` for last run timestamp
2. Fetch all emails since last run via `search_email` (using `after:` query)
3. Apply rules from `rules.yaml` — first match wins
4. Label and act on matched emails via MCP (`move_email` / `delete_email`)
5. For `em/review` items, classify using LLM judgment (headers first, body if needed)
6. Auto-archive aged-out newsletters/notifications/promotions past retention period
7. Generate digest (see below)
8. Update `state.json` with new timestamp
9. User can approve, override, or teach new rules inline

### Digest

Generated at the end of every cleanup run:

- Lists `em/important` emails since last run
- Per email: sender, subject, date, why it needs attention, suggested action
- Operational summary: counts of emails classified, archived, trashed, flagged for review
- Presented directly in the conversation (Desktop or Code)

### State Tracking

`state.json`:

```json
{
  "last_run": "2026-03-13T08:00:00Z",
  "initial_cleanup": {
    "status": "completed",
    "emails_processed": 15000
  }
}
```

- `last_run`: timestamp of last completed run (used for `after:` Gmail query)
- `initial_cleanup`: tracks whether the initial cleanup is done and progress if interrupted
- **Claude Code:** reads/writes directly
- **Claude Desktop:** read from knowledge file; Claude outputs updated JSON after each run for user to re-upload

## Architecture

### File Structure

```
email-manager/
├── rules.yaml              # Classification rules + settings (source of truth)
├── state.json              # Last run timestamp and progress
├── CLAUDE.md               # Claude Code project instructions
├── desktop-instructions.md # Custom instructions to paste into Claude Desktop project
└── docs/
    └── superpowers/
        └── specs/
```

### CLAUDE.md (for Claude Code)

Project instructions that Claude Code loads automatically when working in `email-manager/`. Contains:

1. **Role and workflow** — email manager role, full cleanup data flow
2. **Rules interpretation** — how to read `rules.yaml`, match patterns, first match wins
3. **Built-in heuristics** — default classification for unmatched emails
4. **Privacy** — headers-first by default, body only when needed
5. **Safety** — confirm before bulk operations, trash/archive only, never permanently delete
6. **Digest format** — how to present the importance digest and operational summary
7. **Rule management** — how to edit `rules.yaml` when user requests changes
8. **MCP server** — which Gmail MCP tools to use for each operation

### desktop-instructions.md (for Claude Desktop Project)

Same content as `CLAUDE.md` but formatted for pasting into a Claude Desktop project's custom instructions field. Additionally notes:
- `rules.yaml` and `state.json` are available as project knowledge files
- After cleanup runs, Claude should output updated `state.json` for the user to re-upload
- For rule changes, Claude should output the full updated `rules.yaml` for re-upload

### Claude Desktop Project Setup

1. Create a new Project in claude.ai called "Email Manager"
2. Paste contents of `desktop-instructions.md` into the project custom instructions
3. Upload `rules.yaml` as a knowledge file
4. Upload `state.json` as a knowledge file
5. Ensure the Gmail MCP server is configured in Claude Desktop settings

### Privacy

- `settings.privacy_mode` in `rules.yaml`: `headers_first` (default)
- Classification works on headers only (sender, subject, date, mail headers)
- Body content only fetched for digest summaries of `em/important` emails or when LLM needs it for ambiguous classification
- User can set `privacy_mode: full` to allow body access for all classification

### Data Flow

```
User says "clean my inbox" (Desktop or Code)
  → Read state.json for last run timestamp
  → Read rules.yaml for classification rules
  → Fetch emails since last run (search_email MCP)
  → For each email, apply rules (first match wins)
  → Label + act on matched emails (move_email / delete_email MCP)
  → For unmatched → label em/review → LLM classifies (headers, body if needed)
  → Apply LLM classifications via MCP
  → Auto-archive aged-out newsletters/notifications/promotions
  → Generate digest of em/important emails
  → Present digest + operational summary in conversation
  → Update state.json with new timestamp
```

### Thread Handling

Classification operates on individual messages, not threads. If a thread contains mixed content (e.g., a newsletter reply), each message is classified independently.

## Non-Goals

- No scheduled/daemon mode — on-demand only
- No permanent deletion — trash and archive only
- No web UI — Claude Desktop and Claude Code are the interfaces
- No Python scripts — Claude does all logic directly
- No separate OAuth setup — handled by the MCP server
