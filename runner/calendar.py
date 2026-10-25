"""Calendar events from rule matches: date extraction, event building, Graph client."""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Literal
from zoneinfo import ZoneInfo

import httpx

from .models import Message
from .providers.base import AuthError, Provider, TransientError, request_with_retry
from .rules import EXTRACTOR_NAMES, Rule
from .state import State

BASE = "https://graph.microsoft.com/v1.0/me"

LONDON = ZoneInfo("Europe/London")
AHEAD_DAYS = 60
BEHIND_DAYS = 7

_MONTHS = {}
for _i in range(1, 13):
    _MONTHS[calendar.month_name[_i].lower()] = _i
    _MONTHS[calendar.month_abbr[_i].lower()] = _i
_MONTHS["sept"] = 9

_WEEKDAYS = [n.lower() for n in calendar.day_name] + [n.lower() for n in calendar.day_abbr]
_WEEKDAY_RE = re.compile(r"(?:%s)\b,?\s*" % "|".join(sorted(_WEEKDAYS, key=len, reverse=True)), re.I)

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?!\d)")
_SLASH_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_NAMED_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?(?:,?\s+(\d{4})(?!\d))?")

_SKIP_RE = re.compile(r"\s*(?::|\b(?:is|of|on)\b)?\s*", re.I)
_FILLER_RE = re.compile(r"\D{0,40}")


class NoDate(Exception):
    """No usable date found; the message is the reason."""


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_date_token(fragment: str, received: date, lookback: int = BEHIND_DAYS) -> date | None:
    """Parse a date at the start of fragment; None if there isn't one.

    A year-less date takes the received year unless that is more than `lookback` days
    before received, then the next year.
    """
    fragment = fragment.lstrip()
    wd = _WEEKDAY_RE.match(fragment)
    if wd:
        fragment = fragment[wd.end():]

    m = _ISO_RE.match(fragment)
    if m:
        return _build(int(m[1]), int(m[2]), int(m[3]))
    m = _SLASH_RE.match(fragment)
    if m:
        return _build(int(m[3]), int(m[2]), int(m[1]))
    m = _NAMED_RE.match(fragment)
    if not m:
        return None
    month = _MONTHS.get(m[2].lower())
    if month is None:
        return None
    day = int(m[1])
    if m[3]:
        return _build(int(m[3]), month, day)
    found = _build(received.year, month, day)
    if found is not None and found < received - timedelta(days=lookback):
        found = _build(received.year + 1, month, day)
    return found


def _check_range(found: date, received: date) -> date:
    if not (received - timedelta(days=BEHIND_DAYS) <= found <= received + timedelta(days=AHEAD_DAYS)):
        raise NoDate("date out of range")
    return found


def _first_date(phrases: re.Pattern, text: str, received: date, resolve: Callable[[str, date], date | None]) -> date:
    """Try each phrase match in position order; the first phrase that parses to a date wins.

    That date must lie between BEHIND_DAYS before and AHEAD_DAYS after `received`;
    if it does not, NoDate("date out of range") is raised at once and later phrases
    are not tried. NoDate("no date") if no phrase is followed by a date.
    """
    text = _collapse(text)
    for m in phrases.finditer(text):
        rest = text[m.end():]
        rest = rest[_SKIP_RE.match(rest).end():]
        found = resolve(rest, received)
        if found is not None:
            return _check_range(found, received)
    raise NoDate("no date")


def _resolve_token(rest: str, received: date, lookback: int) -> date | None:
    fragment = rest[_FILLER_RE.match(rest).end():]
    return _parse_date_token(fragment, received, lookback) if fragment else None


_PAY_BY_RE = re.compile(r"\b(?:pay by|due date|due by|due on)\b", re.I)
_ARRIVING_RE = re.compile(r"\b(?:arriving|delivery estimate|estimated delivery|expected delivery)\b", re.I)
_RELATIVE_RE = re.compile(r"(today|tomorrow)\b", re.I)


def _resolve_pay_by(rest: str, received: date) -> date | None:
    return _resolve_token(rest, received, BEHIND_DAYS)


def _resolve_arriving(rest: str, received: date) -> date | None:
    rel = _RELATIVE_RE.match(rest)
    if rel:
        return received + timedelta(days=1 if rel[1].lower() == "tomorrow" else 0)
    return _resolve_token(rest, received, 0)


def extract_pay_by(text: str, received: date) -> date:
    return _first_date(_PAY_BY_RE, text, received, _resolve_pay_by)


def extract_arriving(text: str, received: date) -> date:
    return _first_date(_ARRIVING_RE, text, received, _resolve_arriving)


_FUNCS: dict[str, Callable[[str, date], date]] = {
    "pay_by": extract_pay_by,
    "arriving": extract_arriving,
}
EXTRACTORS: dict[str, Callable[[str, date], date]] = {name: _FUNCS[name] for name in EXTRACTOR_NAMES}


@dataclass(frozen=True)
class EventSpec:
    title: str
    date: date
    note: str


_QUOTES = " \t\"'\u2018\u2019\u201c\u201d"
_ORDERED_RE = re.compile(r"^\s*ordered\b:?", re.I)
_TITLE_MAX = 60


def _trim_ellipsis(text: str) -> str:
    return text.rstrip().removesuffix("...").removesuffix("…").rstrip()


def derive_title(rule: Rule, message: Message) -> str:
    if rule.calendar_title:
        return rule.calendar_title
    text = _ORDERED_RE.sub("", message.subject or "", count=1)
    text = _collapse(text.strip(_QUOTES))
    if len(text) > _TITLE_MAX:
        cut = text[: _TITLE_MAX + 1]
        text = cut[: cut.rfind(" ")] if " " in cut else cut[:_TITLE_MAX]
    text = _trim_ellipsis(text)
    return f"Amazon: {text}" if text else message.subject


def _fmt_day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def build_event(rule: Rule, message: Message, body: str) -> EventSpec:
    received = message.received.astimezone(LONDON).date()
    when = EXTRACTORS[rule.date_extractor](body, received)
    note = f"From: {message.sender_display()} · {message.subject} · received {_fmt_day(received)}"
    return EventSpec(derive_title(rule, message), when, note)


class GraphCalendar:
    def __init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        self.client = client or httpx.Client(timeout=30.0)
        self.sleep = sleep

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        return request_with_retry(self.client, method, url, sleep=self.sleep, **kw)

    def find(self, title: str, day: date) -> str | None:
        want = title.casefold().strip()
        params = {
            # Graph reads an offset-less bound as UTC whatever Prefer says, so qualify it.
            "startDateTime": datetime.combine(day, dtime(0), tzinfo=LONDON).isoformat(),
            "endDateTime": datetime.combine(day + timedelta(days=1), dtime(0), tzinfo=LONDON).isoformat(),
            "$select": "id,subject,start",
            "$top": 50,
        }
        headers = {"Prefer": 'outlook.timezone="Europe/London"'}
        url: str | None = f"{BASE}/calendarView"
        seen: set[str] = set()
        first = True
        while url:
            page = self._req("GET", url, params=params if first else None, headers=headers).json()
            first = False
            added = 0
            for item in page.get("value", []) or []:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                added += 1
                starts = ((item.get("start") or {}).get("dateTime") or "")[:10] == day.isoformat()
                if starts and (item.get("subject") or "").casefold().strip() == want:
                    return item["id"]
            if added == 0:
                break  # a page that adds nothing new is a paging fault; never follow it again
            url = page.get("@odata.nextLink")
        return None

    def create_all_day(self, spec: EventSpec) -> str:
        payload = {
            "subject": spec.title,
            "isAllDay": True,
            "showAs": "free",
            "isReminderOn": False,
            "start": {"dateTime": f"{spec.date.isoformat()}T00:00:00", "timeZone": "Europe/London"},
            "end": {"dateTime": f"{(spec.date + timedelta(days=1)).isoformat()}T00:00:00", "timeZone": "Europe/London"},
            "body": {"contentType": "text", "content": spec.note},
        }
        # POST is the one non-idempotent call: a timeout may come after Graph committed the
        # event, so never resend it blindly. Look it up once instead.
        try:
            resp = self._req("POST", f"{BASE}/events", json=payload, retry_transport=False)
        except httpx.HTTPError as exc:
            found = self.find(spec.title, spec.date)
            if found:
                return found
            raise TransientError(f"POST {BASE}/events: {exc}; event not found afterwards") from exc
        return resp.json()["id"]


class BodyAuthError(AuthError):
    """The mail provider (not the calendar) rejected the body fetch."""


@dataclass(frozen=True)
class CalendarOutcome:
    status: Literal["created", "skipped", "review"]
    title: str | None = None
    date: date | None = None
    reason: str | None = None


def calendar_step(
    rule: Rule,
    message: Message,
    provider: Provider,
    calendar,
    state: State,
    now: datetime,
    dry_run: bool,
    seen: set | None = None,
    skip_before: date | None = None,
) -> CalendarOutcome:
    """Create (or recognise) the all-day event for one calendar_event match.

    AuthError propagates; every other failure is a `review` outcome with a reason.
    An event dated before ``skip_before`` (the backfill passes today) is `skipped`
    with reason "past", touching neither the calendar nor the state.
    """
    try:
        body = provider.get_body(message.id)
    except AuthError as exc:
        raise BodyAuthError(str(exc), exc.connect_url) from exc
    except (TransientError, httpx.HTTPError, ValueError) as exc:
        return CalendarOutcome("review", reason=f"body: {exc}")
    try:
        spec = build_event(rule, message, body)
    except NoDate as exc:
        return CalendarOutcome("review", reason=str(exc))
    except ValueError as exc:
        return CalendarOutcome("review", reason=f"body: {exc}")
    if skip_before is not None and spec.date < skip_before:
        return CalendarOutcome("skipped", spec.title, spec.date, "past")
    if state.calendar_has(spec.title, spec.date):
        return CalendarOutcome("skipped", spec.title, spec.date)
    key = (spec.title.casefold().strip(), spec.date)
    if dry_run and seen is not None and key in seen:
        return CalendarOutcome("skipped", spec.title, spec.date, "dry-run")
    try:
        if calendar.find(spec.title, spec.date):
            if not dry_run:
                state.calendar_add(spec.title, spec.date, message.id, message.account, now)
            return CalendarOutcome("skipped", spec.title, spec.date)
        if dry_run:
            if seen is not None:
                seen.add(key)
            return CalendarOutcome("created", spec.title, spec.date, "dry-run")
        calendar.create_all_day(spec)
    except AuthError:
        raise
    except (TransientError, httpx.HTTPError, ValueError) as exc:
        return CalendarOutcome("review", spec.title, spec.date, f"graph: {exc}")
    state.calendar_add(spec.title, spec.date, message.id, message.account, now)
    return CalendarOutcome("created", spec.title, spec.date)
