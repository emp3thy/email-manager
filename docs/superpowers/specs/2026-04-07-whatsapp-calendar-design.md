# WhatsApp-to-Calendar Integration

## Overview

Add a WhatsApp MCP server to the email manager that reads messages from a personal training group chat, extracts upcoming weights session dates/times, and creates Outlook calendar events. Runs as part of the existing inbox cleaning workflow.

## Context

- Personal trainer Hanna schedules sessions via a WhatsApp group chat "gethin & helen pt"
- Messages are conversational — proposals, confirmations, cancellations, invoices, photos mixed together
- Sessions are 1 hour, typically weekday mornings
- Two participants: Gethin and Helen. Some sessions are joint, some individual.

## WhatsApp MCP Server

New Node.js MCP server at `whatsapp-mcp/`, following the same structure as `outlook-mcp/`.

### Dependencies

- `@whiskeysockets/baileys` — WhatsApp Web API
- `@modelcontextprotocol/sdk` — MCP server framework (same as outlook-mcp)

### Tools

| Tool | Parameters | Returns |
|------|-----------|---------|
| `whatsapp-auth-status` | none | `{ authenticated: bool, phone: string? }` |
| `whatsapp-authenticate` | none | QR code for terminal display; blocks until scanned |
| `whatsapp-list-chats` | `limit?: number` | Array of `{ name, id, lastMessage, timestamp }` |
| `whatsapp-get-messages` | `chatName: string, since?: ISO8601` | Array of `{ sender, timestamp, text, mediaType? }` |

### Auth Storage

- Session credentials persisted in `whatsapp-mcp/auth-store/` (gitignored)
- Baileys handles automatic reconnection when credentials are valid
- If credentials expire, `whatsapp-auth-status` returns `authenticated: false` and the user must re-authenticate via `whatsapp-authenticate`

### Message Filtering

- `whatsapp-get-messages` accepts a `since` parameter to limit messages to those after a given timestamp
- Media messages (images, PDFs, voice notes) are returned as metadata only (type + caption if present), not downloaded
- Text messages returned with sender name/number, timestamp, and full body

## Clean-Inbox Skill Integration

New phase added after email processing:

1. **Check auth** — call `whatsapp-auth-status`. If not authenticated, log a warning and skip. Do not block email cleaning.
2. **Fetch messages** — call `whatsapp-get-messages` for "gethin & helen pt" chat, using `last_run - 5 days` as the `since` filter. The 5-day lookback provides conversational context so Claude can detect cancellations and rescheduling that reference sessions proposed in earlier messages.
3. **Extract sessions** — Claude reads the returned messages and identifies weights sessions. For each session, extracts:
   - Date
   - Time
   - Who it's for: Gethin, Helen, or both
   - Whether it's confirmed, cancelled, or rescheduled (based on conversation context)
4. **Deduplicate** — for each confirmed session:
   - Check `state.json` `created_events` array for a matching date/time
   - Check Outlook calendar via `list-events` for an existing event with subject matching "Weights session" at that date/time
   - Skip if found in either
5. **Handle cancellations** — for each cancelled session:
   - Check `state.json` `created_events` for a matching date/time that was previously created
   - If found, delete the Outlook calendar event via `outlook-manager delete-event`
   - Remove from `created_events` in `state.json`
   - Include in run summary: "Deleted: Weights session (Gethin) on 2026-04-09 at 09:00 — cancelled in chat"
6. **Create events** — call `outlook-manager create-event` for each new confirmed session
7. **Update state** — add created events to `state.json`, update whatsapp `last_run`

## State Management

`state.json` gains a new top-level `whatsapp` key:

```json
{
  "accounts": { "..." : "..." },
  "whatsapp": {
    "last_run": "2026-04-07T10:00:00Z",
    "chat_name": "gethin & helen pt",
    "created_events": [
      {
        "date": "2026-04-09",
        "time": "09:00",
        "for": ["gethin", "helen"],
        "outlook_event_id": "AAMk...",
        "created_at": "2026-04-07T10:00:00Z"
      }
    ]
  }
}
```

- `last_run` used as `since` filter for message retrieval
- `created_events` serves as the dedup ledger
- Past events (date before today) are pruned on each run to keep the list short

## Calendar Event Format

- **Subject:** `Weights session (Gethin)`, `Weights session (Helen)`, or `Weights session (Gethin & Helen)`
- **Start:** extracted date and time in ISO 8601
- **End:** start + 1 hour
- **Body:** `Scheduled by Hanna via WhatsApp`
- **No attendees** — calendar blocks only

## Session Extraction Rules

Claude parses Hanna's messages using these guidelines:

1. Only extract sessions from messages sent by Hanna (identified by her phone number +44 7877 777748 or name)
2. Look for date + time combinations (e.g. "Wednesday the 11th at 9", "Monday the 16th at 8:15")
3. Track who each session is for — look for name indicators (G/Gethin, H/Helen, "you both")
4. If a session is later cancelled or rescheduled in a subsequent message, use the final state
5. Only create events for future dates (relative to today)
6. When a time has no AM/PM qualifier, assume AM for times before 12 (consistent with morning training pattern)

## Error Handling

- WhatsApp auth failure: warn and skip, continue with email cleaning
- Chat not found: warn and skip
- Outlook calendar API failure: report which events failed, continue with remaining
- Ambiguous session data: skip and report to user for manual handling

## Out of Scope

- Sending WhatsApp messages (read-only)
- Processing media attachments (invoices, photos)
- Sending WhatsApp replies or confirmations
- Other WhatsApp chats
