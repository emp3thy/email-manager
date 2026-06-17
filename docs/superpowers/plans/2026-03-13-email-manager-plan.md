# Email Manager Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up an on-demand email management system operated from Claude Desktop and Claude Code, using the Gmail AutoAuth MCP Server for full Gmail access.

**Architecture:** No application code. The system consists of configuration files (`rules.yaml`, `state.json`), instruction documents (`CLAUDE.md`, `desktop-instructions.md`), and the Gmail MCP server. Claude itself is the runtime — it reads the config, applies rules, and calls MCP tools.

**Tech Stack:** Gmail AutoAuth MCP Server (`@gongrzhe/server-gmail-autoauth-mcp`), YAML config, Claude Desktop Projects, Claude Code.

---

## Chunk 1: MCP Server Setup and Verification

### Task 1: Install Gmail AutoAuth MCP Server

**Files:**
- Modify: `~/.gmail-mcp/` (created by MCP server auth flow)
- Modify: Claude Code MCP settings

- [ ] **Step 1: Install the MCP server package**

Run:
```bash
npx -y @smithery/cli install @gongrzhe/server-gmail-autoauth-mcp --client claude
```

If Smithery install fails, fall back to manual config. Add to Claude Code MCP settings (`~/.claude/settings.json` or equivalent):

```json
{
  "mcpServers": {
    "gmail": {
      "command": "npx",
      "args": ["@gongrzhe/server-gmail-autoauth-mcp"]
    }
  }
}
```

- [ ] **Step 2: Set up Google Cloud OAuth credentials**

This requires a Google Cloud Project with Gmail API enabled:

1. Go to https://console.cloud.google.com/
2. Create or select a project
3. Enable the Gmail API
4. Go to APIs & Services > Credentials
5. Create OAuth client ID (Desktop app type)
6. Download the JSON key file
7. Place it at `~/.gmail-mcp/gcp-oauth.keys.json`

- [ ] **Step 3: Run authentication**

```bash
npx @gongrzhe/server-gmail-autoauth-mcp auth
```

This opens a browser for Google consent. After approval, credentials are saved to `~/.gmail-mcp/credentials.json`.

- [ ] **Step 4: Verify MCP server works — list labels**

From Claude Code, call the `list_labels` MCP tool. Verify it returns Gmail system labels (INBOX, SENT, SPAM, etc.).

- [ ] **Step 5: Verify MCP server works — search emails**

From Claude Code, call `search_email` with query `is:inbox` and `maxResults: 5`. Verify it returns email results with message IDs, senders, subjects.

- [ ] **Step 6: Commit progress**

```bash
cd /c/Users/gethi/source/email-manager
git add -A
git commit -m "docs: add implementation plan"
```

---

### Task 2: Create Gmail Labels

**Files:**
- None (labels created in Gmail via MCP)

- [ ] **Step 1: Check existing labels**

Call `list_labels` MCP tool. Note any existing labels to avoid conflicts.

- [ ] **Step 2: Create em/* labels**

Call `create_label` for each of these (skip any that already exist):

- `em/important`
- `em/newsletters`
- `em/receipts`
- `em/notifications`
- `em/promotions`
- `em/spam`
- `em/review`

- [ ] **Step 3: Verify labels were created**

Call `list_labels` again. Confirm all 7 `em/*` labels appear in the list. Record each label's ID — these will be needed for `move_email` operations.

---

## Chunk 2: Configuration Files

### Task 3: Create rules.yaml

**Files:**
- Create: `C:\Users\gethi\source\email-manager\rules.yaml`

- [ ] **Step 1: Create starter rules.yaml**

```yaml
schema_version: 1

settings:
  retention_days:
    em/newsletters: 7
    em/notifications: 7
    em/promotions: 7
  privacy_mode: headers_first  # headers_first | full

rules: []
# Rules will be populated during initial cleanup profiling.
# Format:
#   - pattern: "*@example.com"     # glob pattern, case-insensitive
#     match_field: sender           # sender | subject | header
#     label: em/newsletters         # which em/* label to apply
#     action: archive               # keep | archive | trash
#     # header_name: X-Header-Name  # required when match_field is "header"
```

Write this to `C:\Users\gethi\source\email-manager\rules.yaml`.

- [ ] **Step 2: Verify YAML is valid**

```bash
python -c "import yaml; yaml.safe_load(open('rules.yaml')); print('valid')"
```

Expected: `valid`

- [ ] **Step 3: Commit**

```bash
git add rules.yaml
git commit -m "feat: add starter rules.yaml config"
```

---

### Task 4: Create state.json

**Files:**
- Create: `C:\Users\gethi\source\email-manager\state.json`

- [ ] **Step 1: Create initial state.json**

```json
{
  "last_run": null,
  "initial_cleanup": {
    "status": "not_started",
    "emails_processed": 0
  }
}
```

Write this to `C:\Users\gethi\source\email-manager\state.json`.

- [ ] **Step 2: Commit**

```bash
git add state.json
git commit -m "feat: add initial state.json"
```

---

## Chunk 3: Claude Code Instructions

### Task 5: Write CLAUDE.md

**Files:**
- Create: `C:\Users\gethi\source\email-manager\CLAUDE.md`

- [ ] **Step 1: Write the CLAUDE.md file**

This is the project instruction file that Claude Code loads automatically when working in the `email-manager/` directory. It tells Claude how to behave as an email manager.

```markdown
# Email Manager

You are an email management assistant. When the user asks you to clean their inbox, manage emails, or work with rules, follow the instructions below.

## MCP Server

This system uses the Gmail AutoAuth MCP Server. The following tools are available:

- `search_email` — search emails with Gmail query syntax
- `read_email` — read a single message by ID
- `list_labels` — list all Gmail labels
- `create_label` — create a new label
- `move_email` — move email to a label/folder
- `mark_as_read` / `mark_as_unread` — toggle read status
- `delete_email` — move to trash
- `batch_operations` — process multiple emails at once
- `send_email` — send an email

## Cleanup Workflow

When the user says "clean my inbox" or similar:

1. **Read state** — read `state.json` in this directory for the `last_run` timestamp.
2. **Read rules** — read `rules.yaml` in this directory for classification rules and settings.
3. **Fetch emails** — use `search_email` with `after:YYYY/MM/DD` (from `last_run`) to get new emails. Paginate through all results.
4. **Classify each email** by applying rules from `rules.yaml` in order. First match wins:
   - For `match_field: sender` — glob-match the pattern against the From header (case-insensitive).
   - For `match_field: subject` — glob-match the pattern against the Subject header (case-insensitive).
   - For `match_field: header` — check if the header specified by `header_name` exists (if pattern is `*`) or glob-match its value.
5. **Apply built-in heuristics** to emails not matched by user rules:
   - `List-Unsubscribe` header present → `em/newsletters`, archive
   - `Precedence: bulk` header → `em/promotions`, archive
   - Subject matches order/receipt/confirmation patterns → `em/receipts`, keep
6. **Label unmatched emails** as `em/review`. Use LLM judgment to classify — read headers first, body only if needed (respect `privacy_mode` setting).
7. **Execute actions** via MCP:
   - `keep` → apply label only (use `move_email` to add the label)
   - `archive` → apply label + remove from inbox
   - `trash` → apply label + use `delete_email`
8. **Auto-archive aged emails** — for labels with `retention_days` set, search for emails with that label older than the retention period and archive them.
9. **Generate digest** — see Digest Format below.
10. **Update state** — write the current timestamp to `state.json` `last_run` field.

## Digest Format

At the end of every cleanup run, present:

### Important Emails

For each email labeled `em/important` since last run:
- **From:** sender
- **Subject:** subject line
- **Date:** when received
- **Why:** brief explanation of why this needs attention
- **Suggested action:** reply, follow up, review, etc.

### Operational Summary

- Emails processed: N
- Classified: N per label (em/important: X, em/newsletters: Y, etc.)
- Actions taken: N archived, N trashed, N kept, N flagged for review

## Rule Management

When the user asks to add, modify, or remove a rule:

1. Read the current `rules.yaml`
2. Make the requested change
3. Write the updated file back
4. Confirm the change to the user

Rules format:
```yaml
- pattern: "*@example.com"
  match_field: sender       # sender | subject | header
  label: em/newsletters     # em/* label name
  action: archive           # keep | archive | trash
  # header_name: X-Header   # only when match_field is "header"
```

Pattern syntax: glob-style, case-insensitive. `*` matches any characters, `?` matches single character.
Rules are evaluated in order — first match wins.

## Privacy

- Default mode is `headers_first` — only read email headers for classification.
- Only fetch email body when:
  - Generating digest summaries for `em/important` emails
  - LLM needs body content to classify an ambiguous `em/review` email
- Check `settings.privacy_mode` in `rules.yaml` before reading body content.

## Safety

- Never permanently delete emails. Use trash or archive only.
- Before bulk operations (especially during initial cleanup), present a summary of planned actions and ask for user confirmation.
- When suggesting rule changes, explain what the rule will match before applying it.

## Initial Cleanup

If `state.json` shows `initial_cleanup.status` is not `completed`:

### Phase 1 — Profiling
- Fetch email headers in batches using `search_email` with pagination
- Group by sender domain
- Present a frequency report of top senders
- Ask the user for triage decisions: keep, archive, or trash each sender group

### Phase 2 — Rule Generation
- Based on triage decisions, generate rules and write them to `rules.yaml`
- Present the rules for user review before proceeding

### Phase 3 — Batch Classification
- Apply rules to all existing emails using `batch_operations`
- Label and archive/trash matched emails
- Flag unmatched as `em/review`

### Phase 4 — LLM Escalation
- Process `em/review` emails in batches (~100 at a time)
- Classify using headers first, body if needed
- Suggest new rules for patterns discovered
- Update `rules.yaml` with approved rules

After all phases complete, update `state.json`:
```json
{
  "initial_cleanup": {
    "status": "completed",
    "emails_processed": <count>
  }
}
```
```

Write this to `C:\Users\gethi\source\email-manager\CLAUDE.md`.

- [ ] **Step 2: Verify CLAUDE.md loads**

From the `email-manager/` directory in Claude Code, start a new session. Confirm that the instructions are loaded by asking Claude to describe its email management capabilities.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "feat: add CLAUDE.md with email manager instructions"
```

---

## Chunk 4: Claude Desktop Instructions

### Task 6: Write desktop-instructions.md

**Files:**
- Create: `C:\Users\gethi\source\email-manager\desktop-instructions.md`

- [ ] **Step 1: Write the desktop-instructions.md file**

This contains the same workflow as CLAUDE.md but adapted for the Claude Desktop environment where file writes require user re-upload.

```markdown
# Email Manager

You are an email management assistant. When the user asks you to clean their inbox, manage emails, or work with rules, follow the instructions below.

## MCP Server

This system uses the Gmail AutoAuth MCP Server. The following tools are available:

- `search_email` — search emails with Gmail query syntax
- `read_email` — read a single message by ID
- `list_labels` — list all Gmail labels
- `create_label` — create a new label
- `move_email` — move email to a label/folder
- `mark_as_read` / `mark_as_unread` — toggle read status
- `delete_email` — move to trash
- `batch_operations` — process multiple emails at once
- `send_email` — send an email

## Cleanup Workflow

When the user says "clean my inbox" or similar:

1. **Read state** — check the `state.json` knowledge file for the `last_run` timestamp. If this is the first run, process all inbox emails.
2. **Read rules** — check the `rules.yaml` knowledge file for classification rules and settings.
3. **Fetch emails** — use `search_email` with `after:YYYY/MM/DD` (from `last_run`) to get new emails. Paginate through all results.
4. **Classify each email** by applying rules from `rules.yaml` in order. First match wins:
   - For `match_field: sender` — glob-match the pattern against the From header (case-insensitive).
   - For `match_field: subject` — glob-match the pattern against the Subject header (case-insensitive).
   - For `match_field: header` — check if the header specified by `header_name` exists (if pattern is `*`) or glob-match its value.
5. **Apply built-in heuristics** to emails not matched by user rules:
   - `List-Unsubscribe` header present → `em/newsletters`, archive
   - `Precedence: bulk` header → `em/promotions`, archive
   - Subject matches order/receipt/confirmation patterns → `em/receipts`, keep
6. **Label unmatched emails** as `em/review`. Use LLM judgment to classify — read headers first, body only if needed (respect `privacy_mode` setting).
7. **Execute actions** via MCP:
   - `keep` → apply label only (use `move_email` to add the label)
   - `archive` → apply label + remove from inbox
   - `trash` → apply label + use `delete_email`
8. **Auto-archive aged emails** — for labels with `retention_days` set, search for emails with that label older than the retention period and archive them.
9. **Generate digest** — see Digest Format below.
10. **Output updated state** — at the end of the run, output the updated `state.json` content so the user can re-upload it to the project:

```
Updated state.json — please re-upload to the project:
```json
{
  "last_run": "<current ISO timestamp>",
  "initial_cleanup": {
    "status": "completed",
    "emails_processed": <count>
  }
}
```
```

## Digest Format

At the end of every cleanup run, present:

### Important Emails

For each email labeled `em/important` since last run:
- **From:** sender
- **Subject:** subject line
- **Date:** when received
- **Why:** brief explanation of why this needs attention
- **Suggested action:** reply, follow up, review, etc.

### Operational Summary

- Emails processed: N
- Classified: N per label (em/important: X, em/newsletters: Y, etc.)
- Actions taken: N archived, N trashed, N kept, N flagged for review

## Rule Management

When the user asks to add, modify, or remove a rule:

1. Read the current `rules.yaml` from the project knowledge files
2. Generate the updated `rules.yaml` content
3. Output the full updated file so the user can re-upload it:

```
Updated rules.yaml — please re-upload to the project:
```yaml
<full updated rules.yaml content>
```
```

Alternatively, tell the user: "You can ask Claude Code to make this change directly — run Claude Code in the email-manager folder and ask it to update the rule."

Rules format:
```yaml
- pattern: "*@example.com"
  match_field: sender       # sender | subject | header
  label: em/newsletters     # em/* label name
  action: archive           # keep | archive | trash
  # header_name: X-Header   # only when match_field is "header"
```

Pattern syntax: glob-style, case-insensitive. `*` matches any characters, `?` matches single character.
Rules are evaluated in order — first match wins.

## Privacy

- Default mode is `headers_first` — only read email headers for classification.
- Only fetch email body when:
  - Generating digest summaries for `em/important` emails
  - LLM needs body content to classify an ambiguous `em/review` email
- Check `settings.privacy_mode` in `rules.yaml` before reading body content.

## Safety

- Never permanently delete emails. Use trash or archive only.
- Before bulk operations (especially during initial cleanup), present a summary of planned actions and ask for user confirmation.
- When suggesting rule changes, explain what the rule will match before applying it.

## Initial Cleanup

If `state.json` shows `initial_cleanup.status` is not `completed` (or if this is the first conversation):

### Phase 1 — Profiling
- Fetch email headers in batches using `search_email` with pagination
- Group by sender domain
- Present a frequency report of top senders
- Ask the user for triage decisions: keep, archive, or trash each sender group

### Phase 2 — Rule Generation
- Based on triage decisions, generate rules
- Present the rules for user review
- Output the full updated `rules.yaml` for user to upload to the project

### Phase 3 — Batch Classification
- Apply rules to all existing emails using `batch_operations`
- Label and archive/trash matched emails
- Flag unmatched as `em/review`

### Phase 4 — LLM Escalation
- Process `em/review` emails in batches (~100 at a time)
- Classify using headers first, body if needed
- Suggest new rules for patterns discovered
- Output updated `rules.yaml` for user to upload

After all phases complete, output updated `state.json` for re-upload.
```

Write this to `C:\Users\gethi\source\email-manager\desktop-instructions.md`.

- [ ] **Step 2: Commit**

```bash
git add desktop-instructions.md
git commit -m "feat: add Claude Desktop project instructions"
```

---

### Task 7: Set Up Claude Desktop Project

**Files:**
- None (configuration in claude.ai UI)

- [ ] **Step 1: Create the project**

In claude.ai:
1. Navigate to Projects
2. Click "+ New Project"
3. Name it "Email Manager"

- [ ] **Step 2: Add custom instructions**

Copy the full contents of `C:\Users\gethi\source\email-manager\desktop-instructions.md` and paste into the project's custom instructions field.

- [ ] **Step 3: Upload knowledge files**

Upload these files to the project:
- `C:\Users\gethi\source\email-manager\rules.yaml`
- `C:\Users\gethi\source\email-manager\state.json`

- [ ] **Step 4: Verify the Gmail MCP server is configured in Claude Desktop**

Check Claude Desktop settings to confirm the Gmail MCP server (`@gongrzhe/server-gmail-autoauth-mcp`) is listed and active.

- [ ] **Step 5: Test from Claude Desktop**

In the Email Manager project, start a new conversation and say: "List my Gmail labels."

Verify Claude uses the MCP server and returns labels including the `em/*` labels created in Task 2.

---

## Chunk 5: End-to-End Verification

### Task 8: Verify Cleanup from Claude Code

**Files:**
- Modify: `C:\Users\gethi\source\email-manager\state.json` (updated by cleanup run)

- [ ] **Step 1: Add a test rule**

In Claude Code, from the `email-manager/` directory, add a rule to `rules.yaml`. For example, pick a known frequent sender from your inbox:

```yaml
rules:
  - pattern: "*@<known-sender-domain>"
    match_field: sender
    label: em/newsletters
    action: keep
```

(Use `keep` for testing so nothing gets moved.)

- [ ] **Step 2: Run a small cleanup**

Tell Claude Code: "Clean my inbox — just process the last 10 emails as a test."

Verify:
- Claude reads `state.json` and `rules.yaml`
- Claude fetches recent emails via MCP
- Claude classifies them against the rule
- Claude applies labels via MCP
- Claude presents a digest
- Claude updates `state.json`

- [ ] **Step 3: Verify labels were applied**

Call `search_email` with query `label:em/newsletters` (or whichever label was used). Verify the test emails appear.

- [ ] **Step 4: Commit updated state**

```bash
git add state.json rules.yaml
git commit -m "feat: verify end-to-end cleanup flow"
```

---

### Task 9: Verify Cleanup from Claude Desktop

- [ ] **Step 1: Re-upload files**

Upload the latest `rules.yaml` and `state.json` to the Claude Desktop Email Manager project.

- [ ] **Step 2: Run a test cleanup**

In the Email Manager project conversation, say: "Clean my inbox — just process the last 10 emails as a test."

Verify same behavior as Claude Code: classification, labeling, digest, and state output.

- [ ] **Step 3: Verify rule management**

Ask Claude Desktop: "Add a rule for emails from noreply@example.com, label as notifications, archive."

Verify Claude outputs the updated `rules.yaml` content for re-upload.

---

### Task 10: Run Initial Cleanup (Phase 1 — Profiling)

This is the first real use of the system. Run it from whichever interface you prefer.

- [ ] **Step 1: Start profiling**

Say: "Start the initial inbox cleanup. Begin with profiling — show me my top senders by volume."

- [ ] **Step 2: Review the frequency report**

Claude will present sender domains grouped by email count. Review and make triage decisions for each group.

- [ ] **Step 3: Generate rules**

Based on your decisions, Claude generates rules and writes them to `rules.yaml` (Code) or outputs them for re-upload (Desktop).

- [ ] **Step 4: Review and approve rules**

Check the generated rules make sense before proceeding to batch classification.

- [ ] **Step 5: Commit rules**

```bash
git add rules.yaml state.json
git commit -m "feat: add initial classification rules from inbox profiling"
```

After profiling, continue with Phases 2–4 of the initial cleanup conversationally. The instructions in CLAUDE.md / desktop-instructions.md guide the process from here.
