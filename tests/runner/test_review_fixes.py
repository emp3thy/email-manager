"""Tests added for the whole-branch review findings (C1, C2, I1, I2, M1)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from runner.providers.base import AuthError
from runner.providers.gmail import BASE, EM_LABELS, GmailProvider
from runner.rules import Rule, RulesError, load_rules, match
from runner.run import RunConfig, run
from runner.state import State
from tests.runner.fakes import FakeProvider
from tests.runner.test_gmail import label_list, meta

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
RULES = [
    Rule("*@bank.example", "sender", "em/important", "keep"),
    Rule("*@news.example", "sender", "em/newsletters", "archive"),
]
NO_SLEEP = lambda s: None  # noqa: E731


def cfg(tmp_path, **kw) -> RunConfig:
    base = dict(data_dir=tmp_path / "data", rules_path=tmp_path / "rules.yaml", state_path=tmp_path / "state.json")
    base.update(kw)
    return RunConfig(**base)


def m(msg, i, sender, account="gmail", minutes_ago=60):
    return msg(id=f"{account}-{i}", account=account, sender=sender, subject=f"s{i}", received=NOW - timedelta(minutes=minutes_ago))


@pytest.fixture
def provider():
    return GmailProvider(client=httpx.Client(), sleep=NO_SLEEP)


# --- C1: Gmail lists newest first; a cap must keep the OLDEST n --------------------------

@respx.mock(base_url=BASE)
def test_cap_keeps_the_oldest_messages_when_gmail_lists_newest_first(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": f"m{i}"} for i in (5, 4, 3, 2, 1)]}))
    for i in range(1, 6):
        respx_mock.get(f"/messages/m{i}").mock(return_value=httpx.Response(200, json=meta(f"m{i}", "x@example.test", "s", i * 1000)))
    out = provider.list_since(datetime(2026, 10, 1, tzinfo=timezone.utc), limit=3)
    assert [x.id for x in out] == ["m1", "m2", "m3"]
    assert provider.truncated is True


@respx.mock(base_url=BASE)
def test_skipped_em_messages_do_not_consume_the_cap(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    held_id = next(l["id"] for l in label_list(EM_LABELS)["labels"] if l["name"] == "em/held")
    respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": f"m{i}"} for i in (4, 3, 2, 1)]}))
    respx_mock.get("/messages/m4").mock(return_value=httpx.Response(200, json=meta("m4", "x@example.test", "s", 4000, label_ids=("INBOX", held_id))))
    for i in (1, 2, 3):
        respx_mock.get(f"/messages/m{i}").mock(return_value=httpx.Response(200, json=meta(f"m{i}", "x@example.test", "s", i * 1000)))
    out = provider.list_since(datetime(2026, 10, 1, tzinfo=timezone.utc), limit=3)
    assert [x.id for x in out] == ["m1", "m2", "m3"]
    assert provider.truncated is False


def test_capped_comes_from_provider_truncation_not_message_count(tmp_path, msg):
    msgs = [m(msg, i, "a@bank.example", minutes_ago=100 - i) for i in range(1, 4)]
    gmail = FakeProvider("gmail", msgs)
    gmail.force_truncated = True
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path, max_messages=10), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert s.accounts["gmail"].capped is True
    assert state.last_run("gmail") == msgs[-1].received  # not advanced to `started`


# --- C2: a failure freezes last_run before it ----------------------------------------------

def test_failed_op_freezes_last_run_before_it_and_next_run_refetches(tmp_path, msg):
    failed = m(msg, 1, "a@news.example", minutes_ago=90)  # archive -> remove_from_inbox fails
    later = m(msg, 2, "b@bank.example", minutes_ago=80)  # keep -> succeeds
    gmail = FakeProvider("gmail", [failed, later], fail_ops={"remove_from_inbox"})
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert s.accounts["gmail"].errors == 1 and s.accounts["gmail"].processed == 1
    assert state.last_run("gmail") is None or state.last_run("gmail") < failed.received
    gmail2 = FakeProvider("gmail", [failed, later])
    run(cfg(tmp_path), {"gmail": gmail2}, state, RULES, now=lambda: NOW)
    assert failed.id in [mid for mid, _ in gmail2.applied]


def test_success_before_failure_still_advances_to_the_success(tmp_path, msg):
    ok = m(msg, 1, "b@bank.example", minutes_ago=90)
    bad = m(msg, 2, "a@news.example", minutes_ago=80)
    gmail = FakeProvider("gmail", [ok, bad], fail_ops={"remove_from_inbox"})
    state = State.load(tmp_path / "state.json")
    run(cfg(tmp_path), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert state.last_run("gmail") == ok.received


# --- I1: AuthError during apply; 403 rate limits are retryable ----------------------------

def test_auth_error_during_apply_skips_rest_of_account_and_continues(tmp_path, msg):
    msgs = [m(msg, 1, "a@bank.example", minutes_ago=90), m(msg, 2, "b@bank.example", minutes_ago=80)]
    gmail = FakeProvider("gmail", msgs)
    original_apply = gmail.apply

    def flaky_apply(mid, op):
        if mid == msgs[0].id:
            raise AuthError("token revoked mid-run")
        return original_apply(mid, op)

    gmail.apply = flaky_apply
    outlook = FakeProvider("outlook", [m(msg, 3, "c@bank.example", account="outlook")])
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path), {"gmail": gmail, "outlook": outlook}, state, RULES, now=lambda: NOW)
    assert s.auth["gmail"] == "unauthorized" and s.auth["outlook"] == "ok"
    assert s.accounts["gmail"].processed == 0 and s.accounts["outlook"].processed == 1
    assert state.last_run("gmail") is None


@respx.mock(base_url=BASE)
def test_403_rate_limit_is_retried_not_treated_as_auth(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    provider.ensure_labels()
    route = respx_mock.post("/messages/m/modify").mock(
        side_effect=[
            httpx.Response(403, json={"error": {"code": 403, "errors": [{"reason": "userRateLimitExceeded"}], "message": "User-rate limit exceeded."}}),
            httpx.Response(200, json={}),
        ]
    )
    provider.apply("m", ("add_label", "em/receipts"))
    assert route.call_count == 2


# --- I2: unknown labels are rejected up front and degrade gracefully ----------------------

@respx.mock(base_url=BASE)
def test_apply_unknown_label_raises_value_error_not_key_error(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    provider.ensure_labels()
    with pytest.raises(ValueError):
        provider.apply("m", ("add_label", "em/not-a-label"))


def test_load_rules_rejects_unknown_label_and_unmovable_archive(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text("rules:\n- pattern: '*'\n  match_field: sender\n  label: em/misc\n  action: keep\n")
    with pytest.raises(RulesError):
        load_rules(p)
    p.write_text("rules:\n- pattern: '*'\n  match_field: sender\n  label: em/spam\n  action: archive\n")
    with pytest.raises(RulesError):
        load_rules(p)
    p.write_text("rules:\n- pattern: '*'\n  match_field: sender\n  label: em/spam\n  action: trash\n")
    assert len(load_rules(p)) == 1


# --- M1 (promoted): display-name text must not satisfy a sender rule ------------------------

def test_sender_rule_does_not_match_a_spoofed_display_name(msg):
    rules = [Rule("*bank.example*", "sender", "em/important", "keep")]
    assert match(msg(sender='"alerts@bank.example" <phish@evil.example>'), rules) is None
    assert match(msg(sender="alerts@bank.example"), rules) is rules[0]
