# Calendar events from rule matches — design

**Date:** 2026-10-25
**Status:** Draft for review
**Owner:** Gethin
**Builds on:** `docs/superpowers/specs/2026-10-24-nanoclaw-inbox-processing-design.md` (the
autonomous runner on branch `nanoclaw-inbox`, PR #3)

## 1. Purpose

Rules with `action: calendar_event` currently label the email, leave it in the
inbox and count it under "needs calendar review" in the digest. Nothing is
ever put in a calendar. This design makes the runner create the event itself:
an all-day entry in Gethin's Outlook calendar on the date the email names, so
a payment-due reminder from American Express becomes "Amex Payment Due" on the
30th, and it shows on the kitchen wall through the existing calendar feed.

Reconstruction note: the original interactive `clean-inbox` skill that once
made these events is not in the repository or anywhere on the machine. The
behaviour below is a reconstruction agreed with the owner on 2026-10-25.

## 2. Scope

**In**

- A calendar step in `runner/` for every `calendar_event` match, in both
  accounts, with deterministic date extraction and Microsoft Graph event
  creation.
- Body fetch for those matches only.
- Two extractors: `pay_by` (bills) and `arriving` (Amazon orders).
- Dedupe by title + date (state record and calendar lookup).
- A one-off 14-day backfill on the first run after deployment.
- Rule-file fields `date_extractor` and `calendar_title_from`.
- Digest lines for created and skipped events; auth warning for the
  calendar scope.
- Relay scope widened to `Calendars.ReadWrite`; owner re-consents once.

**Out**

- Updating or deleting events (a changed due date creates a second event).
- Reminders/alarms on the event (owner chose all-day with no reminder).
- Google Calendar (events always go to Outlook, the calendar on the wall).
- Any LLM involvement (Approach B was rejected).
- Changes to how non-calendar rules are processed.

## 3. Rules

`rules.yaml` gains two optional fields, used only when `action: calendar_event`:

| Field | Values | Meaning |
|---|---|---|
| `date_extractor` | `pay_by` \| `arriving` | Required for `calendar_event` rules. Loading rejects a `calendar_event` rule without it or with an unknown value (`rule <n>: date_extractor is required for calendar_event`). |
| `calendar_title` | string | Event subject. Required unless `calendar_title_from` is set. |
| `calendar_title_from` | `subject` | Derive the subject from the email subject (see §5). |

The five existing rules become:

```yaml
- pattern: '*@mandsbank.com'            # existing M&S rule (sender match)
  action: calendar_event
  calendar_title: M&S Credit Card Payment Due
  date_extractor: pay_by
- pattern: '*payment*due*'              # three Amex rules, same shape
  sender_pattern: '*@*americanexpress.com'
  action: calendar_event
  calendar_title: Amex Payment Due
  date_extractor: pay_by
- pattern: '*Ordered*'
  sender_pattern: '*@amazon.co.uk'
  action: calendar_event
  calendar_title_from: subject
  date_extractor: arriving
```

(Existing `match_field`, `label`, `last_matched` lines unchanged.)

## 4. Components

```
runner/
  calendar.py        extractors, event builder, GraphCalendar, calendar_step()
  providers/base.py  Provider gains get_body(message_id) -> str
  providers/gmail.py get_body: messages.get format=full → text/plain part, else html→text
  providers/graph.py get_body: GET /me/messages/{id}?$select=body with
                     Prefer: outlook.body-content-type="text"
  policy.py          calendar_event → ops unchanged; bucket "calendar"
  run.py             after ops succeed, call calendar_step(); backfill on first run
  state.py           calendar.created[], calendar.backfilled_at; trim 90 days
  digest.py          📅 lines; calendar auth warning
relay/ms.py          SCOPES += " Calendars.ReadWrite"
```

### 4.1 `runner/calendar.py`

```python
LONDON = ZoneInfo("Europe/London")
AHEAD_DAYS = 60     # a date further ahead is a misparse
BEHIND_DAYS = 7     # a date further back is a misparse (bill already due)

@dataclass
class EventSpec:
    title: str
    date: date
    note: str          # "From: <sender> · <subject> · received 25 Oct 2026"

class NoDate(Exception): ...

def extract_pay_by(text: str, received: date) -> date      # raises NoDate
def extract_arriving(text: str, received: date) -> date    # raises NoDate
EXTRACTORS = {"pay_by": extract_pay_by, "arriving": extract_arriving}

def derive_title(rule: Rule, message: Message) -> str
def build_event(rule: Rule, message: Message, body: str) -> EventSpec

class GraphCalendar:
    def __init__(self, client: httpx.Client | None = None, sleep=time.sleep)
    def find(self, title: str, day: date) -> str | None      # event id or None
    def create_all_day(self, spec: EventSpec) -> str          # event id

@dataclass
class CalendarOutcome:
    status: Literal["created", "skipped", "review"]
    title: str | None
    date: date | None
    reason: str | None   # for "review": "no date", "date out of range", "graph 503", …

def calendar_step(rule, message, provider, calendar: GraphCalendar, state, now) -> CalendarOutcome
```

**`extract_pay_by`.** Lower-cases the text, finds the first of these phrases
in order of position: `please pay by`, `pay by`, `payment due date`,
`due date`, `due by`, `due on`. Takes the text after the phrase (and an
optional `:`/`is`/`of`), skips an optional weekday name, and parses the first
date in one of: `D Mon YYYY`, `D Month YYYY`, `DD/MM/YYYY`, `YYYY-MM-DD`,
`D Mon` / `D Month` (year = received year, or next year if that makes the date
more than 7 days before `received`). Anything else → `NoDate`. A result
outside `[received - 7 days, received + 60 days]` → `NoDate("date out of
range")`.

**`extract_arriving`.** Finds the first of `arriving`, `delivery estimate`,
`estimated delivery`, `expected delivery`, then reads the following text:
`today` → `received`; `tomorrow` → `received + 1`; `<Weekday> D Month` or
`D Month` (year optional) → that date, resolved to the first occurrence on or
after `received`; a range `A - B` / `A to B` → `A`. Same sanity window.
Anything else → `NoDate`.

**`derive_title`.** `calendar_title` when set. With `calendar_title_from:
subject`: strip a leading `Ordered:`/`Ordered` and surrounding quotes/curly
quotes and whitespace, truncate to 60 characters at a word boundary, prefix
`Amazon: ` → e.g. `Amazon: Charger Cable for Fitbit`. Empty result falls back
to the email subject.

**`GraphCalendar`.** `find`: `GET /me/calendarView?startDateTime=<day
00:00 London>&endDateTime=<next day 00:00 London>&$select=id,subject` and
match `subject == title` (case-insensitive, trimmed). `create_all_day`: `POST
/me/events` with `{"subject", "body": {"contentType": "text", "content":
note}, "start": {"dateTime": "YYYY-MM-DDT00:00:00", "timeZone":
"Europe/London"}, "end": {next day, same}, "isAllDay": true, "showAs": "free",
"isReminderOn": false}`. Both go through `request_with_retry` with the same
rate-limit handling as the mail client; a 401/403 raises `AuthError`.

**`calendar_step`.** Order: `body = provider.get_body(id)` → `spec =
build_event(...)` → if `state.calendar_has(title, date)` → `skipped
("state")`; elif `calendar.find(title, date)` → record in state, `skipped
("calendar")`; else `calendar.create_all_day(spec)`, record, `created`. Every
failure path returns `review` with a reason; `AuthError` propagates so the run
can mark calendar auth (§7). `--dry-run` performs the body fetch, extraction
and lookups but never creates; it reports `created` outcomes as
`would create`.

### 4.2 Policy and run

`policy.decide` keeps the existing ops for `calendar_event` (Gmail add label;
Outlook mark read) and sets `bucket = "calendar"`. In `run.py`, after the ops
for a message succeed, a `calendar` bucket calls `calendar_step`. Outcomes
feed the summary:

```python
calendar_created: list[dict]   # {"title", "date", "account"}
calendar_skipped: int
calendar_review: int           # unchanged meaning: needs a human
```

`last_run` advances exactly as today; a `review` outcome does not block it.

### 4.3 Backfill

On entry to `run()`, if `state.calendar.backfilled_at` is absent and the run
is not `--dry-run`: for each account with `auth == ok`, `list_since(now - 14
days, limit=500)` (headers), apply **only** `calendar_event` rules via the
normal matcher, and call `calendar_step` for each match. No labels, moves or
mark-read in this pass. Record outcomes in the same summary fields. Set
`backfilled_at = now` only when the pass finished without an exception; an
exception leaves it unset so the next run retries (dedupe makes that safe).
`--dry-run` performs the pass but never stamps.

### 4.4 State

```json
"calendar": {
  "backfilled_at": "2026-10-26T07:00:12Z",
  "created": [
    {"title": "Amex Payment Due", "date": "2026-10-30", "message_id": "AAMk…",
     "account": "outlook", "created_at": "2026-10-26T07:00:14Z"}
  ]
}
```

`created` entries older than 90 days (by `date`) are dropped on save. Keys are
compared as `(title.casefold().strip(), date)`.

### 4.5 Providers

`get_body(message_id) -> str` on both providers, plain text, whitespace
collapsed to single spaces, HTML entities unescaped. Gmail: `messages.get`
with `format=full`; prefer the first `text/plain` part (base64url), else the
`text/html` part with tags stripped; multipart walked recursively. Graph:
`GET /me/messages/{id}?$select=body` with `Prefer:
outlook.body-content-type="text"`. Bodies are never written to any file or
log; only the extracted date and the first 120 characters around the match
may appear in the run log at debug level.

### 4.6 Relay

`relay/ms.py` `SCOPES = "offline_access User.Read Mail.ReadWrite
Calendars.ReadWrite"`. The owner adds the delegated permission
`Calendars.ReadWrite` to the app registration and runs `python -m relay
authorize` once; the saved document's `scope` then includes it. Until then,
calendar calls return 403 and the digest reports it (§7).

## 5. Digest

New lines after `Processed:`, only when non-zero:

```
📅 Calendar: Amex Payment Due 30 Oct, Amazon: Charger Cable for Fitbit 21 Oct
📅 Already in calendar: 1
🕒 Needs calendar review: 1
```

Dates render as `D Mon`. The verdict rule adds "any `calendar_created`" to
the REPORT conditions. On `--dry-run` the first line reads `📅 Would create:`.

## 6. Timezone and all-day boundaries

All dates are London calendar dates. The Graph event uses `timeZone:
"Europe/London"` on both ends with `isAllDay: true`, so the event covers the
right civil day across the clock change.

## 7. Error handling

- Per message: body fetch error, `NoDate`, out-of-range date, Graph
  non-auth error → `review` with reason; the mail ops already applied stand.
- Calendar `AuthError` (401/403): mark `summary.auth["calendar"] =
  "unauthorized"` once, stop further calendar calls this run (every remaining
  calendar match becomes `review`), keep processing mail. Digest line
  `⚠️ Calendar needs re-authorising: add Calendars.ReadWrite and run
  python -m relay authorize`, suppressed to once per six hours via the
  existing `last_reported` mechanism (condition `calendar_unauthorized`).
- Backfill exception: logged, stamp not written, run continues with the
  normal window.
- Rule-file validation error: as today, the run refuses to start.
- Nothing in this step deletes, moves or edits events.

## 8. Testing

Offline, `pytest tests -q`, respx for HTTP. New tests in `tests/runner/`:

- `test_calendar_extract.py`: Amex body (recorded text, "Please pay by: 30
  Oct 2026") → 2026-10-30; `30 October 2026`, `30/10/2026`, `2026-10-30`,
  `Friday 30 Oct 2026`; `30 Oct` without year across a year boundary; no
  phrase → `NoDate`; 61 days ahead and 8 days behind → `NoDate`; `Arriving
  today`, `Arriving tomorrow`, `Arriving Tuesday 21 October`, `Arriving 21
  October - 23 October` → first day; resolution forward from a received date
  late in the year.
- `test_calendar_event.py`: `derive_title` for Amazon subjects with straight
  and curly quotes and a 60-char cut; `build_event` note text; Graph payload
  (`isAllDay`, London zone, `showAs` free, reminder off, end = next day).
- `test_calendar_step.py`: created / skipped-state / skipped-calendar /
  review-no-date / review-graph-500 / auth-error paths; dry-run never POSTs.
- `test_backfill.py`: first run lists 14 days, applies only calendar rules,
  performs no mail ops, stamps on success, does not stamp on exception, does
  not stamp on dry run, never runs twice.
- `test_providers_body.py`: Gmail text part, HTML-only part, nested
  multipart; Graph text preference header.
- `test_digest.py` additions: 📅 lines, dry-run wording, auth warning and its
  suppression.
- `test_rules.py` additions: `date_extractor` required/unknown; both title
  fields.

Live on Ubuntu, recorded in the ledger: relay re-consent shows the scope;
`run.sh --dry-run` prints `📅 Would create: Amex Payment Due 30 Oct`; the
real run creates it; Outlook shows the event; the wall's calendar card shows
it after the next feed refresh.

## 9. Assumptions to validate before the plan

| # | Assumption | Check |
|---|---|---|
| A1 | Amex's body contains "Please pay by: D Mon YYYY" | Verified 2026-10-25 from the live email. |
| A2 | Amazon "Ordered" bodies contain "Arriving <day>" or a delivery estimate line | Spike on the next Ordered email body (only dispatch/delivery bodies were read on 2026-10-25). |
| A3 | Graph `calendarView` with London-zone bounds and `POST /me/events` with `isAllDay` work through the OneCLI proxy with the relay token | Spike after re-consent, one call each. |
| A4 | Gmail `messages.get format=full` is allowed by the existing OneCLI Gmail connection scopes | Spike inside the container. |
| A5 | The owner can add `Calendars.ReadWrite` to the app registration | Owner action. |

## 10. Owner actions

1. Azure portal → app registration `fd8c524f-…` → API permissions → add
   delegated `Calendars.ReadWrite`.
2. When told: in Ubuntu, `cd ~/email-manager && .venv-host/bin/python -m
   relay authorize`, sign in, approve.
