"""Calendar events from rule matches: date extraction, event building, Graph client."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .rules import EXTRACTOR_NAMES

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


def _parse_date_token(fragment: str, received: date) -> date | None:
    """Parse a date at the start of fragment; None if there isn't one."""
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
    if found is not None and found < received - timedelta(days=BEHIND_DAYS):
        found = _build(received.year + 1, month, day)
    return found


def _after_phrase(phrases: re.Pattern, text: str) -> str:
    text = _collapse(text)
    m = phrases.search(text)
    if not m:
        raise NoDate("no date")
    rest = text[m.end():]
    rest = rest[_SKIP_RE.match(rest).end():]
    return rest


def _check_range(found: date, received: date) -> date:
    if not (received - timedelta(days=BEHIND_DAYS) <= found <= received + timedelta(days=AHEAD_DAYS)):
        raise NoDate("date out of range")
    return found


def _find_date(rest: str, received: date) -> date:
    fragment = rest[_FILLER_RE.match(rest).end():]
    found = _parse_date_token(fragment, received) if fragment else None
    if found is None:
        raise NoDate("no date")
    return _check_range(found, received)


_PAY_BY_RE = re.compile(r"pay by|due date|due by|due on", re.I)
_ARRIVING_RE = re.compile(r"arriving|delivery estimate", re.I)
_RELATIVE_RE = re.compile(r"(today|tomorrow)\b", re.I)


def extract_pay_by(text: str, received: date) -> date:
    return _find_date(_after_phrase(_PAY_BY_RE, text), received)


def extract_arriving(text: str, received: date) -> date:
    rest = _after_phrase(_ARRIVING_RE, text)
    rel = _RELATIVE_RE.match(rest)
    if rel:
        offset = 1 if rel[1].lower() == "tomorrow" else 0
        return received + timedelta(days=offset)
    return _find_date(rest, received)


_FUNCS: dict[str, Callable[[str, date], date]] = {
    "pay_by": extract_pay_by,
    "arriving": extract_arriving,
}
EXTRACTORS: dict[str, Callable[[str, date], date]] = {name: _FUNCS[name] for name in EXTRACTOR_NAMES}
