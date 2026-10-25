from datetime import datetime, timedelta, timezone

from runner.providers.base import AuthError, TransientError
from runner.rules import Rule
from runner.run import RunConfig, run
from runner.state import State
from tests.runner.fakes import FakeCalendar, FakeProvider

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
AMEX = "Payment due. Pay by Fri 30 Oct 2026 to avoid interest."
CAL_RULE = Rule("*payment due*", "subject", "em/receipts", "calendar_event",
                calendar_title="Amex Payment Due", date_extractor="pay_by")
TRASH_RULE = Rule("*@shop.example", "sender", "em/promotions", "trash")
RULES = [CAL_RULE, TRASH_RULE]


def cfg(tmp_path, **kw) -> RunConfig:
    base = dict(data_dir=tmp_path / "data", rules_path=tmp_path / "rules.yaml",
                state_path=tmp_path / "state.json", accounts=["outlook"])
    base.update(kw)
    return RunConfig(**base)


def amex(msg, days_ago=10, id="a1"):
    return msg(id=id, account="outlook", sender="amex@card.example", subject="Payment due",
               received=NOW - timedelta(days=days_ago))


def go(tmp_path, provider, cal=None, rules=RULES, state=None, **kw):
    state = state or State.load(tmp_path / "state.json")
    cal = cal or FakeCalendar()
    summary = run(cfg(tmp_path, **kw), {"outlook": provider}, state, rules, now=lambda: NOW, calendar=cal)
    return summary, state, cal


def test_backfill_runs_once_and_stamps(tmp_path, msg):
    p = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    s, state, cal = go(tmp_path, p)
    assert len(cal.created) == 1 and len(s.calendar_created) == 1
    assert state.calendar_backfilled_at() == NOW
    assert len(p.candidate_calls) == 1
    go(tmp_path, p, state=state)
    assert len(p.candidate_calls) == 1


def test_backfill_lists_processed_mail_too(tmp_path, msg):
    m = amex(msg)
    p = FakeProvider("outlook", [m], bodies={"a1": AMEX})
    p.applied.append(("a1", ("move", "em/receipts")))  # already handled by an earlier run
    s, _, cal = go(tmp_path, p)
    assert len(cal.created) == 1


def test_backfill_applies_only_calendar_rules(tmp_path, msg):
    shop = msg(id="s1", account="outlook", sender="x@shop.example", subject="Sale",
               received=NOW - timedelta(days=10))
    p = FakeProvider("outlook", [shop])
    s, _, cal = go(tmp_path, p)
    assert p.applied == [] and s.held == 0 and cal.created == []
    assert p.body_calls == []


def test_backfill_does_no_mail_ops(tmp_path, msg):
    p = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    go(tmp_path, p)
    assert p.applied == []


def test_backfill_dry_run_does_not_stamp(tmp_path, msg):
    p = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    s, state, cal = go(tmp_path, p, dry_run=True)
    assert state.calendar_backfilled_at() is None
    assert cal.created == [] and len(s.calendar_created) == 1


def test_backfill_exception_does_not_stamp(tmp_path, msg):
    class Boom(FakeProvider):
        def list_calendar_candidates(self, start, limit):
            raise TransientError("graph down")

    recent = msg(id="r1", account="outlook", sender="x@shop.example", subject="Sale",
                 received=NOW - timedelta(minutes=30))
    p = Boom("outlook", [recent])
    s, state, _ = go(tmp_path, p)
    assert any(e.startswith("backfill: outlook:") for e in s.errors)
    assert state.calendar_backfilled_at() is None
    assert s.accounts["outlook"].processed == 1


def test_backfill_provider_auth_error_moves_on_and_does_not_stamp(tmp_path, msg):
    p = FakeProvider("outlook", auth_error=AuthError("401"))
    s, state, _ = go(tmp_path, p)
    assert s.auth["outlook"] == "unauthorized"
    assert state.calendar_backfilled_at() is None


def test_backfill_auth_error_on_one_account_still_scans_the_next_but_does_not_stamp(tmp_path, msg):
    gmail = FakeProvider("gmail", auth_error=AuthError("401"))
    outlook = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    state = State.load(tmp_path / "state.json")
    cal = FakeCalendar()
    run(cfg(tmp_path, accounts=["gmail", "outlook"]), {"gmail": gmail, "outlook": outlook}, state, RULES,
        now=lambda: NOW, calendar=cal)
    assert len(cal.created) == 1
    assert state.calendar_backfilled_at() is None


def test_backfill_body_auth_error_does_not_stamp(tmp_path, msg):
    class BodyAuth(FakeProvider):
        def get_body(self, message_id):
            self.body_calls.append(message_id)
            raise AuthError("401")

    p = BodyAuth("outlook", [amex(msg)])
    s, state, cal = go(tmp_path, p)
    assert any(e.startswith("backfill: outlook: auth:") for e in s.errors)
    assert cal.created == []
    assert state.calendar_backfilled_at() is None


def test_backfill_both_accounts_clean_stamps(tmp_path, msg):
    g = msg(id="g1", account="gmail", sender="amex@card.example", subject="Payment due",
            received=NOW - timedelta(days=10))
    gmail = FakeProvider("gmail", [g], bodies={"g1": AMEX})
    outlook = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path, accounts=["gmail", "outlook"]), {"gmail": gmail, "outlook": outlook}, state, RULES,
            now=lambda: NOW, calendar=FakeCalendar())
    assert len(gmail.candidate_calls) == 1 and len(outlook.candidate_calls) == 1
    assert state.calendar_backfilled_at() == NOW


def test_backfill_skips_past_dated_events_but_creates_future_ones(tmp_path, msg):
    arriving = msg(id="z1", account="outlook", sender="ship@amazon.example", subject="Ordered: 'Desk Lamp'",
                   received=NOW - timedelta(days=10))
    p = FakeProvider("outlook", [arriving, amex(msg)], bodies={"z1": "Arriving tomorrow", "a1": AMEX})
    rules = [Rule("*Ordered*", "subject", "em/receipts", "calendar_event",
                  calendar_title_from="subject", date_extractor="arriving"), CAL_RULE]
    s, state, cal = go(tmp_path, p, rules=rules)
    assert s.calendar_skipped == 1
    assert [c.title for c in cal.created] == ["Amex Payment Due"]
    assert not state.calendar_has("Amazon: Desk Lamp", (NOW - timedelta(days=9)).date())
    assert s.calendar_review == 0


def test_backfill_calendar_auth_error_turns_calendar_off_for_main_loop(tmp_path, msg):
    recent = msg(id="r1", account="outlook", sender="amex@card.example", subject="Payment due",
                 received=NOW - timedelta(minutes=30))
    p = FakeProvider("outlook", [amex(msg), recent], bodies={"a1": AMEX, "r1": AMEX})
    cal = FakeCalendar(fail=AuthError("401"))
    s, state, _ = go(tmp_path, p, cal=cal)
    assert s.auth["calendar"] == "unauthorized"
    assert cal.find_calls == 1  # backfill tried once; main loop never called Graph
    assert p.body_calls == ["a1"]
    assert s.calendar_review >= 1


def test_backfill_window_is_14_days(tmp_path, msg):
    p = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    go(tmp_path, p)
    assert p.candidate_calls[0][0] == NOW - timedelta(days=14)


def test_backfill_auth_mark_is_cleared_by_successful_main_loop(tmp_path, msg):
    class AuthOnCandidates(FakeProvider):
        def list_calendar_candidates(self, start, limit):
            raise AuthError("401")

    p = AuthOnCandidates("outlook", [])
    s, _, _ = go(tmp_path, p)
    assert s.auth["outlook"] == "ok"
    assert len([e for e in s.errors if e.startswith("backfill: outlook:")]) == 1


def test_first_run_overlap_creates_once(tmp_path, msg):
    m = msg(id="o1", account="outlook", sender="amex@card.example", subject="Payment due",
            received=NOW - timedelta(minutes=30))
    p = FakeProvider("outlook", [m], bodies={"o1": AMEX})
    s, state, cal = go(tmp_path, p)
    assert len(cal.created) == 1 and len(s.calendar_created) == 1
    assert s.calendar_skipped == 1
    assert state.calendar_has("Amex Payment Due", datetime(2026, 10, 30).date())
    assert state.calendar_backfilled_at() == NOW


def test_backfill_calendar_step_exception_counts_review_and_does_not_stamp(tmp_path, msg):
    p = FakeProvider("outlook", [amex(msg)], bodies={"a1": AMEX})
    s, state, _ = go(tmp_path, p, cal=FakeCalendar(fail=KeyError("id")))
    assert s.calendar_review >= 1
    assert any(e.startswith("backfill: outlook:") for e in s.errors)
    assert state.calendar_backfilled_at() is None
