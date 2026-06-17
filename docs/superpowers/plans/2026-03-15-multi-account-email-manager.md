# Multi-Account Email Manager Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update CLAUDE.md and state.json to support both Gmail and Outlook email cleanup using the same shared rules.

**Architecture:** Prompt-driven system with no application code. All logic lives in CLAUDE.md instructions that the LLM follows at runtime. Two MCP servers provide the Gmail and Outlook APIs. A single `rules.yaml` drives classification for both accounts, with provider-specific action mapping.

**Tech Stack:** CLAUDE.md (prompt instructions), JSON (state), YAML (rules), Gmail MCP server, Outlook MCP server.

**Spec:** `docs/superpowers/specs/2026-03-15-multi-account-email-manager-design.md`

---

## Chunk 1: State Migration and CLAUDE.md Rewrite

### Task 1: Migrate state.json to per-account structure

**Files:**
- Modify: `state.json`

- [ ] **Step 1: Read current state.json and migrate to per-account format**

Current flat structure:
```json
{
  "last_run": "2026-03-14T16:30:00Z",
  "initial_cleanup": {
    "status": "completed",
    "emails_processed": 1800
  }
}
```

Rewrite to:
```json
{
  "accounts": {
    "gmail": {
      "last_run": "2026-03-14T16:30:00Z",
      "initial_cleanup": {
        "status": "completed",
        "emails_processed": 1800
      }
    },
    "outlook": {
      "last_run": null,
      "initial_cleanup": {
        "status": "not_started",
        "emails_processed": 0
      }
    }
  }
}
```

- [ ] **Step 2: Commit state.json migration**

```bash
git add state.json
git commit -m "feat: migrate state.json to per-account structure"
```

---

### Task 2: Rewrite CLAUDE.md for multi-account support

**Files:**
- Modify: `CLAUDE.md`

This is a full rewrite of the file. The new CLAUDE.md must contain all of the following sections, in this order. Each step adds one section.

- [ ] **Step 1: Write header and Account Targeting section**

Replace the opening line and add a new section before MCP Server:

```markdown
# Email Manager

You are an email management assistant. When the user asks you to clean their inbox, manage emails, or work with rules, follow the instructions below.

## Account Targeting

This system manages two email accounts: Gmail and Outlook (Hotmail).

- "Clean my inbox" / "clean my email" / "clean up" → process both accounts sequentially (Gmail first, then Outlook)
- "Clean my gmail" → process Gmail only
- "Clean my outlook" / "clean my hotmail" → process Outlook only

Before processing Outlook, always check authentication:
1. Call `mcp__outlook-manager__check-auth-status`
2. If not authenticated, call `mcp__outlook-manager__authenticate` and guide user through browser flow
3. Proceed only after auth is confirmed

Gmail uses auto-auth and does not need this step.
```

- [ ] **Step 2: Write MCP Server section (Gmail + Outlook)**

Keep the existing Gmail MCP server documentation. Add the Outlook MCP server section after it. Replace the "Key operations mapping" table with a provider-comparison table.

```markdown
## MCP Servers

### Gmail — gmail-manager (read/write — `@gongrzhe/server-gmail-autoauth-mcp`)

Primary server for all Gmail operations. Tool names are prefixed with `mcp__gmail-manager__`:

- `search_emails` — search emails with Gmail query syntax (params: `query`, `maxResults`)
- `read_email` — read a single message by ID (param: `messageId`)
- `list_email_labels` — list all Gmail labels
- `create_label` — create a new label (param: `name`)
- `get_or_create_label` — get existing or create label (param: `name`)
- `modify_email` — add/remove labels on a message (params: `messageId`, `addLabelIds`, `removeLabelIds`)
- `batch_modify_emails` — bulk add/remove labels (params: `messageIds`, `addLabelIds`, `removeLabelIds`)
- `delete_email` — permanently delete (param: `messageId`) — avoid using, prefer archiving
- `send_email` — send an email (params: `to`, `subject`, `body`)
- `draft_email` — create a draft (params: `to`, `subject`, `body`)
- `create_filter` — create Gmail filter (params: `criteria`, `action`)

### Gmail — Claude.ai Gmail (read-only — built-in connector)

Fallback for reading. Tool names are prefixed with `mcp__claude_ai_Gmail__`:

- `gmail_search_messages` — search with Gmail syntax
- `gmail_read_message` — read message by ID
- `gmail_read_thread` — read full thread
- `gmail_list_labels` — list labels
- `gmail_get_profile` — get account info

### Outlook — outlook-manager (read/write)

Server for all Outlook/Hotmail operations. Tool names are prefixed with `mcp__outlook-manager__`:

- `search-emails` — search emails with KQL syntax (params: `query`, `from`, `subject`, `folder`, `count`, `unreadOnly`, `hasAttachments`)
- `read-email` — read a single message by ID (params: `id`, `includeRawHtml`)
- `list-emails` — list recent emails from a folder (params: `folder`, `count`)
- `list-folders` — list mail folders (params: `includeChildren`, `includeItemCounts`)
- `create-folder` — create a new folder (params: `name`, `parentFolder`)
- `move-emails` — move emails between folders (params: `emailIds` comma-separated, `targetFolder`, `sourceFolder`)
- `mark-as-read` — mark an email as read (param: `id`)
- `authenticate` — start OAuth flow (param: `force`)
- `check-auth-status` — check current auth state

### Operations mapping by provider

| Operation | Gmail | Outlook |
|-----------|-------|---------|
| Search emails | `search_emails` with `query: "after:YYYY/MM/DD"` | `search-emails` with `query: "received:>=YYYY/MM/DD"`, `count: 200` |
| Read email | `read_email` with `messageId` | `read-email` with `id` |
| List labels/folders | `list_email_labels` | `list-folders` |
| Create label/folder | `get_or_create_label` (idempotent) | `create-folder` (check existence first via `list-folders`) |
| Classify (keep) | `modify_email` with `addLabelIds: ["<label_id>"]` | `mark-as-read` with `id` (leave in Inbox) |
| Classify (archive) | `modify_email` with `addLabelIds: ["<label_id>"], removeLabelIds: ["INBOX"]` | `move-emails` with `targetFolder: "em-<category>"` |
| Classify (trash) | `modify_email` with `addLabelIds: ["TRASH", "<label_id>"], removeLabelIds: ["INBOX"]` | `move-emails` with `targetFolder: "Deleted Items"` |
| Classify (unmatched) | `modify_email` with `addLabelIds: ["<em/review label_id>"]` | `mark-as-read` with `id` (leave in Inbox for manual review) |
| Bulk classify | `batch_modify_emails` with `messageIds` array | `move-emails` with comma-separated `emailIds` (~50 per call) |

### Outlook tool notes

- **search-emails** uses KQL via the `query` parameter. Despite the schema declaring max 50 for `count`, the implementation paginates beyond 50 internally. When `query` is provided, results are in relevance order — sort client-side by `receivedDateTime` if chronological order is needed. **Do not use `unreadOnly` when `query` is also provided** — the two use incompatible filtering mechanisms (KQL vs OData). Instead, filter client-side by checking the `isRead` field on each result.
- **move-emails** takes `emailIds` as a comma-separated string, not an array. Batch in groups of ~50 IDs per call.
- **read-email** does not expose raw email headers (e.g., `List-Unsubscribe`, `Precedence`). Classification relies on From address, Subject, and body content only.
- **mark-as-read** processes one email at a time (no batch endpoint).
- **create-folder** is NOT idempotent. Always call `list-folders` first and only create folders that don't already exist.
- **Token refresh** is handled automatically by the MCP server. Long-running cleanup operations are resilient to token expiration.
```

- [ ] **Step 3: Write Outlook Folder Structure section**

```markdown
## Outlook Folder Structure

Outlook uses flat top-level folders instead of Gmail's nested labels. The MCP server's folder lookup only searches top-level folders, so nested folders cannot be used.

| Gmail label | Outlook folder |
|-------------|---------------|
| `em/important` | `em-important` |
| `em/newsletters` | `em-newsletters` |
| `em/notifications` | `em-notifications` |
| `em/promotions` | `em-promotions` |
| `em/receipts` | `em-receipts` |
| `em/spam` | (no folder — trash action moves to `Deleted Items`) |
| `em/review` | (no folder — unmatched emails stay in Inbox) |

### Folder setup (first run)

Before classifying Outlook emails for the first time:
1. Call `list-folders` to get existing top-level folders
2. For each required folder (`em-important`, `em-newsletters`, `em-notifications`, `em-promotions`, `em-receipts`):
   - If it doesn't exist in the folder list, call `create-folder` with that name
```

- [ ] **Step 4: Write Cleanup Workflow section**

Replace the existing Cleanup Workflow with a multi-account version:

```markdown
## Cleanup Workflow

When the user asks to clean their inbox (see Account Targeting for which accounts to process):

For each target account (Gmail, Outlook, or both):

1. **Check auth** — (Outlook only) call `check-auth-status`. If not authenticated, run auth flow before proceeding.
1b. **Verify folders** — (Outlook only) call `list-folders` and confirm all required `em-*` folders exist. Create any missing ones via `create-folder`. This runs on every cleanup, not just initial — folders may have been deleted between runs.
2. **Read state** — read `state.json` in this directory. Access state under `accounts.<provider>` (e.g., `accounts.gmail.last_run`).
   - **Migration:** if `state.json` has a flat `last_run` field (no `accounts` key), migrate: move existing `last_run` and `initial_cleanup` into `accounts.gmail`, initialize `accounts.outlook` with `last_run: null` and `initial_cleanup.status: "not_started"`.
3. **Check initial cleanup** — if `accounts.<provider>.initial_cleanup.status` is not `completed`, run the Initial Cleanup process (see below) instead of the standard workflow.
4. **Read rules** — read `rules.yaml` in this directory for classification rules and settings.
5. **Fetch emails** — get new emails since `last_run`:
   - Gmail: `search_emails` with `query: "after:YYYY/MM/DD"` from `last_run`
   - Outlook: `search-emails` with `query: "received:>=YYYY/MM/DD"` from `last_run`, `count: 200`. Filter results to unread emails only (check `isRead` field).
6. **Classify each email** by applying rules from `rules.yaml` in order. First match wins:
   - For `match_field: sender` — glob-match the pattern against the From header (case-insensitive).
   - For `match_field: subject` — glob-match the pattern against the Subject header (case-insensitive).
   - For `match_field: header` — (Gmail only) check if the header specified by `header_name` exists (if pattern is `*`) or glob-match its value. **Skip header rules for Outlook** — the Outlook MCP server does not expose raw email headers.
7. **Apply built-in heuristics** to emails not matched by user rules:
   - Gmail only: `List-Unsubscribe` header present → `em/newsletters`, archive
   - Gmail only: `Precedence: bulk` header → `em/promotions`, archive
   - Both: Subject matches order/receipt/confirmation patterns → `em/receipts`, keep
   - Outlook only: if body contains "unsubscribe" text, consider as newsletter → `em/newsletters`, archive
8. **Handle unmatched emails**:
   - Gmail: label as `em/review`
   - Outlook: leave in Inbox, `mark-as-read`
   - Use LLM judgment to classify — read headers first (Gmail) or From/Subject (Outlook), body only if needed (respect `privacy_mode` setting).
9. **Execute actions** — use the provider-specific tools from the operations mapping table above.
   - For Outlook `em/spam` rules: ignore the label, just execute the `trash` action (move to `Deleted Items`).
   - For Outlook `keep` rules: `mark-as-read` only (no folder move).
10. **Auto-archive/retain aged emails** — for labels with `retention_days` set in settings:
    - Gmail: search for emails with that label older than the retention period and archive them.
    - Outlook: use `list-emails` on the `em-<category>` folder, filter by `receivedDateTime` for emails older than retention period, move aged emails to `Deleted Items`.
11. **Generate digest** — see Digest Format below.
12. **Update state** — write the current timestamp to `state.json` under `accounts.<provider>.last_run`.
```

- [ ] **Step 5: Write Digest Format section**

```markdown
## Digest Format

At the end of every cleanup run, present a combined digest:

### Important Emails

For each account processed, list emails classified as `em/important`:

#### Gmail
For each important Gmail email:
- **From:** sender
- **Subject:** subject line
- **Date:** when received
- **Why:** brief explanation of why this needs attention
- **Suggested action:** reply, follow up, review, etc.

#### Outlook
For each important Outlook email (same format as above).

### Operational Summary

| | Gmail | Outlook | Total |
|---|---|---|---|
| Processed | X | Y | X+Y |
| em/important | ... | ... | ... |
| em/newsletters | ... | ... | ... |
| em/notifications | ... | ... | ... |
| em/promotions | ... | ... | ... |
| em/receipts | ... | ... | ... |
| Archived | ... | ... | ... |
| Trashed | ... | ... | ... |
| Review | ... | ... | ... |

If only one account was processed, show a single-column summary (no need for the comparison table).
```

- [ ] **Step 6: Write Rule Management section (unchanged)**

```markdown
## Rule Management

When the user asks to add, modify, or remove a rule:

1. Read the current `rules.yaml`
2. Make the requested change
3. Write the updated file back
4. Confirm the change to the user

Rules format:
\```yaml
- pattern: "*@example.com"
  match_field: sender       # sender | subject | header
  label: em/newsletters     # em/* label name
  action: archive           # keep | archive | trash
  # header_name: X-Header   # only when match_field is "header"
\```

Pattern syntax: glob-style, case-insensitive. `*` matches any characters, `?` matches single character.
Rules are evaluated in order — first match wins.
Rules with `match_field: header` only apply to Gmail. They are skipped for Outlook.
```

- [ ] **Step 7: Write Privacy and Safety sections**

```markdown
## Privacy

- Default mode is `headers_first` — only read email headers/metadata for classification.
- Only fetch email body when:
  - Generating digest summaries for `em/important` emails
  - LLM needs body content to classify an ambiguous `em/review` email
  - (Outlook) checking for "unsubscribe" text as a newsletter heuristic
- Check `settings.privacy_mode` in `rules.yaml` before reading body content.

## Safety

- Never permanently delete emails. Use trash or archive only.
- Before bulk operations (especially during initial cleanup), present a summary of planned actions and ask for user confirmation.
- When suggesting rule changes, explain what the rule will match before applying it.
- Outlook "trash" moves to Deleted Items (recoverable), not permanent deletion.
```

- [ ] **Step 8: Write Initial Cleanup section**

```markdown
## Initial Cleanup

If `state.json` shows `accounts.<provider>.initial_cleanup.status` is not `completed`, run the initial cleanup for that provider:

### Phase 1 — Profiling
- Fetch email headers in batches:
  - Gmail: `search_emails` with pagination
  - Outlook: `search-emails` with high `count` (e.g., 500)
- Group by sender domain
- Present a frequency report of top senders
- Ask the user for triage decisions: keep, archive, or trash each sender group

### Phase 2 — Rule Generation
- Based on triage decisions, generate rules and write them to `rules.yaml`
- Present the rules for user review before proceeding

### Phase 3 — Batch Classification
- Apply rules to all existing emails:
  - Gmail: use `batch_modify_emails` to label and archive/trash matched emails
  - Outlook: use `move-emails` to move matched emails to `em-<category>` folders or `Deleted Items`, in batches of ~50 IDs
- Flag unmatched:
  - Gmail: label as `em/review`
  - Outlook: `mark-as-read` (stays in Inbox)

### Phase 4 — LLM Escalation
- Process unmatched emails in batches (~100 at a time)
- Classify using headers/metadata first, body if needed
- Suggest new rules for patterns discovered
- Update `rules.yaml` with approved rules

### Outlook folder setup

Before Phase 3 for Outlook, ensure the folder structure exists:
1. Call `list-folders` to get existing top-level folders
2. For each required folder (`em-important`, `em-newsletters`, `em-notifications`, `em-promotions`, `em-receipts`):
   - If it doesn't exist, call `create-folder` with that name

### After all phases complete

Update `state.json` under `accounts.<provider>`:
\```json
{
  "accounts": {
    "<provider>": {
      "last_run": "<current ISO timestamp>",
      "initial_cleanup": {
        "status": "completed",
        "emails_processed": <count>
      }
    }
  }
}
\```
```

- [ ] **Step 9: Assemble full CLAUDE.md and write the file**

Combine all sections from Steps 1-8 into a single file in this order:
1. Header + Account Targeting (Step 1)
2. MCP Servers (Step 2)
3. Outlook Folder Structure (Step 3)
4. Cleanup Workflow (Step 4)
5. Digest Format (Step 5)
6. Rule Management (Step 6)
7. Privacy and Safety (Step 7)
8. Initial Cleanup (Step 8)

Write the complete file to `CLAUDE.md`.

- [ ] **Step 10: Commit CLAUDE.md rewrite**

```bash
git add CLAUDE.md
git commit -m "feat: rewrite CLAUDE.md for multi-account email management

Add Outlook MCP server documentation, account targeting, provider-specific
action mapping, flat folder naming, and combined digest format."
```

---

### Task 3: Verify the setup

- [ ] **Step 1: Verify state.json is valid JSON**

Read `state.json` and confirm it parses correctly with both `accounts.gmail` and `accounts.outlook` entries.

- [ ] **Step 2: Verify CLAUDE.md contains all required sections**

Read `CLAUDE.md` and confirm it contains:
- Account Targeting section
- Gmail MCP server documentation
- Outlook MCP server documentation
- Operations mapping table
- Outlook folder structure table
- Multi-account cleanup workflow
- Combined digest format
- Initial cleanup with provider-specific instructions
- State migration instructions

- [ ] **Step 3: Verify Outlook folders can be created**

Call `mcp__outlook-manager__list-folders` to confirm Outlook auth is working and list existing folders. Then create any missing `em-*` folders.

- [ ] **Step 4: Final commit if any adjustments were needed**

```bash
git add -A
git commit -m "fix: adjustments from verification"
```
