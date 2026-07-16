# Autonomous (Always-On) Email Processing

This document defines how the email manager processes new mail **unattended**, on a
schedule, with **no human present to confirm actions**. It is the always-on counterpart
to the interactive `clean-inbox` workflow.

A scheduler on your machine (see [Scheduling](#scheduling)) invokes Claude Code headlessly
every ~15 minutes. Each run processes mail that arrived since the last run, applies only
**safe** actions, and sends you a short digest over WhatsApp.

> **Trigger:** a run is autonomous when the invoking prompt says so — the scheduler scripts
> pass `Run the autonomous email processing workflow (autonomous-processing.md).` Treat any
> such run as non-interactive: **never ask the user a question; if something needs a
> decision, skip it and report it in the digest.**

---

## Guardrails (what makes this safe to run unattended)

1. **Conservative actions only.** Apply `keep`, `archive`, and labelling from *existing*
   rules. **Do not delete or trash anything.** A rule whose `action` is `trash` is
   downgraded: apply its label and additionally label the message `em/held`, but leave it
   in the inbox for you to review. Deletions only ever happen in an interactive session.
2. **Existing rules only.** Do not create, edit, or infer new rules. Do not run the
   Initial Cleanup / profiling phases. If a message matches no rule, label it `em/review`
   (Gmail) or mark it read and leave it in place (Outlook) — never guess a destructive action.
3. **No calendar side effects by default.** `action: calendar_event` rules are treated as
   `keep` in autonomous mode (label + leave in inbox) and listed in the digest under
   "needs calendar review". Calendar/WhatsApp session sync stays an interactive task.
4. **Incremental only.** Process just the window since `last_run`. Never re-sweep the
   whole mailbox.
5. **Fail safe and quiet-loud.** On any error (auth expired, MCP down, rule file unreadable),
   stop, make no further changes, and send a single WhatsApp alert describing what failed.
   Do not retry destructively.
6. **Idempotent.** If a run is interrupted, the next run must not double-process. Advance
   `last_run` only after actions for that window succeed.

---

## Preconditions (check first; bail cleanly if unmet)

Run these checks before touching any mail. If a check fails, send a WhatsApp alert and stop.

1. **Rules & state readable.** `rules.yaml` parses and `state.json` exists.
2. **Initial cleanup complete.** For each target account, `accounts.<provider>.initial_cleanup.status`
   must be `completed`. If not, **do not** run initial cleanup autonomously — send a WhatsApp
   note ("Gmail initial cleanup not done — run it interactively") and skip that account.
3. **Auth.**
   - Gmail: auto-auth; assume available (a tool error surfaces as a failure → alert).
   - Outlook: call `mcp__outlook-manager__check-auth-status`. If not authenticated, skip
     Outlook this run and note it in the digest. **Never** launch the interactive browser
     auth flow in an unattended run.
   - WhatsApp (for the digest): call `whatsapp-auth-status`. If not authenticated, fall back
     to writing the digest to `last-run-summary.md` only, and note that WhatsApp was skipped.

---

## Workflow

For each target account (default: both Gmail and Outlook; the scheduler may narrow this):

1. **Read state** — load `state.json`; read `accounts.<provider>.last_run`.
2. **Read rules** — load `rules.yaml`.
3. **Fetch new mail since `last_run`:**
   - Gmail: `search_emails` with `query: "after:YYYY/MM/DD"` (use the `last_run` date; also
     filter client-side to messages strictly newer than the `last_run` timestamp to avoid
     re-processing same-day mail).
   - Outlook: `search-emails` with `query: "received:>=YYYY/MM/DD"`, `count: 200`, then
     filter to unread and to `receivedDateTime > last_run`.
4. **Classify** each message by applying `rules.yaml` in order, first match wins
   (`match_field: sender` / `subject`; skip `header` rules for Outlook).
5. **Map the matched action through the conservative policy:**

   | Rule action | Autonomous behaviour (Gmail) | Autonomous behaviour (Outlook) |
   |---|---|---|
   | `keep` | add `em/<category>` label | `mark-as-read`, leave in Inbox |
   | `archive` | add label, remove `INBOX` | `move-emails` → `em-<category>` folder |
   | `trash` | add label **+ `em/held`**, leave in Inbox (no delete) | add nothing, `mark-as-read`, leave in Inbox (no move) — list under "held" |
   | `calendar_event` | add label, leave in Inbox, list under "needs calendar review" | `mark-as-read`, leave in Inbox, list under "needs calendar review" |
   | unmatched | add `em/review` label | `mark-as-read`, leave in Inbox |

6. **Retention/auto-archive is skipped** in autonomous mode (it can move aged mail toward
   deletion). Leave retention sweeps to interactive runs.
7. **Update state** — set `accounts.<provider>.last_run` to the run's start timestamp (ISO)
   only after step 5 succeeds for that account. Update `autonomous.last_run` too (see schema).
8. **Send the digest** (see below).

---

## Digest (WhatsApp)

At the end of the run, build a compact plain-text digest and send it with
`whatsapp-send-message` (no `to`/`chatName` → it goes to your own *Message Yourself* chat).
Keep it short — this is a phone notification, not a report.

Only send if there is something worth reporting: new important mail, anything held for
review, or an error. If a run processed only routine archives/keeps with nothing important,
send nothing (or a once-daily heartbeat if `autonomous.heartbeat` is enabled).

Format:

```
📬 Email run HH:MM
Processed: 12 (Gmail 9, Outlook 3)
⭐ Important: 2
  • First Direct — "Payment due 20th"
  • HMRC — "Message in your account"
🕒 Needs calendar review: 1 (Amex payment due)
✋ Held for review (would-be trash): 3
📝 Review (unmatched): 1
```

- List every `em/important` item with sender + subject (read only headers/metadata;
  respect `settings.privacy_mode`).
- Summarise held/review/calendar as counts, optionally with one line each if few.
- On failure, send instead: `⚠️ Email run failed at HH:MM: <reason>. No changes made.`

Always also append the same digest to `last-run-summary.md` (gitignored) as a durable log.

---

## state.json additions

Autonomous mode adds an `autonomous` block alongside the existing `accounts` block. Example:

```json
{
  "accounts": {
    "gmail":   { "last_run": "2026-07-16T09:00:00Z", "initial_cleanup": { "status": "completed", "emails_processed": 1800 } },
    "outlook": { "last_run": "2026-07-16T09:00:00Z", "initial_cleanup": { "status": "completed", "emails_processed": 400 } }
  },
  "autonomous": {
    "enabled": true,
    "accounts": ["gmail", "outlook"],
    "last_run": "2026-07-16T09:00:00Z",
    "digest": { "channel": "whatsapp", "target": "self" },
    "heartbeat": false
  }
}
```

- `autonomous.accounts` — which accounts the scheduled run processes.
- `autonomous.digest.target` — `"self"` (default, messages yourself), a group `chatName`,
  or a phone number. Kept in `state.json` (gitignored) so no personal number is committed.
- `autonomous.heartbeat` — if `true`, send a digest even on an empty run so you know it's alive.

`state.json` is gitignored (it holds personal timestamps). Nothing here is committed.

---

## Scheduling

The scheduler lives on **your machine**, where the MCP servers and OAuth credentials are.
It invokes Claude Code headlessly. Wrapper scripts are in `scripts/`:

- **Windows** — `scripts/autonomous-run.ps1`; register it with `scripts/register-task-windows.ps1`
  (creates a Task Scheduler job that runs every 15 minutes).
- **macOS/Linux** — `scripts/autonomous-run.sh`; schedule with cron:

  ```cron
  */15 * * * * /path/to/email-manager/scripts/autonomous-run.sh >> /path/to/email-manager/autonomous.log 2>&1
  ```

Each wrapper `cd`s into the repo and runs Claude Code in headless/print mode with the
autonomous trigger prompt. Because the run must call MCP tools without a human to approve
them, the scripts pre-authorise tools — either via `--dangerously-skip-permissions` (simple,
fine for a trusted personal machine, and safe here because autonomous mode never deletes)
or via an allowlist in `.claude/settings.json`. See the script comments for the safer
allowlist variant.

**Only runs while your machine is on.** For true 24/7 you'd host the MCP servers + tokens on
an always-on box and schedule from there — out of scope for this local setup.
