from datetime import datetime, timedelta, timezone

import pytest

from runner.digest import decide_verdict, render
from runner.run import AccountStats, Summary
from runner.state import State

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)  # 19:00 in Europe/London (BST)


def summary(**kw) -> Summary:
    base = dict(
        started="2026-10-24T18:00:03Z",
        finished="2026-10-24T18:00:09Z",
        dry_run=False,
        accounts={"gmail": AccountStats(fetched=9, processed=9), "outlook": AccountStats(fetched=3, processed=3)},
        important=[],
        held=0,
        review=0,
        calendar_review=0,
        errors=[],
        auth={"gmail": "ok", "outlook": "ok"},
        connect_urls={},
        verdict="QUIET",
    )
    base.update(kw)
    return Summary(**base)


def test_render_matches_spec_format_with_all_sections():
    s = summary(
        important=[
            {"account": "gmail", "from": "First Direct", "subject": "Payment due 20th"},
            {"account": "outlook", "from": "HMRC", "subject": "Message in your account"},
        ],
        calendar_review=1,
        held=3,
        review=1,
    )
    assert render(s) == (
        "📬 Email run 19:00\n"
        "Processed: 12 (Gmail 9, Outlook 3)\n"
        "⭐ Important: 2\n"
        "  • First Direct — \"Payment due 20th\"\n"
        "  • HMRC — \"Message in your account\"\n"
        "🕒 Needs calendar review: 1\n"
        "✋ Held for review (would-be trash): 3\n"
        "📝 Review (unmatched): 1"
    )


def test_render_omits_empty_sections():
    text = render(summary(held=2))
    assert text == "📬 Email run 19:00\nProcessed: 12 (Gmail 9, Outlook 3)\n✋ Held for review (would-be trash): 2"


def test_render_marks_dry_run_and_capped():
    s = summary(dry_run=True, accounts={"gmail": AccountStats(fetched=500, processed=500, capped=True)})
    text = render(s)
    assert text.startswith("📬 Email run 19:00 (dry run)")
    assert "Processed: 500 (Gmail 500)" in text
    assert "capped" in text.lower()


def test_render_error_and_auth_forms():
    s = summary(errors=["gmail: add_label x: HTTP 500"], auth={"gmail": "ok", "outlook": "unauthorized"})
    text = render(s)
    assert "⚠️ Errors: 1" in text and "gmail: add_label x: HTTP 500" in text
    assert "⚠️ Outlook needs re-authorising: run `python -m relay authorize` on the host." in text
    s2 = summary(auth={"gmail": "not_connected", "outlook": "ok"}, connect_urls={"gmail": "http://localhost:10254/x"})
    assert "⚠️ Gmail not connected: http://localhost:10254/x" in render(s2)


def test_verdict_quiet_when_nothing_to_say(tmp_path):
    assert decide_verdict(summary(), State.load(tmp_path / "s.json"), NOW) == "QUIET"


@pytest.mark.parametrize(
    "kw",
    [
        {"important": [{"account": "gmail", "from": "x", "subject": "y"}]},
        {"held": 1},
        {"review": 1},
        {"calendar_review": 1},
        {"errors": ["boom"]},
        {"auth": {"gmail": "ok", "outlook": "unauthorized"}},
    ],
)
def test_verdict_report_on_each_trigger(tmp_path, kw):
    assert decide_verdict(summary(**kw), State.load(tmp_path / "s.json"), NOW) == "REPORT"


def test_auth_condition_suppressed_within_six_hours(tmp_path):
    state = State.load(tmp_path / "s.json")
    s = summary(auth={"gmail": "ok", "outlook": "unauthorized"})
    assert decide_verdict(s, state, NOW) == "REPORT"
    assert decide_verdict(s, state, NOW + timedelta(hours=2)) == "QUIET"
    assert decide_verdict(s, state, NOW + timedelta(hours=7)) == "REPORT"
    # other triggers still report even while the auth line is suppressed
    assert decide_verdict(summary(held=1, auth={"gmail": "ok", "outlook": "unauthorized"}), state, NOW + timedelta(hours=8)) == "REPORT"
