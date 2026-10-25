from pathlib import Path

import pytest

from runner.rules import ACTIONS, Rule, RulesError, glob_match, load_rules, match


def test_sender_rule_matches_address_inside_display_name(msg):
    rules = [Rule("*@acme.com", "sender", "em/notifications", "keep")]
    assert match(msg(sender='"Acme Billing" <no-reply@acme.com>'), rules) is rules[0]


def test_glob_is_case_insensitive_and_supports_question_mark():
    assert glob_match("*@Example.COM", "x@example.com") is True
    assert glob_match("order ?onfirmed*", "Order Confirmed #42") is True
    assert glob_match("order ?onfirmed*", "Order Reconfirmed #42") is False


def test_first_match_wins_in_file_order(msg):
    rules = [
        Rule("*@acme.com", "sender", "em/promotions", "trash"),
        Rule("*@acme.com", "sender", "em/important", "keep"),
    ]
    assert match(msg(sender="a@acme.com"), rules).label == "em/promotions"


def test_subject_rule_with_sender_pattern_requires_both(msg):
    r = Rule("*invoice*", "subject", "em/receipts", "keep", sender_pattern="*@shop.example")
    assert match(msg(subject="Your invoice", sender="billing@shop.example"), [r]) is r
    assert match(msg(subject="Your invoice", sender="billing@other.example"), [r]) is None


def test_header_rule_needs_headers_and_header_name(msg):
    r = Rule("*", "header", "em/newsletters", "archive", header_name="List-Unsubscribe")
    assert match(msg(headers={"list-unsubscribe": "<mailto:x>"}), [r]) is r
    assert match(msg(headers=None), [r]) is None
    assert match(msg(headers={"precedence": "bulk"}), [r]) is None


def test_load_rules_rejects_unknown_action_and_missing_field(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text("rules:\n- pattern: '*'\n  match_field: sender\n  label: em/x\n  action: delete\n")
    with pytest.raises(RulesError):
        load_rules(bad)
    bad.write_text("rules:\n- pattern: '*'\n  label: em/x\n  action: keep\n")
    with pytest.raises(RulesError):
        load_rules(bad)
    bad.write_text("rules:\n- pattern: '*'\n  match_field: header\n  label: em/x\n  action: keep\n")
    with pytest.raises(RulesError):
        load_rules(bad)


def test_load_rules_ignores_bookkeeping_keys(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "settings: {privacy_mode: headers_first}\n"
        "rules:\n- pattern: '*@a.b'\n  match_field: sender\n  label: em/receipts\n  action: keep\n"
        "  last_matched: '2026-01-01'\n  retention_days: 7\n  calendar_title: x\n"
    )
    rules = load_rules(p)
    assert len(rules) == 1 and rules[0].action == "keep" and rules[0].sender_pattern is None


def test_repo_rules_file_loads_if_present():
    p = Path(__file__).resolve().parents[2] / "rules.yaml"
    if not p.exists():
        pytest.skip("no rules.yaml in checkout")
    rules = load_rules(p)
    assert len(rules) > 100 and all(r.action in ACTIONS for r in rules)


def _cal_rules(tmp_path, extra):
    p = tmp_path / "r.yaml"
    p.write_text(
        "rules:\n- pattern: '*payment*due*'\n  match_field: subject\n  label: em/important\n"
        "  action: calendar_event\n" + extra,
        encoding="utf-8",
    )
    return p


def test_calendar_rule_loads_fields(tmp_path):
    p = _cal_rules(tmp_path, "  calendar_title: Amex Payment Due\n  date_extractor: pay_by\n")
    r = load_rules(p)[0]
    assert (r.date_extractor, r.calendar_title, r.calendar_title_from) == ("pay_by", "Amex Payment Due", None)


def test_calendar_rule_requires_extractor(tmp_path):
    p = _cal_rules(tmp_path, "  calendar_title: Amex Payment Due\n")
    with pytest.raises(RulesError, match="rule #1 needs date_extractor"):
        load_rules(p)


def test_calendar_rule_rejects_unknown_extractor(tmp_path):
    p = _cal_rules(tmp_path, "  calendar_title: X\n  date_extractor: bogus\n")
    with pytest.raises(RulesError, match="rule #1 needs date_extractor"):
        load_rules(p)


def test_calendar_rule_requires_a_title(tmp_path):
    p = _cal_rules(tmp_path, "  date_extractor: arriving\n")
    with pytest.raises(RulesError, match="needs calendar_title or calendar_title_from"):
        load_rules(p)


def test_calendar_rule_title_from_subject(tmp_path):
    p = _cal_rules(tmp_path, "  calendar_title_from: subject\n  date_extractor: arriving\n")
    assert load_rules(p)[0].calendar_title_from == "subject"


def test_calendar_rule_unknown_title_from(tmp_path):
    p = _cal_rules(tmp_path, "  calendar_title_from: body\n  date_extractor: arriving\n")
    with pytest.raises(RulesError, match="unknown calendar_title_from"):
        load_rules(p)


def test_non_calendar_rule_ignores_calendar_fields(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(
        "rules:\n- pattern: '*x*'\n  match_field: subject\n  label: em/receipts\n  action: keep\n"
        "  date_extractor: bogus\n  calendar_title_from: body\n",
        encoding="utf-8",
    )
    r = load_rules(p)[0]
    assert (r.date_extractor, r.calendar_title, r.calendar_title_from) == (None, None, None)


def test_real_rules_file_calendar_rules_have_extractors():
    rules = load_rules(Path(__file__).parents[2] / "rules.yaml")
    cal = [r for r in rules if r.action == "calendar_event"]
    assert len(cal) == 5 and all(r.date_extractor in ("pay_by", "arriving") for r in cal)
