from datetime import date
from pathlib import Path

import pytest

from runner.calendar import EXTRACTORS, NoDate, extract_arriving, extract_pay_by

R = date(2026, 10, 25)
AMEX = (Path(__file__).parent / "fixtures" / "amex_body.txt").read_text(encoding="utf-8")


def test_amex_fixture():
    assert extract_pay_by(AMEX, R) == date(2026, 10, 30)


@pytest.mark.parametrize("frag", [
    "Please pay by: 30 October 2026",
    "pay by 30/10/2026",
    "Due date: 2026-10-30",
    "Payment due date is Friday 30 Oct 2026",
    "due on Fri, 30 Oct 2026",
])
def test_pay_by_forms(frag):
    assert extract_pay_by(f"blah {frag} blah", R) == date(2026, 10, 30)


def test_pay_by_without_year_rolls_into_next_year():
    assert extract_pay_by("Please pay by: 5 Jan", date(2026, 12, 28)) == date(2027, 1, 5)


def test_pay_by_without_year_same_year():
    assert extract_pay_by("Please pay by: 30 Oct", R) == date(2026, 10, 30)


def test_pay_by_first_phrase_wins():
    assert extract_pay_by("due date 1 Nov 2026 … please pay by: 30 Oct 2026", R) == date(2026, 11, 1)


def test_pay_by_no_phrase():
    with pytest.raises(NoDate):
        extract_pay_by("Thanks for your payment", R)


def test_pay_by_too_far_ahead():
    with pytest.raises(NoDate, match="out of range"):
        extract_pay_by("pay by 1 Jan 2027", R)  # 68 days


def test_pay_by_too_far_behind():
    with pytest.raises(NoDate, match="out of range"):
        extract_pay_by("pay by 17 Oct 2026", R)  # 8 days


def test_pay_by_phrase_without_date():
    with pytest.raises(NoDate):
        extract_pay_by("pay by the end of the month", R)


def test_arriving_today():
    assert extract_arriving("[Pending] Delivered Arriving today 12 pm - 3 pm", R) == R


def test_arriving_tomorrow():
    assert extract_arriving("Delivered Arriving tomorrow Gethin", R) == date(2026, 10, 26)


def test_arriving_weekday_and_day():
    assert extract_arriving("Arriving Tuesday 27 October", R) == date(2026, 10, 27)


def test_arriving_day_only_resolves_forward():
    assert extract_arriving("Arriving 2 January", date(2026, 12, 28)) == date(2027, 1, 2)


def test_arriving_range_takes_first_day():
    assert extract_arriving("Delivery estimate: 27 October - 29 October", R) == date(2026, 10, 27)


def test_arriving_none():
    with pytest.raises(NoDate):
        extract_arriving("Your order has been placed", R)


def test_extractors_registry():
    assert set(EXTRACTORS) == {"pay_by", "arriving"}


@pytest.mark.parametrize("frag", ["Estimated delivery: 27 October", "Expected delivery 27 October"])
def test_arriving_other_phrases(frag):
    assert extract_arriving(frag, R) == date(2026, 10, 27)


def test_pay_by_phrase_needs_word_boundary():
    with pytest.raises(NoDate):
        extract_pay_by("Your statement is due online. Posted 30 Oct 2026", R)


def test_pay_by_overdue_by_is_not_a_phrase():
    with pytest.raises(NoDate):
        extract_pay_by("Account overdue by 30 Oct 2026", R)


def test_pay_by_later_phrase_used_when_earlier_has_no_date():
    assert extract_pay_by("Due date: see the statement attached to this email for details, thanks. Please pay by: 30 Oct 2026", R) == date(2026, 10, 30)


def test_arriving_past_day_does_not_look_back():
    with pytest.raises(NoDate, match="out of range"):
        extract_arriving("Arriving 20 October", R)
