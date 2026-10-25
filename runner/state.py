"""state.json: per-account run position and report suppression.

Loading never raises: a missing file yields a fresh document, an unparsable
one is set aside as `state.json.corrupt-<ts>` and replaced by a fresh
document. Saving is atomic (tmp + replace). Timestamps are ISO-8601 UTC `Z`.
"""
from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_ACCOUNTS = ["gmail", "outlook"]
DEFAULT_BACKLOG_DAYS = 7


def _fresh() -> dict:
    return {
        "accounts": {
            name: {"last_run": None, "initial_cleanup": {"status": "not_started", "emails_processed": 0}}
            for name in DEFAULT_ACCOUNTS
        },
        "autonomous": {
            "enabled": True,
            "accounts": list(DEFAULT_ACCOUNTS),
            "last_run": None,
            "backlog_days": DEFAULT_BACKLOG_DAYS,
            "digest": {"channel": "slack", "target": "gethin"},
            "last_reported": {},
        },
    }


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class State:
    def __init__(self, path: Path, data: dict):
        self.path = Path(path)
        self.data = data
        self.data.setdefault("accounts", {})
        auto = self.data.setdefault("autonomous", {})
        auto.setdefault("accounts", list(DEFAULT_ACCOUNTS))
        auto.setdefault("backlog_days", DEFAULT_BACKLOG_DAYS)
        auto.setdefault("last_reported", {})
        for name in auto["accounts"]:
            self.data["accounts"].setdefault(name, {"last_run": None})

    @classmethod
    def load(cls, path: Path) -> "State":
        path = Path(path)
        if not path.exists():
            return cls(path, _fresh())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state is not a JSON object")
        except (OSError, ValueError):
            backup = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
            try:
                os.replace(path, backup)
            except OSError:
                pass
            return cls(path, _fresh())
        return cls(path, data)

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    # --- accounts and windows -------------------------------------------------

    def accounts(self) -> list[str]:
        return list(self.data["autonomous"].get("accounts") or DEFAULT_ACCOUNTS)

    def backlog_days(self) -> int:
        return int(self.data["autonomous"].get("backlog_days") or DEFAULT_BACKLOG_DAYS)

    def last_run(self, account: str) -> datetime | None:
        return _parse(self.data["accounts"].get(account, {}).get("last_run"))

    def window_start(self, account: str, now: datetime) -> datetime:
        return self.last_run(account) or (now - timedelta(days=self.backlog_days()))

    def advance(self, account: str, ts: datetime) -> None:
        current = self.last_run(account)
        if current is None or ts > current:
            self.data["accounts"].setdefault(account, {})["last_run"] = _iso(ts)
            auto_last = _parse(self.data["autonomous"].get("last_run"))
            if auto_last is None or ts > auto_last:
                self.data["autonomous"]["last_run"] = _iso(ts)

    # --- report suppression ---------------------------------------------------

    def should_report(self, condition: str, now: datetime, hours: int = 6) -> bool:
        last = _parse(self.data["autonomous"]["last_reported"].get(condition))
        return last is None or now - last >= timedelta(hours=hours)

    def mark_reported(self, condition: str, now: datetime) -> None:
        self.data["autonomous"]["last_reported"][condition] = _iso(now)

    def snapshot(self) -> dict:
        return copy.deepcopy(self.data)
