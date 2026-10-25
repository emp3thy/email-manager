from datetime import date, datetime, timezone

import pytest

from runner.calendar import calendar_step
from runner.providers.base import AuthError, TransientError
from runner.rules import Rule
from runner.state import State
from tests.runner.fakes import FakeCalendar, FakeProvider

AMEX = "Payment due. Pay by Fri 30 Oct 2026 to avoid interest."
RULE = Rule("*payment due*", "subject", "em/receipts", "calendar_event",
            calendar_title="Amex Payment Due", date_extractor="pay_by")
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 30)


@pytest.fixture
def state(tmp_path):
    return State.load(tmp_path / "state.json")


def step(m, provider, cal, state, dry_run=False):
    return calendar_step(RULE, m, provider, cal, state, NOW, dry_run)


def test_created_path(msg, state):
    m = msg(subject="Payment due")
    cal = FakeCalendar()
    out = step(m, FakeProvider("gmail", bodies={"m1": AMEX}), cal, state)
    assert out.status == "created" and out.date == DAY
    assert cal.created[0].title == "Amex Payment Due"
    assert state.calendar_has("Amex Payment Due", DAY)


def test_state_dedupe_skips_without_calendar_call(msg, state):
    state.calendar_add("Amex Payment Due", DAY, "x", "gmail", NOW)
    cal = FakeCalendar()
    out = step(msg(), FakeProvider("gmail", bodies={"m1": AMEX}), cal, state)
    assert out.status == "skipped" and cal.find_calls == 0 and cal.created == []


def test_calendar_dedupe_records_in_state(msg, state):
    cal = FakeCalendar(existing={("Amex Payment Due", DAY)})
    out = step(msg(), FakeProvider("gmail", bodies={"m1": AMEX}), cal, state)
    assert out.status == "skipped" and cal.created == []
    assert state.calendar_has("Amex Payment Due", DAY)


def test_second_reminder_same_cycle_is_skipped(msg, state):
    p = FakeProvider("gmail", bodies={"a": AMEX, "b": AMEX})
    cal = FakeCalendar()
    o1 = step(msg(id="a", subject="Payment due reminder"), p, cal, state)
    o2 = step(msg(id="b", subject="Minimum payment due"), p, cal, state)
    assert (o1.status, o2.status) == ("created", "skipped")
    assert len(cal.created) == 1


def test_no_date_is_review(msg, state):
    out = step(msg(), FakeProvider("gmail", bodies={"m1": "Thanks"}), FakeCalendar(), state)
    assert (out.status, out.reason) == ("review", "no date")


def test_body_failure_is_review(msg, state):
    out = step(msg(), FakeProvider("gmail"), FakeCalendar(), state)
    assert out.status == "review" and out.reason.startswith("body: ")


def test_create_failure_records_nothing(msg, state):
    cal = FakeCalendar(fail_create=TransientError("503"))
    out = step(msg(), FakeProvider("gmail", bodies={"m1": AMEX}), cal, state)
    assert out.status == "review" and out.reason.startswith("graph: ")
    assert not state.calendar_has("Amex Payment Due", DAY)


def test_dry_run_never_creates(msg, state):
    cal = FakeCalendar()
    out = step(msg(), FakeProvider("gmail", bodies={"m1": AMEX}), cal, state, dry_run=True)
    assert (out.status, out.reason) == ("created", "dry-run")
    assert cal.created == [] and not state.calendar_has("Amex Payment Due", DAY)


def test_auth_error_propagates(msg, state):
    with pytest.raises(AuthError):
        step(msg(), FakeProvider("gmail", bodies={"m1": AMEX}), FakeCalendar(fail=AuthError("403")), state)


def test_body_auth_error_is_distinct(msg, state):
    from runner.calendar import BodyAuthError
    p = FakeProvider("gmail")
    p.get_body = lambda _id: (_ for _ in ()).throw(AuthError("401"))
    with pytest.raises(BodyAuthError):
        step(msg(), p, FakeCalendar(), state)


def test_dry_run_same_cycle_second_is_skipped(msg, state):
    p = FakeProvider("gmail", bodies={"a": AMEX, "b": AMEX})
    cal = FakeCalendar()
    seen = set()
    o1 = calendar_step(RULE, msg(id="a"), p, cal, state, NOW, True, seen)
    o2 = calendar_step(RULE, msg(id="b"), p, cal, state, NOW, True, seen)
    assert (o1.status, o1.reason) == ("created", "dry-run")
    assert (o2.status, o2.reason) == ("skipped", "dry-run")
