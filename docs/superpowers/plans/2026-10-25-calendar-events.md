# Calendar Events from Rule Matches — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The autonomous runner turns `calendar_event` rule matches into all-day events in Gethin's Outlook calendar, deduped, with a one-off 14-day backfill.

**Architecture:** A new `runner/calendar.py` holds two deterministic date extractors, an event builder and a small Graph calendar client. `run.py` calls a `calendar_step` after a match's mail operations succeed, and runs a once-only backfill at entry. Providers gain `get_body` and `list_calendar_candidates`. State records created events; the digest reports them. Nothing deletes or edits events.

**Tech Stack:** Python ≥ 3.12 (Windows 3.13 tests, Ubuntu 3.14 runtime), httpx, pyyaml, python-dateutil (already in `requirements-runner.txt`), pytest + respx. Microsoft Graph v1.0 via the OneCLI proxy with the relay token.

**Spec:** `docs/superpowers/specs/2026-10-25-calendar-events-design.md`

## Global Constraints

- Events always go to the Outlook calendar (`/me/events` with the relay token), whichever account the email came from; never Google Calendar.
- All-day event: `isAllDay: true`, `start`/`end` as `YYYY-MM-DDT00:00:00` with `timeZone: "Europe/London"`, end = next day, `showAs: "free"`, `isReminderOn: false`, body text `From: <sender display> · <subject> · received D Mon YYYY`.
- Extractors: `pay_by` phrases in order `please pay by`, `pay by`, `payment due date`, `due date`, `due by`, `due on`; `arriving` phrases `arriving`, `delivery estimate`, `estimated delivery`, `expected delivery`. Sanity window `[received − 7 days, received + 60 days]`; outside → `NoDate("date out of range")`.
- Dedupe key `(title.casefold().strip(), date)`; check state first, then the calendar (`calendarView` for that London day, subject match case-insensitive trimmed).
- Bodies fetched only for `calendar_event` matches; never written to files or logs (debug log may hold the extracted date and ≤120 chars around the match).
- `calendar_event` rules require `date_extractor` ∈ {`pay_by`, `arriving`} and either `calendar_title` or `calendar_title_from: subject`; loading refuses otherwise with `rule #N: …`.
- Backfill once: lists the last 14 days **ignoring read/label state**, applies only `calendar_event` rules, performs no mail ops, stamps `calendar.backfilled_at` only on a clean pass; `--dry-run` never stamps.
- Calendar 401/403 → `summary.auth["calendar"] = "unauthorized"`, no further calendar calls that run, digest line `⚠️ Calendar needs re-authorising: add Calendars.ReadWrite and run python -m relay authorize`, suppressed to once per 6 h under condition `calendar_unauthorized`.
- Digest lines after `Processed:` when non-zero: `📅 Calendar: <title> D Mon, …` (`📅 Would create:` on dry run), `📅 Already in calendar: N`, `🕒 Needs calendar review: N`. Any created event makes the verdict `REPORT`.
- `last_run` semantics unchanged; a `review` outcome never blocks advancing.
- Relay `SCOPES = "offline_access User.Read Mail.ReadWrite Calendars.ReadWrite"`.
- Tests: `.venv-dev\Scripts\python -m pytest tests -q` on Windows, offline, pristine output (currently 107 passed).
- Branch: work on `calendar-events` cut from `nanoclaw-inbox` (PR #3's branch, which holds the runner); spec and plan live there too.

## Review Focus

1. Amex sends "payment due" and "minimum payment" mails for the same cycle on different days → one event. (Task 6 `test_second_reminder_same_cycle_is_skipped`)
2. "Please pay by: 5 Jan" received on 28 Dec → 5 Jan of the next year, not a rejected past date. (Task 2 `test_pay_by_without_year_rolls_into_next_year`)
3. An HTML-only Gmail body with entities and tags (`&nbsp;`, `<b>30 Oct 2026</b>`) still yields the date. (Task 4 `test_gmail_get_body_html_only_part`)
4. A 429 on the create call must not produce two events: retries happen only before a 2xx, and a create that ultimately fails leaves the message in review with nothing recorded. (Task 3 `test_create_all_day_retries_on_429_then_succeeds_once`, Task 6 `test_create_failure_records_nothing`)
5. The backfill must find the Amex mail even though the earlier run already labelled it (Gmail) or marked it read (Outlook). (Task 7 `test_backfill_lists_processed_mail_too`)

## Assumptions and confidence

| Task | Confidence | Residual risk |
|------|-----------|---------------|
| 1, 2, 5, 8 | 95 % | None material; formats verified from live mail on 2026-10-25. |
| 3 | 90 % | Graph `calendarView` parameter shape and `isAllDay` payload are from the v1.0 reference; the live create is first exercised in Task 9 after re-consent. |
| 4 | 92 % | Gmail `format=full` part layout (base64url `body.data`, nested `parts`) is standard; nested multipart fixture covers it. |
| 6, 7 | 92 % | Integration with `run()`'s existing loop; FakeProvider extended for tests. |
| 9 | 88 % | Needs the owner's Azure permission and re-consent; Graph may require `Calendars.ReadWrite` consent to propagate for a minute. |

---

## File structure

```
runner/calendar.py             NoDate, extract_pay_by, extract_arriving, EXTRACTORS,
                               derive_title, build_event, EventSpec, GraphCalendar,
                               CalendarOutcome, calendar_step
runner/rules.py                Rule gains date_extractor, calendar_title, calendar_title_from
runner/providers/base.py       Provider protocol gains get_body, list_calendar_candidates
runner/providers/gmail.py      get_body, list_calendar_candidates
runner/providers/graph.py      get_body, list_calendar_candidates
runner/policy.py               bucket "calendar"
runner/state.py                calendar_has / calendar_add / calendar_backfilled_at / mark_backfilled
runner/run.py                  Summary fields; calendar step; backfill
runner/digest.py               📅 lines; calendar auth warning; verdict
relay/ms.py                    SCOPES
rules.yaml                     five rules updated
README.md                      calendar section
tests/runner/fakes.py          FakeProvider.get_body / bodies / list_calendar_candidates; FakeCalendar
tests/runner/test_calendar_extract.py  test_calendar_event.py  test_calendar_step.py
tests/runner/test_backfill.py  test_providers_body.py  (+ additions to test_rules/test_digest/test_state)
```

---

### Task 1: Rule fields

**Files:**
- Modify: `runner/rules.py` (Rule dataclass, `load_rules` validation), `rules.yaml` (the five `calendar_event` rules)
- Test: `tests/runner/test_rules.py`

**Interfaces:**
- Produces: `Rule.date_extractor: str | None = None`, `Rule.calendar_title: str | None = None`, `Rule.calendar_title_from: str | None = None`; `EXTRACTOR_NAMES = ("pay_by", "arriving")`.
- Validation (in `load_rules`, same `RulesError` style): for `action == "calendar_event"`: missing/unknown `date_extractor` → `"{path}: rule #{i+1} needs date_extractor (pay_by|arriving)"`; neither `calendar_title` nor `calendar_title_from` → `"{path}: rule #{i+1} needs calendar_title or calendar_title_from"`; `calendar_title_from` not `"subject"` → `"{path}: rule #{i+1} has unknown calendar_title_from {value!r}"`. Non-calendar rules ignore the three fields.

- [ ] **Step 1: Write the failing tests**

```python
def test_calendar_rule_loads_fields(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("rules:\n- pattern: '*payment*due*'\n  match_field: subject\n  label: em/important\n  action: calendar_event\n  calendar_title: Amex Payment Due\n  date_extractor: pay_by\n", encoding="utf-8")
    r = load_rules(p)[0]
    assert (r.date_extractor, r.calendar_title, r.calendar_title_from) == ("pay_by", "Amex Payment Due", None)

def test_calendar_rule_requires_extractor(tmp_path):
    ... same without date_extractor → pytest.raises(RulesError, match="rule #1 needs date_extractor")

def test_calendar_rule_requires_a_title(tmp_path):
    ... with date_extractor: arriving but no title fields → match="needs calendar_title or calendar_title_from"

def test_calendar_rule_title_from_subject(tmp_path):
    ... calendar_title_from: subject → r.calendar_title_from == "subject"

def test_calendar_rule_unknown_title_from(tmp_path):
    ... calendar_title_from: body → match="unknown calendar_title_from"

def test_real_rules_file_loads():
    rules = load_rules(Path(__file__).parents[2] / "rules.yaml")
    cal = [r for r in rules if r.action == "calendar_event"]
    assert len(cal) == 5 and all(r.date_extractor in ("pay_by", "arriving") for r in cal)
```

- [ ] **Step 2: Run** `pytest tests/runner/test_rules.py -q` — Expected: new tests FAIL (`TypeError: unexpected keyword` / no raise / real file: `RulesError` or missing fields).
- [ ] **Step 3: Implement the fields and validation; update `rules.yaml`:** M&S rule and the three Amex rules get `date_extractor: pay_by`; the Amazon `*Ordered*` rule gets `calendar_title_from: subject` and `date_extractor: arriving`.
- [ ] **Step 4: Run** `pytest tests/runner/test_rules.py -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git add runner/rules.py rules.yaml tests/runner/test_rules.py && git commit -m "feat(rules): calendar_event rules carry a date extractor and a title source"`

---

### Task 2: Date extractors

**Files:**
- Create: `runner/calendar.py` (extractors only in this task), `tests/runner/test_calendar_extract.py`, `tests/runner/fixtures/amex_body.txt`

**Interfaces:**
- Produces: `LONDON = ZoneInfo("Europe/London")`, `AHEAD_DAYS = 60`, `BEHIND_DAYS = 7`, `class NoDate(Exception)` (message is the reason), `extract_pay_by(text: str, received: date) -> date`, `extract_arriving(text: str, received: date) -> date`, `EXTRACTORS: dict[str, Callable[[str, date], date]]`, helper `_parse_date_token(fragment: str, received: date) -> date | None` used by both (accepts `D Mon YYYY`, `D Month YYYY`, `DD/MM/YYYY`, `YYYY-MM-DD`, `D Mon`, `D Month`, `Weekday D Month`, `Weekday, D Month YYYY`; a weekday prefix is skipped; a year-less date takes the received year unless that is more than 7 days before `received`, then the next year).
- `received` is a London calendar date (callers convert `Message.received` with `.astimezone(LONDON).date()`).
- Matching is case-insensitive over whitespace-collapsed text; the first phrase by position wins; after the phrase an optional `:`, `is`, `of`, `on` and whitespace are skipped.

- [ ] **Step 1: Fixture** `amex_body.txt`: the whitespace-collapsed real text (de-personalised: account number and balance replaced) containing `Minimum payment due: £131.82 Statement balance: £5,795.11 Please pay by: 30 Oct 2026 Make a payment`.

- [ ] **Step 2: Write the failing tests**

```python
R = date(2026, 10, 25)
def test_amex_fixture(): assert extract_pay_by(AMEX, R) == date(2026, 10, 30)
@pytest.mark.parametrize("frag", ["Please pay by: 30 October 2026", "pay by 30/10/2026", "Due date: 2026-10-30", "Payment due date is Friday 30 Oct 2026", "due on Fri, 30 Oct 2026"])
def test_pay_by_forms(frag): assert extract_pay_by(f"blah {frag} blah", R) == date(2026, 10, 30)
def test_pay_by_without_year_rolls_into_next_year(): assert extract_pay_by("Please pay by: 5 Jan", date(2026, 12, 28)) == date(2027, 1, 5)
def test_pay_by_without_year_same_year(): assert extract_pay_by("Please pay by: 30 Oct", R) == date(2026, 10, 30)
def test_pay_by_first_phrase_wins(): assert extract_pay_by("due date 1 Nov 2026 … please pay by: 30 Oct 2026", R) == date(2026, 11, 1)
def test_pay_by_no_phrase(): pytest.raises(NoDate, extract_pay_by, "Thanks for your payment", R)
def test_pay_by_too_far_ahead(): pytest.raises(NoDate, match="out of range", extract_pay_by, "pay by 1 Jan 2027", R)   # 68 days
def test_pay_by_too_far_behind(): pytest.raises(NoDate, match="out of range", extract_pay_by, "pay by 17 Oct 2026", R)  # 8 days
def test_pay_by_phrase_without_date(): pytest.raises(NoDate, extract_pay_by, "pay by the end of the month", R)
def test_arriving_today(): assert extract_arriving("[Pending] Delivered Arriving today 12 pm - 3 pm", R) == R
def test_arriving_tomorrow(): assert extract_arriving("Delivered Arriving tomorrow Gethin", R) == date(2026, 10, 26)
def test_arriving_weekday_and_day(): assert extract_arriving("Arriving Tuesday 27 October", R) == date(2026, 10, 27)
def test_arriving_day_only_resolves_forward(): assert extract_arriving("Arriving 2 January", date(2026, 12, 28)) == date(2027, 1, 2)
def test_arriving_range_takes_first_day(): assert extract_arriving("Delivery estimate: 27 October - 29 October", R) == date(2026, 10, 27)
def test_arriving_none(): pytest.raises(NoDate, extract_arriving, "Your order has been placed", R)
def test_extractors_registry(): assert set(EXTRACTORS) == {"pay_by", "arriving"}
```

- [ ] **Step 3: Run** `pytest tests/runner/test_calendar_extract.py -q` — Expected: ImportError.
- [ ] **Step 4: Implement** in `runner/calendar.py`. Month names via `calendar.month_name`/`month_abbr` (case-insensitive); no `dateutil` fuzzy parsing (it accepts far too much); regexes anchored right after the phrase, allowing up to 40 characters of non-digit filler before the date token.
- [ ] **Step 5: Run** — Expected: 17 passed.
- [ ] **Step 6: Commit** `git add runner/calendar.py tests/runner/test_calendar_extract.py tests/runner/fixtures/amex_body.txt && git commit -m "feat(calendar): deterministic pay-by and arriving date extractors"`

---

### Task 3: Event builder and Graph calendar client

**Files:**
- Modify: `runner/calendar.py`
- Test: `tests/runner/test_calendar_event.py`

**Interfaces:**
- Consumes: `Rule` fields (Task 1), extractors (Task 2), `request_with_retry` and `AuthError` from `runner/providers/base.py`, `Message`.
- Produces:
  - `@dataclass(frozen=True) EventSpec(title: str, date: date, note: str)`
  - `derive_title(rule: Rule, message: Message) -> str` — `rule.calendar_title` if set; else (`calendar_title_from == "subject"`): strip a leading `Ordered:`/`Ordered` (case-insensitive) and surrounding straight/curly quotes and whitespace, collapse whitespace, cut to 60 chars at a word boundary (strip a trailing `...`/`…`), prefix `Amazon: `; empty → `message.subject`.
  - `build_event(rule: Rule, message: Message, body: str) -> EventSpec` — runs `EXTRACTORS[rule.date_extractor](body, message.received.astimezone(LONDON).date())`; note `f"From: {message.sender_display()} · {message.subject} · received {received:%-d %b %Y}"` (use a portable day formatter, not `%-d`).
  - `class GraphCalendar(client: httpx.Client | None = None, sleep=time.sleep)`: `find(title: str, day: date) -> str | None` → `GET {BASE}/calendarView` with `startDateTime=<day>T00:00:00`, `endDateTime=<day+1>T00:00:00`, header `Prefer: outlook.timezone="Europe/London"`, `$select=id,subject`, `$top=50`, follows `@odata.nextLink`; returns the id whose `subject.casefold().strip() == title.casefold().strip()`. `create_all_day(spec: EventSpec) -> str` → `POST {BASE}/events` with the Global Constraints payload; returns `id`. Both via `request_with_retry` so 429/5xx retry and 401/403 raise `AuthError`.
  - `BASE = "https://graph.microsoft.com/v1.0/me"`.

- [ ] **Step 1: Write the failing tests** (respx)

```python
def test_derive_title_amazon_curly_quotes(msg):
    r = Rule("*Ordered*", "subject", "em/receipts", "calendar_event", calendar_title_from="subject", date_extractor="arriving")
    assert derive_title(r, msg(subject="Ordered: ‘Charger Cable for Fitbit...’")) == "Amazon: Charger Cable for Fitbit"
def test_derive_title_cuts_at_60_on_word_boundary(msg): ... 70-char subject → ≤ 60 chars, no mid-word cut
def test_derive_title_prefers_calendar_title(msg): ... calendar_title="Amex Payment Due" wins
def test_build_event_note_and_date(msg):
    spec = build_event(amex_rule, msg(sender='"American Express" <a@welcome.americanexpress.com>', subject="Payment due reminder", received=datetime(2026,10,25,16,40,tzinfo=timezone.utc)), AMEX)
    assert spec == EventSpec("Amex Payment Due", date(2026,10,30), "From: American Express · Payment due reminder · received 25 Oct 2026")
def test_find_matches_subject_case_insensitively(respx_mock):
    route = respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": [{"id": "E1", "subject": " amex payment due "}]})
    assert GraphCalendar(httpx.Client()).find("Amex Payment Due", date(2026,10,30)) == "E1"
    assert route.calls[0].request.url.params["startDateTime"] == "2026-10-30T00:00:00"
    assert route.calls[0].request.headers["Prefer"] == 'outlook.timezone="Europe/London"'
def test_find_returns_none_when_absent(respx_mock): ...
def test_create_all_day_payload(respx_mock):
    route = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E9"})
    assert GraphCalendar(httpx.Client()).create_all_day(EventSpec("Amex Payment Due", date(2026,10,30), "n")) == "E9"
    body = json.loads(route.calls[0].request.content)
    assert body["isAllDay"] is True and body["showAs"] == "free" and body["isReminderOn"] is False
    assert body["start"] == {"dateTime": "2026-10-30T00:00:00", "timeZone": "Europe/London"}
    assert body["end"] == {"dateTime": "2026-10-31T00:00:00", "timeZone": "Europe/London"}
    assert body["body"] == {"contentType": "text", "content": "n"}
def test_create_all_day_retries_on_429_then_succeeds_once(respx_mock):
    route = respx_mock.post(f"{BASE}/events").mock(side_effect=[httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(201, json={"id": "E9"})])
    GraphCalendar(httpx.Client(), sleep=lambda s: None).create_all_day(...)
    assert route.call_count == 2
def test_calendar_401_raises_auth_error(respx_mock): ... pytest.raises(AuthError)
```

- [ ] **Step 2: Run** — Expected: ImportError/AttributeError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/runner/test_calendar_event.py tests/runner/test_calendar_extract.py -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(calendar): event builder and Graph calendar client"`

---

### Task 4: Provider bodies and candidate listing

**Files:**
- Modify: `runner/providers/base.py` (Protocol), `runner/providers/gmail.py`, `runner/providers/graph.py`, `tests/runner/fakes.py`
- Test: `tests/runner/test_providers_body.py`

**Interfaces:**
- Produces on both providers and `FakeProvider`:
  - `get_body(message_id: str) -> str` — plain text, HTML entities unescaped, tags stripped, whitespace collapsed to single spaces.
  - `list_calendar_candidates(start: datetime, limit: int) -> list[Message]` — every inbox message received after `start`, **regardless of read state or `em/` labels**, oldest first, capped at `limit` (no `truncated` flag). Gmail: same id-collection loop as `list_since` but the query is `after:<ts> in:inbox` with no label skip. Graph: `$filter=receivedDateTime ge <iso>` with `$orderby=receivedDateTime asc` on `/mailFolders/inbox/messages`, same paging guard.
  - Gmail `get_body`: `GET /messages/{id}?format=full`; walk `payload` recursively; first `mimeType == "text/plain"` part's `body.data` (base64url, `-_` alphabet, padding tolerant) wins; else first `text/html` part with tags stripped (`re.sub(r"<[^>]+>", " ", …)` after dropping `<style>…</style>`/`<script>…</script>`); else `snippet`.
  - Graph `get_body`: `GET /messages/{id}?$select=body` with header `Prefer: outlook.body-content-type="text"`; if `body.contentType` is still `html`, strip as above.
  - `FakeProvider` gains `bodies: dict[str, str]` (constructor kw, default `{}`), `get_body` returns `bodies[id]` or raises `TransientError("no body")`, `body_calls: list[str]`; `list_calendar_candidates` returns all messages after `start` regardless of an `applied` history, and records `candidate_calls`.

- [ ] **Step 1: Write the failing tests**

```python
def test_gmail_get_body_text_part(respx_mock): ... payload.parts[text/plain base64url("Please pay by: 30 Oct 2026")] → "Please pay by: 30 Oct 2026"
def test_gmail_get_body_html_only_part(respx_mock): ... html part "<p>Please&nbsp;pay by: <b>30 Oct 2026</b></p><style>x{}</style>" → contains "Please pay by: 30 Oct 2026"
def test_gmail_get_body_nested_multipart(respx_mock): ... multipart/mixed → multipart/alternative → text/plain found
def test_gmail_list_calendar_candidates_ignores_labels(respx_mock): ... a message carrying an em/ label id is returned (list_since would skip it)
def test_graph_get_body_requests_text(respx_mock): ... route.calls[0].request.headers["Prefer"] == 'outlook.body-content-type="text"'; html fallback stripped
def test_graph_list_calendar_candidates_includes_read_mail(respx_mock): ... filter has no "isRead"; a read message is returned
def test_fake_provider_bodies(): ...
```

- [ ] **Step 2: Run** — Expected: AttributeError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/runner -q` — Expected: all pass, no warnings.
- [ ] **Step 5: Commit** `git commit -m "feat(providers): message bodies and calendar candidate listing"`

---

### Task 5: State records

**Files:**
- Modify: `runner/state.py`
- Test: `tests/runner/test_state.py`

**Interfaces:**
- Produces on `State`: `calendar_has(title: str, day: date) -> bool`; `calendar_add(title: str, day: date, message_id: str, account: str, now: datetime) -> None`; `calendar_backfilled_at() -> datetime | None`; `mark_backfilled(now: datetime) -> None`. `save()` drops `calendar.created` entries whose `date` is more than 90 days before the newest `created_at`'s date (or before today when none). Document shape as spec §4.4; `_fresh()` adds `"calendar": {"backfilled_at": None, "created": []}`; `__init__` `setdefault`s it for old files.

- [ ] **Step 1: Write the failing tests**

```python
def test_calendar_add_and_has_casefold(tmp_path): ... add("Amex Payment Due", d, "m1", "outlook", now); assert has(" amex payment due ", d); assert not has("Amex Payment Due", d + 1 day)
def test_calendar_entries_persist_and_trim_90_days(tmp_path): ... add one 100 days old and one today; save; reload; only today's remains
def test_backfilled_stamp(tmp_path): ... None → mark → iso Z string parsed back
def test_old_state_file_gains_calendar_block(tmp_path): ... a file without "calendar" loads and has() works
```

- [ ] **Step 2–4: fail → implement → pass.**
- [ ] **Step 5: Commit** `git commit -m "feat(state): record created calendar events and the backfill stamp"`

---

### Task 6: Calendar step in the run

**Files:**
- Modify: `runner/calendar.py` (`CalendarOutcome`, `calendar_step`), `runner/policy.py`, `runner/run.py`, `tests/runner/fakes.py` (`FakeCalendar`)
- Test: `tests/runner/test_calendar_step.py`, `tests/runner/test_policy.py` (bucket rename), `tests/runner/test_run.py` (additions)

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `policy.BUCKETS = ("keep", "archive", "held", "calendar", "review")`; `decide` returns `bucket="calendar"` for `calendar_event` (ops unchanged).
  - `@dataclass CalendarOutcome(status: Literal["created","skipped","review"], title: str | None, date: date | None, reason: str | None)`.
  - `calendar_step(rule, message, provider, calendar, state, now: datetime, dry_run: bool) -> CalendarOutcome`: order body → build → state dedupe → calendar dedupe (records in state when found) → create (skipped when `dry_run`; outcome `created` with `reason="dry-run"`) → `state.calendar_add`. `TransientError`/`httpx.HTTPError`/`ValueError`/`NoDate` → `review` with reason (`"no date"`, `"date out of range"`, `"body: …"`, `"graph: …"`); `AuthError` propagates.
  - `Summary` gains `calendar_created: list[dict]` (`{"title","date","account"}`), `calendar_skipped: int`; `calendar_review` keeps its meaning. `Summary.auth` may carry key `"calendar"`.
  - `run(cfg, providers, state, rules, now=utcnow, calendar: GraphCalendar | None = None)`: `calendar` defaults to `GraphCalendar(client=providers["outlook"].client)` when an outlook provider exists, else `GraphCalendar()`. After a message's ops succeed and `decision.bucket == "calendar"`: call `calendar_step`; on `AuthError` set `summary.auth["calendar"] = "unauthorized"`, log, set a flag so every later calendar match this run becomes `review` without calling Graph; mail processing continues.
  - `FakeCalendar(existing: set[tuple[str, date]] = (), fail: Exception | None = None)`: `find` returns `"E-existing"` for members, `create_all_day` records specs in `.created` and returns `f"E{n}"`, raises `fail` when set.

- [ ] **Step 1: Write the failing tests**

```python
def test_created_path(msg): ... FakeProvider bodies={"m1": AMEX}; outcome.status == "created"; calendar.created[0].title == "Amex Payment Due"; state.calendar_has(...)
def test_state_dedupe_skips_without_calendar_call(msg): ... state pre-seeded → "skipped", calendar.find_calls == 0
def test_calendar_dedupe_records_in_state(msg): ... existing in FakeCalendar → "skipped"; state now has it; nothing created
def test_second_reminder_same_cycle_is_skipped(msg): ... two messages (subjects "Payment due reminder", "Minimum payment due") both "30 Oct 2026" → one created, one skipped
def test_no_date_is_review(msg): ... body "Thanks" → ("review", reason "no date")
def test_create_failure_records_nothing(msg): ... FakeCalendar(fail=TransientError("503")) → "review", state has nothing
def test_dry_run_never_creates(msg): ... dry_run=True → status "created", reason "dry-run", calendar.created == []
def test_auth_error_propagates(msg): ... FakeCalendar(fail=AuthError("403")) → pytest.raises(AuthError)
# run.py integration (test_run.py)
def test_run_calendar_match_creates_and_summarises(): ... summary.calendar_created == [{"title": "Amex Payment Due", "date": "2026-10-30", "account": "outlook"}]; mail op applied; last_run advanced
def test_run_calendar_auth_lost_once_then_review(): ... first calendar AuthError → summary.auth["calendar"] == "unauthorized"; a second calendar match in the same run → calendar_review += 1 and calendar.find_calls unchanged; mail ops still applied
def test_policy_calendar_bucket(): ... decide(calendar_rule, "gmail").bucket == "calendar"
```

- [ ] **Step 2: Run** — Expected: failures/ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/runner -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(run): create calendar events for calendar_event matches"`

---

### Task 7: Backfill

**Files:**
- Modify: `runner/run.py`
- Test: `tests/runner/test_backfill.py`

**Interfaces:**
- Consumes: `list_calendar_candidates` (Task 4), `calendar_step` (Task 6), `State.calendar_backfilled_at/mark_backfilled` (Task 5).
- Produces: `BACKFILL_DAYS = 14`; `backfill_calendar(cfg, providers, state, rules, calendar, summary, now) -> None` called at the top of `run()` before the per-account loop when `state.calendar_backfilled_at() is None`. For each account in `accounts` with a provider: `ensure_labels()` is **not** called; `candidates = provider.list_calendar_candidates(now - 14 days, cfg.max_messages)`; for each candidate, `rule = match(message, [r for r in rules if r.action == "calendar_event"])`; if matched → `calendar_step(..., dry_run=cfg.dry_run)` and the outcome feeds the same summary fields. `AuthError` from a provider's listing marks that account's `summary.auth` as in the main loop and moves on; `AuthError` from the calendar sets `auth["calendar"]` and ends the backfill. Any other exception is logged as `backfill: <account>: <exc>` into `summary.errors` and ends the backfill **without** stamping. A pass that ends normally and `not cfg.dry_run` → `state.mark_backfilled(now)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_backfill_runs_once_and_stamps(): ... first run creates from a 10-day-old Amex candidate, state.calendar_backfilled_at() set; second run: candidate_calls unchanged
def test_backfill_lists_processed_mail_too(): ... FakeProvider message already in `applied` history and read → still a candidate → created
def test_backfill_applies_only_calendar_rules(): ... a candidate matching a trash rule → no op applied, nothing held
def test_backfill_does_no_mail_ops(): ... provider.applied == [] after backfill
def test_backfill_dry_run_does_not_stamp(): ...
def test_backfill_exception_does_not_stamp(): ... FakeProvider raising TransientError on list_calendar_candidates → errors has "backfill: outlook:", stamp None, main loop still ran
def test_backfill_window_is_14_days(): ... candidate_calls[0][0] == now - timedelta(days=14)
```

- [ ] **Step 2–4: fail → implement → pass** (`pytest tests/runner -q`).
- [ ] **Step 5: Commit** `git commit -m "feat(run): one-off 14-day calendar backfill"`

---

### Task 8: Digest, verdict, relay scope, docs

**Files:**
- Modify: `runner/digest.py`, `relay/ms.py`, `README.md`, `autonomous-processing.md` (the calendar line in the action table)
- Test: `tests/runner/test_digest.py`, `tests/relay/test_ms.py` (scope string)

**Interfaces:**
- `render`: after `Processed:` and before `⭐ Important`: `📅 Calendar: Amex Payment Due 30 Oct, Amazon: Charger Cable for Fitbit 26 Oct` (titles joined with `, `, date `D Mon`), or `📅 Would create: …` when `summary.dry_run`; then `📅 Already in calendar: N` when `calendar_skipped`; `🕒 Needs calendar review: N` unchanged. Auth loop: key `"calendar"` with status `unauthorized` → `⚠️ Calendar needs re-authorising: add Calendars.ReadWrite and run python -m relay authorize`.
- `decide_verdict`: `report` also true when `summary.calendar_created`; the auth loop handles `calendar_unauthorized` through `should_report`/`mark_reported` like the accounts.
- `relay/ms.py`: `SCOPES = "offline_access User.Read Mail.ReadWrite Calendars.ReadWrite"`.
- README: a "Calendar events" section: what creates events, the two extractors, rule fields, dedupe, backfill, the re-consent steps (Azure permission + `python -m relay authorize`), and the digest lines. `autonomous-processing.md` table row for `calendar_event` updated to "add label / mark read, create all-day Outlook event; review when no date".

- [ ] **Step 1: Tests**

```python
def test_digest_calendar_lines(): ... created two + skipped one → exact two lines in order
def test_digest_dry_run_wording(): ... "📅 Would create: Amex Payment Due 30 Oct"
def test_digest_calendar_auth_warning(): ... auth={"calendar": "unauthorized"} → the exact warning line
def test_verdict_reports_created_events(): ... created non-empty, nothing else → "REPORT"
def test_verdict_calendar_auth_suppressed_six_hours(): ... first REPORT, second QUIET within 6 h
def test_relay_scopes_include_calendar(): assert "Calendars.ReadWrite" in ms.SCOPES
```

- [ ] **Step 2–4: fail → implement → pass** (`pytest tests -q`, expected ≥ 107 + new, 0 warnings).
- [ ] **Step 5: Commit** `git commit -m "feat(digest): report created calendar events; relay requests Calendars.ReadWrite"`

---

### Task 9: Deploy and live run

Owner prerequisites: delegated `Calendars.ReadWrite` added to app registration `fd8c524f-001b-4423-8a19-21bd28ff11ab`.

- [ ] **Step 1: Pull on Ubuntu** — `cd ~/email-manager && git fetch origin && git checkout calendar-events && git pull`; `bash scripts/run.sh --dry-run --max 5` must still work before re-consent (calendar 403 → review, digest shows the re-authorising line). Expected: digest contains `⚠️ Calendar needs re-authorising`.
- [ ] **Step 2: Re-consent** — owner runs `.venv-host/bin/python -m relay authorize` then `python -m relay refresh`; `cat ~/.config/email-manager/outlook-token.json | python3 -c "import json,sys;print(json.load(sys.stdin)['scope'])"` contains `Calendars.ReadWrite`. Expected: scope printed with the calendar permission.
- [ ] **Step 3: Dry run with backfill** — `bash scripts/run.sh --dry-run`. Expected: digest line `📅 Would create: Amex Payment Due 30 Oct` (plus any Amazon arrivals within 14 days that are not already past); `state.json` has no `backfilled_at` yet.
- [ ] **Step 4: Real run** — `bash scripts/run.sh`. Expected: `📅 Calendar: Amex Payment Due 30 Oct`; `state.json` `calendar.created` has the entry and `backfilled_at` is set; a second `bash scripts/run.sh` reports `📅 Already in calendar` or nothing and creates no duplicate (verify `calendarView` count for 30 Oct is 1).
- [ ] **Step 5: Verify** — Outlook calendar shows "Amex Payment Due" on 30 Oct (owner); wallboard `data/state/calendar.json` lists it after the next feed refresh (`grep -c "Amex Payment Due" ~/wallboard/data/state/calendar.json` ≥ 1 within ~30 min, since Outlook's published ICS lags).
- [ ] **Step 6: Ledger** the timings and outputs.

---

## Self-review notes

- Spec coverage: §3 → Task 1; §4.1 → Tasks 2, 3, 6; §4.2 → Task 6; §4.3 → Task 7; §4.4 → Task 5; §4.5 → Task 4; §4.6 → Task 8 + 9; §5 → Task 8; §6 → Task 3 payload; §7 → Tasks 6, 7, 8; §8 → every task's tests + Task 9; §9/§10 → Task 9.
- Deviation from the spec, a plan ruling: the backfill lists with a new `list_calendar_candidates` instead of `list_since`, because `list_since` deliberately skips mail that already carries an `em/` label (Gmail) or is read (Outlook), and the Amex mail is both after the 2026-10-25 run; spec §4.3's "list_since" would have found nothing.
- Type consistency checked: `CalendarOutcome.status` strings, `Summary` field names, `FakeCalendar` method names match across Tasks 6–8.
