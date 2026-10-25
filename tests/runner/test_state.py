import json
from datetime import date, datetime, timedelta, timezone

from runner.state import State

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

DOCUMENTED = {
    "accounts": {
        "gmail": {"last_run": "2026-07-16T09:00:00Z", "initial_cleanup": {"status": "completed", "emails_processed": 1800}},
        "outlook": {"last_run": "2026-07-16T09:00:00Z", "initial_cleanup": {"status": "completed", "emails_processed": 400}},
    },
    "autonomous": {
        "enabled": True,
        "accounts": ["gmail", "outlook"],
        "last_run": "2026-07-16T09:00:00Z",
        "digest": {"channel": "whatsapp", "target": "self"},
        "heartbeat": False,
    },
}


def test_load_missing_file_gives_fresh_document_with_both_accounts(tmp_path):
    s = State.load(tmp_path / "state.json")
    assert s.accounts() == ["gmail", "outlook"]
    assert s.last_run("gmail") is None and s.last_run("outlook") is None
    assert s.backlog_days() == 7
    assert s.data["autonomous"]["digest"] == {"channel": "slack", "target": "gethin"}


def test_load_unparsable_file_gives_fresh_document_and_keeps_backup(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    s = State.load(p)
    assert s.last_run("gmail") is None
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"


def test_load_ignores_tmp_sibling(tmp_path):
    p = tmp_path / "state.json"
    (tmp_path / "state.json.tmp").write_text("{garbage")
    s = State.load(p)
    assert s.last_run("gmail") is None


def test_window_start_uses_backlog_when_no_last_run(tmp_path):
    s = State.load(tmp_path / "state.json")
    assert s.window_start("gmail", NOW) == NOW - timedelta(days=7)


def test_window_start_uses_last_run(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(DOCUMENTED))
    s = State.load(p)
    assert s.window_start("gmail", NOW) == datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


def test_advance_never_moves_backwards(tmp_path):
    s = State.load(tmp_path / "state.json")
    s.advance("gmail", NOW)
    s.advance("gmail", NOW - timedelta(hours=1))
    assert s.last_run("gmail") == NOW
    s.advance("gmail", NOW + timedelta(minutes=1))
    assert s.last_run("gmail") == NOW + timedelta(minutes=1)


def test_save_is_atomic_no_tmp_left_behind(tmp_path):
    p = tmp_path / "state.json"
    s = State.load(p)
    s.advance("outlook", NOW)
    s.save()
    assert p.exists() and not (tmp_path / "state.json.tmp").exists()
    assert json.loads(p.read_text())["accounts"]["outlook"]["last_run"] == "2026-10-24T12:00:00Z"


def test_should_report_suppresses_within_six_hours_then_allows(tmp_path):
    s = State.load(tmp_path / "state.json")
    assert s.should_report("outlook_unauthorized", NOW) is True
    s.mark_reported("outlook_unauthorized", NOW)
    assert s.should_report("outlook_unauthorized", NOW + timedelta(hours=5, minutes=59)) is False
    assert s.should_report("outlook_unauthorized", NOW + timedelta(hours=6, minutes=1)) is True


def test_existing_documented_shape_is_preserved(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(DOCUMENTED))
    s = State.load(p)
    s.save()
    again = json.loads(p.read_text())
    assert again["accounts"]["gmail"]["initial_cleanup"] == DOCUMENTED["accounts"]["gmail"]["initial_cleanup"]
    assert again["autonomous"]["heartbeat"] is False
    assert again["autonomous"]["last_reported"] == {}


def test_calendar_add_and_has_casefold(tmp_path):
    s = State.load(tmp_path / "state.json")
    d = date(2026, 11, 3)
    assert not s.calendar_has("Amex Payment Due", d)
    s.calendar_add("Amex Payment Due", d, "m1", "outlook", NOW)
    assert s.calendar_has(" amex payment due ", d)
    assert not s.calendar_has("Amex Payment Due", d + timedelta(days=1))


def test_calendar_entries_persist_and_trim_90_days(tmp_path):
    p = tmp_path / "state.json"
    s = State.load(p)
    s.calendar_add("Old", NOW.date() - timedelta(days=100), "m1", "gmail", NOW)
    s.calendar_add("Today", NOW.date(), "m2", "gmail", NOW)
    s.save()
    again = State.load(p)
    assert not again.calendar_has("Old", NOW.date() - timedelta(days=100))
    assert again.calendar_has("Today", NOW.date())
    entry = json.loads(p.read_text())["calendar"]["created"]
    assert entry == [{"title": "Today", "date": "2026-10-24", "message_id": "m2",
                      "account": "gmail", "created_at": "2026-10-24T12:00:00Z"}]


def test_backfilled_stamp(tmp_path):
    p = tmp_path / "state.json"
    s = State.load(p)
    assert s.calendar_backfilled_at() is None
    s.mark_backfilled(NOW)
    s.save()
    assert json.loads(p.read_text())["calendar"]["backfilled_at"] == "2026-10-24T12:00:00Z"
    assert State.load(p).calendar_backfilled_at() == NOW


def test_old_state_file_gains_calendar_block(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(DOCUMENTED))
    s = State.load(p)
    assert s.calendar_has("x", NOW.date()) is False
    assert s.calendar_backfilled_at() is None
    s.calendar_add("x", NOW.date(), "m", "gmail", NOW)
    assert s.calendar_has("x", NOW.date())
