# Multi-Account Email Manager Design

## Summary

Extend the existing email manager to support both Gmail and Outlook (Hotmail) accounts using a single shared rule set. The system is prompt-driven via `CLAUDE.md` instructions, with no application code. Both accounts are managed through their respective MCP servers.

## Goals

- Apply the same `rules.yaml` rules to both Gmail and Outlook inboxes
- Support account-targeting: "clean my inbox" (both), "clean my gmail", "clean my outlook"
- Run the full initial cleanup process for Outlook (profiling, rule generation, batch classification)
- Produce a combined digest covering both accounts

## Non-Goals

- Server-side Outlook rules (limited matching, no LLM classification)
- Programmatic cleanup scripts (unnecessary given the prompt-driven approach)
- Separate rule files per account

## Data Model

### state.json

Migrate from flat structure to per-account tracking:

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

#### Migration

If `state.json` has a flat `last_run` field (no `accounts` key), migrate it:
1. Move existing `last_run` and `initial_cleanup` into `accounts.gmail`
2. Initialize `accounts.outlook` with `last_run: null` and `initial_cleanup.status: "not_started"`

### rules.yaml

No changes. The existing shared rule file applies to both accounts. Rules are provider-agnostic (sender patterns, subject patterns, header checks).

Note: Rules with `label: em/spam` are valid. On Gmail, the `em/spam` label is applied alongside the trash action. On Outlook, only the trash action applies (move to Deleted Items) -- the `em/spam` label is ignored since there is no corresponding folder.

Note: Rules with `match_field: header` are skipped for Outlook, since the Outlook MCP `read-email` tool does not expose raw email headers. Only `match_field: sender` and `match_field: subject` rules apply to Outlook.

## Outlook Folder Structure

Create these top-level folders in Outlook on first run:

- `em-important`
- `em-newsletters`
- `em-notifications`
- `em-promotions`
- `em-receipts`

**Flat folder naming.** The Outlook MCP server's `getFolderIdByName` only searches top-level folders (`me/mailFolders`). Child folders under a parent cannot be resolved by `move-emails`. Therefore, use flat top-level folder names with `-` separator instead of nested `em/` hierarchy. The label-to-folder mapping is: `em/important` -> `em-important`, etc.

No `em-spam` folder -- trash-action emails go to Deleted Items.
No `em-review` folder -- unmatched emails stay in Inbox.

### Folder Creation Strategy

Outlook's `create-folder` is not idempotent (unlike Gmail's `get_or_create_label`). Before creating folders:
1. Call `list-folders` to get existing top-level folders
2. Compare display names against the required folder list
3. Only call `create-folder` for folders that don't already exist

### Trash Folder Reference

The Outlook MCP server's `move-emails` resolves folder names via `getFolderIdByName`, which searches by display name. The Deleted Items folder's display name is `Deleted Items`. Use `targetFolder: "Deleted Items"` for trash actions.

## Action Mapping

### Gmail (existing, unchanged)

| Action | Gmail behavior |
|--------|---------------|
| keep | Add `em/<category>` label, leave in Inbox |
| archive | Add `em/<category>` label, remove from Inbox |
| trash | Add TRASH label, add `em/<category>` label, remove from Inbox |
| unmatched | Add `em/review` label |

### Outlook (new)

| Action | Outlook behavior |
|--------|-----------------|
| keep | Leave in Inbox, mark as read |
| archive | Move to `em-<category>` folder |
| trash | Move to `Deleted Items` |
| unmatched | Leave in Inbox, mark as read |

Key difference: Gmail labels are additive (email can have a label and stay in Inbox). Outlook folders are exclusive (moving removes from Inbox). Therefore "keep" on Outlook means no folder move.

### Tracking Processed Emails (Outlook)

For "keep" and "unmatched" actions, emails stay in Inbox with no folder move or label. To avoid re-processing on subsequent runs:
- Use `mark-as-read` to mark all processed emails as read
- On subsequent runs, fetch emails using date filtering AND only process unread emails (check `isRead` status from search results)
- The `search-emails` tool returns `isRead` status per email; filter client-side for unread only when not using the `unreadOnly` parameter (since `unreadOnly` uses KQL when combined with `query`, which may behave differently from OData filtering)

Note: `mark-as-read` processes one email at a time (no batch endpoint). For large runs this means many sequential calls. This is an acceptable trade-off given the prompt-driven nature of the system.

## Outlook MCP Tool Mapping

| Operation | Gmail tool | Outlook tool |
|-----------|-----------|--------------|
| Search emails | `mcp__gmail-manager__search_emails` | `mcp__outlook-manager__search-emails` |
| Read email | `mcp__gmail-manager__read_email` | `mcp__outlook-manager__read-email` |
| List folders/labels | `mcp__gmail-manager__list_email_labels` | `mcp__outlook-manager__list-folders` |
| Create folder/label | `mcp__gmail-manager__get_or_create_label` | `mcp__outlook-manager__create-folder` |
| Move/classify | `mcp__gmail-manager__modify_email` | `mcp__outlook-manager__move-emails` |
| Bulk classify | `mcp__gmail-manager__batch_modify_emails` | `mcp__outlook-manager__move-emails` (comma-separated IDs) |
| Mark read | N/A | `mcp__outlook-manager__mark-as-read` |

### Outlook Tool Details

**search-emails**: Uses KQL (Keyword Query Language) via the `query` parameter. Date filtering: `query: "received:>=2026/03/01"`. The MCP server internally paginates via `callGraphAPIPaginated` -- despite the schema declaring `max: 50` for `count`, the implementation accepts higher values and pages through results in batches of 50. For cleanup, use `count: 200` or higher. Note: when `$search` is used (i.e., when `query` is provided), results are returned in relevance order, not chronological. Sort client-side by `receivedDateTime` if ordering matters.

**move-emails**: Takes `emailIds` as a comma-separated string (not an array). Emails are moved one at a time internally. Batch in groups of ~50 IDs per call.

**read-email**: Returns formatted text with From, To, Subject, Date, body. The underlying API fetches `internetMessageHeaders` but the MCP tool does not expose them in output. Classification must rely on From address, Subject, and body content -- not raw headers like `List-Unsubscribe` or `Precedence`.

**list-emails**: Lists emails in a folder ordered by `receivedDateTime desc`. Same pagination behavior as `search-emails` (schema says max 50, implementation pages beyond). Use for retention checks where date ordering is important.

## Heuristic Adjustments for Outlook

The Gmail built-in heuristics rely on email headers (`List-Unsubscribe`, `Precedence: bulk`). Since Outlook's `read-email` does not expose raw headers, adjust heuristics:

- `List-Unsubscribe` header check -> skip for Outlook. Instead, rely on sender pattern matching and body content clues (e.g., "unsubscribe" link text in body).
- `Precedence: bulk` header check -> skip for Outlook. Rely on sender patterns and LLM classification.
- Order/receipt/confirmation subject patterns -> same for both (subject-based, works on Outlook).

## Authentication

Before any Outlook operations, check auth status:
1. Call `mcp__outlook-manager__check-auth-status`
2. If not authenticated, call `mcp__outlook-manager__authenticate` and guide user through browser flow
3. Proceed only after auth is confirmed

Gmail uses auto-auth and does not need this step.

Note: The Outlook MCP server handles token refresh automatically via `ensureAuthenticated()`. Long-running cleanup operations are resilient to token expiration -- the server will refresh the token transparently when needed.

## Cleanup Workflow Changes

### Account Targeting

- "Clean my inbox" / "clean my email" -> process both accounts sequentially (Gmail first, then Outlook)
- "Clean my gmail" -> process Gmail only
- "Clean my outlook" / "clean my hotmail" -> process Outlook only

### Per-Account Flow

For each target account:

1. (Outlook only) Check authentication status; prompt if needed
2. Read `state.json` under `accounts.<provider>`
3. If `initial_cleanup.status` is `not_started`, run the full 4-phase initial cleanup
4. Otherwise, run the standard cleanup workflow using `last_run` timestamp
5. Update `state.json` for that account when done

### Standard Cleanup (Outlook)

1. Read `last_run` from `state.json` accounts.outlook
2. Fetch new emails: `search-emails` with `query: "received:>=YYYY/MM/DD"` from `last_run` date, `count: 200`
3. Filter results to unread emails only (check `isRead` field from search results)
4. Classify each email by applying rules from `rules.yaml` (first match wins; skip `match_field: header` rules)
5. Apply heuristics to unmatched emails (adjusted for Outlook -- see Heuristic Adjustments)
6. Use LLM judgment for remaining unmatched emails (read body if needed, respect `privacy_mode`)
7. Execute actions:
   - `keep` -> `mark-as-read`
   - `archive` -> `move-emails` to `em-<category>` folder (e.g., `em-newsletters`)
   - `trash` -> `move-emails` to `Deleted Items`
   - `unmatched` -> `mark-as-read` (stays in Inbox for manual review)
8. Run retention: for each label with `retention_days`, use `list-emails` on that `em-*` folder with high count, filter by `receivedDateTime` for emails older than retention period, move aged emails to `Deleted Items`
9. Update `state.json` with current timestamp

### Initial Cleanup (Outlook)

Same 4-phase process as Gmail:

1. **Profiling** -- fetch email headers in batches using `search-emails` with high `count`, group by sender domain, present frequency report
2. **Rule Generation** -- generate rules from triage decisions, add to shared `rules.yaml`
3. **Batch Classification** -- apply rules to existing emails using `move-emails` in batches of ~50 IDs
4. **LLM Escalation** -- process unmatched emails, suggest new rules

### Folder Setup (Outlook first run)

Before any classification:
1. Call `list-folders` to get existing top-level folders
2. For each required folder (`em-important`, `em-newsletters`, `em-notifications`, `em-promotions`, `em-receipts`):
   - Check if it already exists by display name
   - If not, call `create-folder` with that name

### Retention (Outlook)

For labels with `retention_days` set in `rules.yaml` settings:
1. Use `list-emails` with `folder: "em-<category>"` and high count to get all emails in the folder
2. Filter results client-side by `receivedDateTime` to find emails older than the retention period
3. Move aged emails to `Deleted Items` using `move-emails`

## Digest Format

Combined digest covering both accounts:

### Important Emails

#### Gmail
- (list of important Gmail emails)

#### Outlook
- (list of important Outlook emails)

### Operational Summary

| | Gmail | Outlook | Total |
|---|---|---|---|
| Processed | X | Y | X+Y |
| em/important | ... | ... | ... |
| Archived | ... | ... | ... |
| Trashed | ... | ... | ... |
| Review | ... | ... | ... |

## CLAUDE.md Changes

The `CLAUDE.md` file needs the following updates:

1. **MCP Server section**: Add Outlook MCP tool reference with operation mapping table
2. **Cleanup Workflow section**: Update to iterate over target accounts, dispatch to correct MCP tools per provider
3. **Account targeting section**: New section defining how to parse user intent ("clean my inbox" vs "clean my gmail")
4. **State management**: Update state read/write instructions to use `accounts.<provider>` paths; include migration logic for flat -> nested state
5. **Outlook-specific operations**: Document folder-based actions vs Gmail label-based actions, flat folder naming convention, authentication check, heuristic adjustments, header rule skipping
6. **Initial cleanup**: Note that Outlook follows the same 4-phase process, using folder moves instead of label ops; document folder setup procedure
7. **Digest format**: Update to combined multi-account format

## Safety

- Same safety rules apply: never permanently delete, confirm before bulk operations
- Outlook "trash" moves to Deleted Items (recoverable), not permanent deletion
- Initial cleanup for Outlook requires user confirmation at each phase, same as Gmail
- Check authentication before Outlook operations to avoid silent failures
