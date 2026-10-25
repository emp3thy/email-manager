import json
from datetime import date, datetime, timezone

import httpx
import pytest

from runner.calendar import BASE, EventSpec, GraphCalendar, build_event, derive_title
from runner.providers.base import AuthError, TransientError, request_with_retry
from runner.rules import Rule

AMEX = "Payment due. Pay by Fri 30 Oct 2026 to avoid interest."
AMEX_RULE = Rule("*payment due*", "subject", "em/receipts", "calendar_event",
                 calendar_title="Amex Payment Due", date_extractor="pay_by")
AMAZON_RULE = Rule("*Ordered*", "subject", "em/receipts", "calendar_event",
                   calendar_title_from="subject", date_extractor="arriving")
SPEC = EventSpec("Amex Payment Due", date(2026, 10, 30), "n")


def test_derive_title_amazon_curly_quotes(msg):
    assert derive_title(AMAZON_RULE, msg(subject="Ordered: ‘Charger Cable for Fitbit...’")) == "Amazon: Charger Cable for Fitbit"


def test_derive_title_strips_straight_quotes_and_bare_ordered(msg):
    assert derive_title(AMAZON_RULE, msg(subject='ordered   "Desk  Lamp"')) == "Amazon: Desk Lamp"


def test_derive_title_cuts_at_60_on_word_boundary(msg):
    subject = "Ordered: " + " ".join(["alpha"] * 14)  # 14 words = 83 chars of product
    title = derive_title(AMAZON_RULE, msg(subject=subject))
    product = title.removeprefix("Amazon: ")
    assert len(product) <= 60 and product == " ".join(["alpha"] * 10)


def test_derive_title_empty_falls_back_to_subject(msg):
    assert derive_title(AMAZON_RULE, msg(subject="Ordered:")) == "Ordered:"


def test_derive_title_prefers_calendar_title(msg):
    r = Rule("*", "subject", "em/receipts", "calendar_event", calendar_title="Amex Payment Due",
             calendar_title_from="subject", date_extractor="pay_by")
    assert derive_title(r, msg(subject="Ordered: thing")) == "Amex Payment Due"


def test_build_event_note_and_date(msg):
    m = msg(sender='"American Express" <a@welcome.americanexpress.com>', subject="Payment due reminder",
            received=datetime(2026, 10, 25, 16, 40, tzinfo=timezone.utc))
    assert build_event(AMEX_RULE, m, AMEX) == EventSpec(
        "Amex Payment Due", date(2026, 10, 30), "From: American Express · Payment due reminder · received 25 Oct 2026")


def test_build_event_single_digit_day_not_padded(msg):
    m = msg(subject="s", received=datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc))
    assert build_event(AMEX_RULE, m, "Pay by 10 Nov 2026").note.endswith("received 5 Nov 2026")


def test_find_matches_subject_case_insensitively(respx_mock):
    route = respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": [{"id": "E1", "subject": " amex payment due ", "start": {"dateTime": "2026-10-30T00:00:00.0000000"}}]})
    assert GraphCalendar(httpx.Client()).find("Amex Payment Due", date(2026, 10, 30)) == "E1"
    p = route.calls[0].request.url.params
    assert p["startDateTime"] == "2026-10-30T00:00:00+00:00" and p["endDateTime"] == "2026-10-31T00:00:00+00:00"
    assert p["$select"] == "id,subject,start" and p["$top"] == "50"
    assert route.calls[0].request.headers["Prefer"] == 'outlook.timezone="Europe/London"'


def test_find_returns_none_when_absent(respx_mock):
    respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": [{"id": "E1", "subject": "Other"}]})
    assert GraphCalendar(httpx.Client()).find("Amex Payment Due", date(2026, 10, 30)) is None


def test_find_follows_next_link(respx_mock):
    nxt = "https://graph.microsoft.com/v1.0/me/calendarView?$skiptoken=abc"
    respx_mock.get(f"{BASE}/calendarView", params={"startDateTime": "2026-10-30T00:00:00+00:00"}).respond(
        200, json={"value": [{"id": "E1", "subject": "Other"}], "@odata.nextLink": nxt})
    second = respx_mock.get(nxt).respond(200, json={"value": [{"id": "E2", "subject": "Amex Payment Due", "start": {"dateTime": "2026-10-30T00:00:00.0000000"}}]})
    assert GraphCalendar(httpx.Client()).find("Amex Payment Due", date(2026, 10, 30)) == "E2"
    assert second.call_count == 1


def test_find_stops_on_page_that_adds_nothing(respx_mock):
    nxt = f"{BASE}/calendarView?$skiptoken=loop"
    route = respx_mock.get(url__startswith=f"{BASE}/calendarView").respond(
        200, json={"value": [{"id": "E1", "subject": "Other"}], "@odata.nextLink": nxt})
    assert GraphCalendar(httpx.Client()).find("Amex Payment Due", date(2026, 10, 30)) is None
    assert route.call_count == 2


def test_create_all_day_payload(respx_mock):
    route = respx_mock.post(f"{BASE}/events").respond(201, json={"id": "E9"})
    assert GraphCalendar(httpx.Client()).create_all_day(SPEC) == "E9"
    body = json.loads(route.calls[0].request.content)
    assert body["subject"] == "Amex Payment Due"
    assert body["isAllDay"] is True and body["showAs"] == "free" and body["isReminderOn"] is False
    assert body["start"] == {"dateTime": "2026-10-30T00:00:00", "timeZone": "Europe/London"}
    assert body["end"] == {"dateTime": "2026-10-31T00:00:00", "timeZone": "Europe/London"}
    assert body["body"] == {"contentType": "text", "content": "n"}


def test_create_all_day_retries_on_429_then_succeeds_once(respx_mock):
    route = respx_mock.post(f"{BASE}/events").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(201, json={"id": "E9"})])
    assert GraphCalendar(httpx.Client(), sleep=lambda s: None).create_all_day(SPEC) == "E9"
    assert route.call_count == 2


@pytest.mark.parametrize("call", ["find", "create"])
def test_calendar_401_raises_auth_error(respx_mock, call):
    respx_mock.get(f"{BASE}/calendarView").respond(401, json={})
    respx_mock.post(f"{BASE}/events").respond(401, json={})
    cal = GraphCalendar(httpx.Client(), sleep=lambda s: None)
    with pytest.raises(AuthError):
        cal.find("x", date(2026, 10, 30)) if call == "find" else cal.create_all_day(SPEC)


def test_find_uses_bst_offset_in_window(respx_mock):
    route = respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": []})
    GraphCalendar(httpx.Client()).find("x", date(2026, 7, 30))
    p = route.calls[0].request.url.params
    assert p["startDateTime"] == "2026-07-30T00:00:00+01:00" and p["endDateTime"] == "2026-07-31T00:00:00+01:00"


def test_find_ignores_same_title_on_adjacent_day(respx_mock):
    respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": [
        {"id": "E1", "subject": "Amex Payment Due", "start": {"dateTime": "2026-10-31T00:00:00.0000000"}}]})
    assert GraphCalendar(httpx.Client()).find("Amex Payment Due", date(2026, 10, 30)) is None


def test_build_event_uses_london_date_across_utc_midnight(msg):
    # 23:30 UTC on 24 Jul is 00:30 BST on 25 Jul. 23 Sep is exactly 60 days after the 25th, so the
    # extractor only accepts it when given the London date (from 24 Jul the window ends 22 Sep).
    m = msg(subject="Reminder", received=datetime(2026, 7, 24, 23, 30, tzinfo=timezone.utc))
    spec = build_event(AMEX_RULE, m, "Please pay by: 23 Sep 2026")
    assert spec.date == date(2026, 9, 23)
    assert spec.note.endswith("received 25 Jul 2026")


def _read_timeout(request):
    raise httpx.ReadTimeout("read timed out", request=request)


def test_create_all_day_transport_error_recovers_event_via_find(respx_mock):
    post = respx_mock.post(f"{BASE}/events").mock(side_effect=_read_timeout)
    view = respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": [
        {"id": "E7", "subject": "Amex Payment Due", "start": {"dateTime": "2026-10-30T00:00:00.0000000"}}]})
    assert GraphCalendar(httpx.Client(), sleep=lambda s: None).create_all_day(SPEC) == "E7"
    assert post.call_count == 1 and view.call_count == 1


def test_create_all_day_transport_error_without_event_is_transient_and_not_reposted(respx_mock):
    post = respx_mock.post(f"{BASE}/events").mock(side_effect=_read_timeout)
    view = respx_mock.get(f"{BASE}/calendarView").respond(200, json={"value": []})
    with pytest.raises(TransientError):
        GraphCalendar(httpx.Client(), sleep=lambda s: None).create_all_day(SPEC)
    assert post.call_count == 1 and view.call_count == 1


def test_request_with_retry_transport_retry_default_and_opt_out(respx_mock):
    route = respx_mock.get("https://example.test/x").mock(side_effect=_read_timeout)
    with pytest.raises(TransientError):
        request_with_retry(httpx.Client(), "GET", "https://example.test/x", sleep=lambda s: None)
    assert route.call_count == 4  # default unchanged: first try + MAX_RETRIES
    route.reset()
    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(httpx.Client(), "GET", "https://example.test/x", sleep=lambda s: None, retry_transport=False)
    assert route.call_count == 1
