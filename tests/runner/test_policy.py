import pytest

from runner.policy import BUCKETS, Decision, decide, folder_for
from runner.rules import Rule


def rule(action, label="em/notifications"):
    return Rule("*", "sender", label, action)


TABLE = [
    ("keep", "gmail", (("add_label", "em/notifications"),), "keep"),
    ("keep", "outlook", (("mark_read", ""),), "keep"),
    ("archive", "gmail", (("add_label", "em/notifications"), ("remove_from_inbox", "")), "archive"),
    ("archive", "outlook", (("move_to_folder", "em-notifications"),), "archive"),
    ("trash", "gmail", (("add_label", "em/notifications"), ("add_label", "em/held")), "held"),
    ("trash", "outlook", (("mark_read", ""),), "held"),
    ("calendar_event", "gmail", (("add_label", "em/notifications"),), "calendar"),
    ("calendar_event", "outlook", (("mark_read", ""),), "calendar"),
    (None, "gmail", (("add_label", "em/review"),), "review"),
    (None, "outlook", (("mark_read", ""),), "review"),
]


@pytest.mark.parametrize("action,provider,ops,bucket", TABLE)
def test_policy_table(action, provider, ops, bucket):
    d = decide(rule(action) if action else None, provider)
    assert isinstance(d, Decision)
    assert d.ops == ops
    assert d.bucket == bucket
    assert d.bucket in BUCKETS
    assert d.label == ("em/notifications" if action else None)


def test_important_flag_only_for_em_important():
    assert decide(rule("keep", "em/important"), "gmail").important is True
    assert decide(rule("keep", "em/receipts"), "gmail").important is False
    assert decide(None, "gmail").important is False


def test_folder_for_maps_slash_to_dash():
    assert folder_for("em/receipts") == "em-receipts"
    assert folder_for("em/important") == "em-important"


@pytest.mark.parametrize("action,provider,ops,bucket", TABLE)
def test_decide_never_emits_delete_or_trash_ops(action, provider, ops, bucket):
    d = decide(rule(action) if action else None, provider)
    for name, _ in d.ops:
        assert "delete" not in name and "trash" not in name


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        decide(rule("keep"), "imap")


def test_policy_calendar_bucket():
    from runner.policy import decide
    from runner.rules import Rule
    rule = Rule("*x*", "subject", "em/receipts", "calendar_event", calendar_title="T", date_extractor="pay_by")
    assert decide(rule, "gmail").bucket == "calendar"
