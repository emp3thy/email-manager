"""`python -m runner.addevent`: the command Dr Claw runs to put an event in the Outlook calendar."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from runner import addevent
from runner.calendar import BASE, GraphCalendar, NewEvent

LONDON = ZoneInfo("Europe/London")
TODAY = date(2026, 11, 1)


def _cal():
    return GraphCalendar(httpx.Client(), sleep=lambda s: None)


def _no_events(respx_mock):
    return respx_mock.get(url__startswith=f"{BASE}/calendarView").respond(200, json={"value": []})


def _event(title="Christopher Moy's Birthday", day=date(2026, 11, 5), **kw):
    start = datetime.combine(day, datetime.min.time(), tzinfo=LONDON)
    base = dict(title=title, start=start, end=start.replace(day=day.day + 1), all_day=True, yearly=False, note="")
    base.update(kw)
    return NewEvent(**base)


# --- Graph payloads -----------------------------------------------------------


def test_create_all_day_event_payload(respx_mock):
    route = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E1"})
    assert _cal().create(_event(note="Added by Dr Claw")) == "E1"
    body = json.loads(route.calls[0].request.content)
    assert body["subject"] == "Christopher Moy's Birthday"
    assert body["isAllDay"] is True and body["showAs"] == "free" and body["isReminderOn"] is False
    assert body["start"] == {"dateTime": "2026-11-05T00:00:00", "timeZone": "Europe/London"}
    assert body["end"] == {"dateTime": "2026-11-06T00:00:00", "timeZone": "Europe/London"}
    assert body["body"] == {"contentType": "text", "content": "Added by Dr Claw"}
    assert "recurrence" not in body


def test_create_timed_event_payload(respx_mock):
    route = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E2"})
    ev = _event(title="Dentist", start=datetime(2026, 11, 6, 14, 0, tzinfo=LONDON),
                end=datetime(2026, 11, 6, 15, 0, tzinfo=LONDON), all_day=False)
    _cal().create(ev)
    body = json.loads(route.calls[0].request.content)
    assert body["isAllDay"] is False and body["showAs"] == "busy"
    assert "isReminderOn" not in body  # Graph's default reminder stands for timed events
    assert body["start"] == {"dateTime": "2026-11-06T14:00:00", "timeZone": "Europe/London"}
    assert body["end"] == {"dateTime": "2026-11-06T15:00:00", "timeZone": "Europe/London"}


def test_create_yearly_event_recurs_on_the_start_day(respx_mock):
    route = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E3"})
    _cal().create(_event(yearly=True))
    body = json.loads(route.calls[0].request.content)
    assert body["recurrence"] == {
        "pattern": {"type": "absoluteYearly", "interval": 1, "month": 11, "dayOfMonth": 5},
        "range": {"type": "noEnd", "startDate": "2026-11-05"},
    }


def test_create_timeout_recovers_event_via_find(respx_mock):
    def _timeout(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    respx_mock.post(f"{BASE}/events").mock(side_effect=_timeout)
    respx_mock.get(url__startswith=f"{BASE}/calendarView").respond(200, json={"value": [
        {"id": "E4", "subject": "Christopher Moy's Birthday", "start": {"dateTime": "2026-11-05T00:00:00.0000000"}}]})
    assert _cal().create(_event()) == "E4"


# --- argument handling --------------------------------------------------------


def test_all_day_from_date_argument():
    ev = addevent.build(addevent.parse_args(["--title", "Christopher Moy's Birthday", "--date", "2026-11-05"]), TODAY)
    assert ev == _event()


def test_timed_event_defaults_to_one_hour():
    ev = addevent.build(addevent.parse_args(["--title", "Dentist", "--start", "2026-11-06T14:00"]), TODAY)
    assert ev.all_day is False
    assert ev.start == datetime(2026, 11, 6, 14, 0, tzinfo=LONDON)
    assert ev.end == datetime(2026, 11, 6, 15, 0, tzinfo=LONDON)


def test_timed_event_takes_explicit_end():
    ev = addevent.build(addevent.parse_args(["--title", "Dentist", "--start", "2026-11-06T14:00", "--end", "2026-11-06T14:30"]), TODAY)
    assert ev.end == datetime(2026, 11, 6, 14, 30, tzinfo=LONDON)


def test_yearly_and_note_flags():
    ev = addevent.build(addevent.parse_args(["--title", "B", "--date", "2026-11-05", "--yearly", "--note", "from the wall"]), TODAY)
    assert ev.yearly is True and ev.note == "from the wall"


@pytest.mark.parametrize("argv, message", [
    (["--title", "X"], "give --date or --start"),
    (["--title", "X", "--date", "2026-11-05", "--start", "2026-11-05T10:00"], "give --date or --start, not both"),
    (["--title", "X", "--date", "2026-11-05", "--end", "2026-11-05T10:00"], "--end needs --start"),
    (["--title", "X", "--start", "2026-11-06T14:00", "--end", "2026-11-06T13:00"], "end is before start"),
    (["--title", "X", "--start", "2026-11-06T14:00", "--end", "2026-11-06T14:00"], "end is before start"),
    (["--title", "X", "--date", "2026-10-24"], "date out of range"),
    (["--title", "X", "--date", "2028-11-02"], "date out of range"),
    (["--title", "X", "--date", "5 Nov"], "date must be YYYY-MM-DD"),
    (["--title", "X", "--start", "2026-11-06 14:00"], "start must be YYYY-MM-DDTHH:MM"),
    (["--title", "   ", "--date", "2026-11-05"], "title is empty"),
])
def test_rejected_arguments(argv, message):
    with pytest.raises(ValueError, match=message):
        addevent.build(addevent.parse_args(argv), TODAY)


def test_range_edges_are_accepted():
    addevent.build(addevent.parse_args(["--title", "X", "--date", "2026-10-25"]), TODAY)
    addevent.build(addevent.parse_args(["--title", "X", "--date", "2028-11-01"]), TODAY)


# --- the output line ------------------------------------------------------------


def test_describe_all_day():
    assert addevent.describe(_event()) == "Christopher Moy's Birthday, Thu 5 Nov 2026, all day"


def test_describe_yearly():
    assert addevent.describe(_event(yearly=True)) == "Christopher Moy's Birthday, Thu 5 Nov 2026, all day, repeats yearly"


def test_describe_timed():
    ev = _event(title="Dentist", start=datetime(2026, 11, 6, 14, 0, tzinfo=LONDON),
                end=datetime(2026, 11, 6, 15, 30, tzinfo=LONDON), all_day=False)
    assert addevent.describe(ev) == "Dentist, Fri 6 Nov 2026 14:00-15:30"


def test_describe_timed_spanning_days():
    ev = _event(title="Camp", start=datetime(2026, 11, 6, 18, 0, tzinfo=LONDON),
                end=datetime(2026, 11, 7, 10, 0, tzinfo=LONDON), all_day=False)
    assert addevent.describe(ev) == "Camp, Fri 6 Nov 2026 18:00 to Sat 7 Nov 2026 10:00"


# --- run: the contract Dr Claw reads ------------------------------------------------


def test_run_creates_and_reports(respx_mock, tmp_path):
    _no_events(respx_mock)
    post = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E1"})
    log = tmp_path / "events.log"
    rc, line = addevent.run(["--title", "Christopher Moy's Birthday", "--date", "2026-11-05", "--yearly"], _cal(), TODAY, log,
                            now=datetime(2026, 11, 1, 10, 9, tzinfo=LONDON))
    assert (rc, line) == (0, "CREATED: Christopher Moy's Birthday, Thu 5 Nov 2026, all day, repeats yearly")
    assert post.call_count == 1
    assert log.read_text(encoding="utf-8") == "2026-11-01T10:09:00+00:00 CREATED: Christopher Moy's Birthday, Thu 5 Nov 2026, all day, repeats yearly\n"


def test_run_reports_existing_event_without_creating(respx_mock, tmp_path):
    respx_mock.get(url__startswith=f"{BASE}/calendarView").respond(200, json={"value": [
        {"id": "E1", "subject": "christopher moy's birthday", "start": {"dateTime": "2026-11-05T00:00:00.0000000"}}]})
    post = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E9"})
    log = tmp_path / "events.log"
    rc, line = addevent.run(["--title", "Christopher Moy's Birthday", "--date", "2026-11-05"], _cal(), TODAY, log)
    assert (rc, line) == (0, "EXISTS: Christopher Moy's Birthday, Thu 5 Nov 2026, all day")
    assert post.call_count == 0 and not log.exists()


def test_run_bad_arguments_make_no_request(respx_mock, tmp_path):
    get = _no_events(respx_mock)
    rc, line = addevent.run(["--title", "X", "--date", "2020-01-01"], _cal(), TODAY, tmp_path / "events.log")
    assert (rc, line) == (1, "ERROR: date out of range (within 7 days back and 2 years ahead of today)")
    assert get.call_count == 0


def test_run_reports_calendar_auth_failure(respx_mock, tmp_path):
    respx_mock.get(url__startswith=f"{BASE}/calendarView").respond(403, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied"}})
    rc, line = addevent.run(["--title", "X", "--date", "2026-11-05"], _cal(), TODAY, tmp_path / "events.log")
    assert rc == 1 and line.startswith("ERROR: calendar not authorised")


def test_run_reports_graph_failure(respx_mock, tmp_path):
    _no_events(respx_mock)
    respx_mock.post(f"{BASE}/events").respond(500, text="boom")
    rc, line = addevent.run(["--title", "X", "--date", "2026-11-05"], _cal(), TODAY, tmp_path / "events.log")
    assert rc == 1 and line.startswith("ERROR: calendar request failed")


def test_run_log_failure_does_not_fail_the_command(respx_mock, tmp_path):
    _no_events(respx_mock)
    respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E1"})
    rc, line = addevent.run(["--title", "X", "--date", "2026-11-05"], _cal(), TODAY, tmp_path)  # a directory, not a file
    assert rc == 0 and line.startswith("CREATED:")


def test_main_prints_the_line_and_exits_with_the_code(monkeypatch, capsys):
    monkeypatch.setattr(addevent, "run", lambda argv, calendar, today, log: (1, "ERROR: x"))
    assert addevent.main(["--title", "X"]) == 1
    assert capsys.readouterr().out == "ERROR: x\n"
