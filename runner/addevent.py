"""`python -m runner.addevent --title T (--date D | --start S [--end E]) [--yearly] [--note N]`

Puts one event in the Outlook calendar. Dr Claw runs it (through scripts/add-event.sh)
when someone asks for a calendar entry. The single stdout line is the contract it reads:

    CREATED: <title>, <when>      exit 0  the event is in the calendar now
    EXISTS: <title>, <when>       exit 0  an event with that title was already on that day
    ERROR: <reason>               exit 1  nothing was created

Dates and times are London wall-clock. Creating is all this does: it never edits or
deletes an event. Each creation appends one line to data/events.log.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .calendar import LONDON, GraphCalendar, NewEvent
from .providers.base import AuthError, TransientError

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LOG = REPO / "data" / "events.log"
BEHIND_DAYS = 7
AHEAD_YEARS = 2
DEFAULT_LENGTH = timedelta(hours=1)
RANGE_MESSAGE = f"date out of range (within {BEHIND_DAYS} days back and {AHEAD_YEARS} years ahead of today)"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m runner.addevent", description="Create one Outlook calendar event.")
    p.add_argument("--title", required=True)
    p.add_argument("--date", help="all-day event on this day, YYYY-MM-DD")
    p.add_argument("--start", help="timed event starting at this London time, YYYY-MM-DDTHH:MM")
    p.add_argument("--end", help="end of a timed event, YYYY-MM-DDTHH:MM; default one hour after --start")
    p.add_argument("--yearly", action="store_true", help="repeat every year on the same day")
    p.add_argument("--note", default="", help="text for the event body")
    return p.parse_args(argv)


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD") from None


def _parse_time(text: str, name: str) -> datetime:
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DDTHH:MM") from None
    return dt.replace(tzinfo=LONDON)


def _years_ahead(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def build(args: argparse.Namespace, today: date) -> NewEvent:
    """The event the arguments describe; ValueError with a plain reason when they do not add up."""
    title = " ".join(args.title.split())
    if not title:
        raise ValueError("title is empty")
    if args.date and args.start:
        raise ValueError("give --date or --start, not both")
    if args.end and not args.start:
        raise ValueError("--end needs --start")
    if args.date:
        start = datetime.combine(_parse_day(args.date), datetime.min.time(), tzinfo=LONDON)
        end = start + timedelta(days=1)
        all_day = True
    elif args.start:
        start = _parse_time(args.start, "start")
        end = _parse_time(args.end, "end") if args.end else start + DEFAULT_LENGTH
        if end <= start:
            raise ValueError("end is before start")
        all_day = False
    else:
        raise ValueError("give --date or --start")
    if not (today - timedelta(days=BEHIND_DAYS) <= start.date() <= _years_ahead(today, AHEAD_YEARS)):
        raise ValueError(RANGE_MESSAGE)
    return NewEvent(title, start, end, all_day, yearly=args.yearly, note=args.note)


def _day(dt: datetime) -> str:
    return f"{dt:%a} {dt.day} {dt:%b %Y}"


def describe(ev: NewEvent) -> str:
    if ev.all_day:
        when = f"{_day(ev.start)}, all day"
    elif ev.end.date() == ev.start.date():
        when = f"{_day(ev.start)} {ev.start:%H:%M}-{ev.end:%H:%M}"
    else:
        when = f"{_day(ev.start)} {ev.start:%H:%M} to {_day(ev.end)} {ev.end:%H:%M}"
    text = f"{ev.title}, {when}"
    return text + ", repeats yearly" if ev.yearly else text


def _log(log: Path, line: str, now: datetime) -> None:
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{now.astimezone(LONDON).isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass  # the event exists; a missing audit line must not turn that into a failure


def run(argv: list[str], calendar: GraphCalendar, today: date, log: Path, now: datetime | None = None) -> tuple[int, str]:
    try:
        ev = build(parse_args(argv), today)
    except ValueError as exc:
        return 1, f"ERROR: {exc}"
    try:
        if calendar.find(ev.title, ev.start.date()):
            return 0, f"EXISTS: {describe(ev)}"
        calendar.create(ev)
    except AuthError as exc:
        return 1, f"ERROR: calendar not authorised ({exc})"
    except (TransientError, httpx.HTTPError, ValueError, KeyError) as exc:
        return 1, f"ERROR: calendar request failed ({exc})"
    line = f"CREATED: {describe(ev)}"
    _log(log, line, now or datetime.now(LONDON))
    return 0, line


def main(argv: list[str] | None = None) -> int:
    today = datetime.now(LONDON).date()
    rc, line = run(sys.argv[1:] if argv is None else argv, GraphCalendar(), today, DEFAULT_LOG)
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
