import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runner.providers.base import AuthError
from runner.rules import Rule
from runner.run import RunConfig, Summary, run
from runner.state import State
from tests.runner.fakes import FakeProvider

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
RULES = [
    Rule("*@bank.example", "sender", "em/important", "keep"),
    Rule("*@shop.example", "sender", "em/promotions", "trash"),
    Rule("*@news.example", "sender", "em/newsletters", "archive"),
]


def cfg(tmp_path, **kw) -> RunConfig:
    base = dict(data_dir=tmp_path / "data", rules_path=tmp_path / "rules.yaml", state_path=tmp_path / "state.json")
    base.update(kw)
    return RunConfig(**base)


def m(msg, i, sender, account="gmail", minutes_ago=60):
    return msg(id=f"{account}-{i}", account=account, sender=sender, subject=f"s{i}", received=NOW - timedelta(minutes=minutes_ago))


def test_first_run_uses_backlog_window(tmp_path, msg):
    gmail = FakeProvider("gmail", [m(msg, 1, "a@bank.example")])
    state = State.load(tmp_path / "state.json")
    run(cfg(tmp_path), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert gmail.list_calls[0][0] == NOW - timedelta(days=7)


def test_cap_sets_capped_and_last_run_stops_at_last_processed(tmp_path, msg):
    msgs = [m(msg, i, "a@bank.example", minutes_ago=100 - i) for i in range(1, 6)]  # oldest first
    gmail = FakeProvider("gmail", msgs)
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path, max_messages=3), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert s.accounts["gmail"].capped is True
    assert s.accounts["gmail"].processed == 3
    assert state.last_run("gmail") == msgs[2].received
    assert gmail.list_calls[0][1] == 3


def test_complete_run_advances_to_started(tmp_path, msg):
    gmail = FakeProvider("gmail", [m(msg, 1, "a@bank.example")])
    state = State.load(tmp_path / "state.json")
    run(cfg(tmp_path), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert state.last_run("gmail") == NOW


def test_dry_run_applies_nothing_but_counts(tmp_path, msg):
    gmail = FakeProvider("gmail", [m(msg, 1, "a@shop.example"), m(msg, 2, "x@unknown.example")])
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path, dry_run=True), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert gmail.applied == []
    assert s.held == 1 and s.review == 1 and s.dry_run is True
    assert state.last_run("gmail") is None


def test_auth_error_skips_account_and_sets_auth(tmp_path, msg):
    outlook = FakeProvider("outlook", auth_error=AuthError("401 InvalidAuthenticationToken"))
    gmail = FakeProvider("gmail", [m(msg, 1, "a@bank.example")])
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path), {"gmail": gmail, "outlook": outlook}, state, RULES, now=lambda: NOW)
    assert s.auth == {"gmail": "ok", "outlook": "unauthorized"}
    assert s.accounts["gmail"].processed == 1
    gm = FakeProvider("gmail", auth_error=AuthError("not connected", connect_url="http://localhost:10254/x"))
    s2 = run(cfg(tmp_path), {"gmail": gm}, State.load(tmp_path / "s2.json"), RULES, now=lambda: NOW)
    assert s2.auth["gmail"] == "not_connected" and s2.connect_urls["gmail"] == "http://localhost:10254/x"


def test_duplicate_ids_processed_once(tmp_path, msg):
    one = m(msg, 1, "a@bank.example")
    gmail = FakeProvider("gmail", [one, one])
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert s.accounts["gmail"].processed == 1 and len(gmail.applied) == 1


def test_budget_cutoff_marks_capped(tmp_path, msg):
    msgs = [m(msg, i, "a@bank.example", minutes_ago=100 - i) for i in range(1, 5)]
    gmail = FakeProvider("gmail", msgs, delay=0.03)
    state = State.load(tmp_path / "state.json")
    s = run(cfg(tmp_path, budget_seconds=0.01), {"gmail": gmail}, state, RULES, now=lambda: NOW)
    assert s.accounts["gmail"].capped is True
    assert 1 <= s.accounts["gmail"].processed < 4
    assert len(gmail.applied) == s.accounts["gmail"].processed  # in-flight message completed, no half-applied ones


def test_important_entries_use_display_name(tmp_path, msg):
    gmail = FakeProvider("gmail", [m(msg, 1, '"First Direct" <alerts@bank.example>')])
    s = run(cfg(tmp_path), {"gmail": gmail}, State.load(tmp_path / "state.json"), RULES, now=lambda: NOW)
    assert s.important == [{"account": "gmail", "from": "First Direct", "subject": "s1"}]


def test_summary_json_roundtrip(tmp_path, msg):
    gmail = FakeProvider("gmail", [m(msg, 1, "a@bank.example")])
    s = run(cfg(tmp_path), {"gmail": gmail}, State.load(tmp_path / "state.json"), RULES, now=lambda: NOW)
    d = json.loads(s.to_json())
    assert d["accounts"]["gmail"] == {"fetched": 1, "processed": 1, "errors": 0, "capped": False}
    assert d["started"].endswith("Z") and d["verdict"] in ("REPORT", "QUIET")


# --- main() ---------------------------------------------------------------------------

def _write_rules(tmp_path):
    (tmp_path / "rules.yaml").write_text(
        "rules:\n- pattern: '*@bank.example'\n  match_field: sender\n  label: em/important\n  action: keep\n"
    )


def test_main_prints_digest_and_verdict_lines_last(tmp_path, msg, monkeypatch, capsys):
    import runner.__main__ as cli
    _write_rules(tmp_path)
    gmail = FakeProvider("gmail", [m(msg, 1, "a@bank.example")])
    monkeypatch.setattr(cli, "build_providers", lambda header_names: {"gmail": gmail})
    rc = cli.main(["--data-dir", str(tmp_path / "data"), "--rules", str(tmp_path / "rules.yaml"), "--state", str(tmp_path / "state.json"), "--accounts", "gmail"])
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert rc == 0
    assert out[-2] == "DIGEST: data/last-digest.txt" and out[-1] == "VERDICT: REPORT"
    assert (tmp_path / "data" / "last-digest.txt").read_text(encoding="utf-8").startswith("📬 Email run")
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["accounts"]["gmail"]["last_run"] is not None


def test_main_quiet_when_nothing_to_say(tmp_path, msg, monkeypatch, capsys):
    import runner.__main__ as cli
    (tmp_path / "rules.yaml").write_text(
        "rules:\n- pattern: '*@news.example'\n  match_field: sender\n  label: em/newsletters\n  action: archive\n"
    )
    gmail = FakeProvider("gmail", [m(msg, 1, "a@news.example")])
    monkeypatch.setattr(cli, "build_providers", lambda header_names: {"gmail": gmail})
    rc = cli.main(["--data-dir", str(tmp_path / "data"), "--rules", str(tmp_path / "rules.yaml"), "--state", str(tmp_path / "state.json"), "--accounts", "gmail"])
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert rc == 0 and out[-1] == "VERDICT: QUIET"


def test_main_crash_writes_error_digest_and_returns_1(tmp_path, monkeypatch, capsys):
    import runner.__main__ as cli
    (tmp_path / "rules.yaml").write_text("rules:\n- pattern: '*'\n  match_field: sender\n  label: em/x\n  action: delete\n")
    monkeypatch.setattr(cli, "build_providers", lambda header_names: {})
    rc = cli.main(["--data-dir", str(tmp_path / "data"), "--rules", str(tmp_path / "rules.yaml"), "--state", str(tmp_path / "state.json")])
    out = capsys.readouterr().out.rstrip("\n").splitlines()
    assert rc == 1 and out[-1] == "VERDICT: REPORT"
    text = (tmp_path / "data" / "last-digest.txt").read_text(encoding="utf-8")
    assert text.startswith("⚠️ Email run") and "RulesError" in text
    assert not (tmp_path / "state.json").exists()


def test_main_writes_run_json_and_appends_summary_md(tmp_path, msg, monkeypatch, capsys):
    import runner.__main__ as cli
    _write_rules(tmp_path)
    gmail = FakeProvider("gmail", [m(msg, 1, "a@bank.example")])
    monkeypatch.setattr(cli, "build_providers", lambda header_names: {"gmail": gmail})
    args = ["--data-dir", str(tmp_path / "data"), "--rules", str(tmp_path / "rules.yaml"), "--state", str(tmp_path / "state.json"), "--accounts", "gmail"]
    cli.main(args)
    cli.main(args)
    runs = list((tmp_path / "data" / "runs").glob("*.json"))
    assert len(runs) == 2
    md = (tmp_path / "data" / "last-run-summary.md").read_text(encoding="utf-8")
    assert md.count("📬 Email run") == 2
