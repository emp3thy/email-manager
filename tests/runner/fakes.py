"""In-memory providers for orchestration tests. No network, no real senders."""
from __future__ import annotations

import time
from datetime import datetime

from runner.models import Message
from runner.providers.base import AuthError, Op, TransientError


class FakeProvider:
    def __init__(
        self,
        name: str,
        messages: list[Message] | None = None,
        fail_ops: set[str] | None = None,
        auth_error: AuthError | None = None,
        delay: float = 0.0,
        bodies: dict[str, str] | None = None,
    ):
        self.name = name
        self.messages = list(messages or [])
        self.fail_ops = set(fail_ops or ())
        self.auth_error = auth_error
        self.delay = delay
        self.bodies = dict(bodies or {})
        self.body_calls: list[str] = []
        self.candidate_calls: list[tuple[datetime, int]] = []
        self.applied: list[tuple[str, Op]] = []
        self.list_calls: list[tuple[datetime, int]] = []
        self.ensure_calls = 0
        self.truncated = False
        self.force_truncated = False

    def ensure_labels(self) -> None:
        self.ensure_calls += 1
        if self.auth_error:
            raise self.auth_error

    def list_since(self, start: datetime, limit: int) -> list[Message]:
        self.list_calls.append((start, limit))
        if self.auth_error:
            raise self.auth_error
        window = sorted((m for m in self.messages if m.received > start), key=lambda m: m.received)
        self.truncated = self.force_truncated or len(window) > limit
        return window[:limit]

    def get_body(self, message_id: str) -> str:
        self.body_calls.append(message_id)
        if message_id not in self.bodies:
            raise TransientError("no body")
        return self.bodies[message_id]

    def list_calendar_candidates(self, start: datetime, limit: int) -> list[Message]:
        self.candidate_calls.append((start, limit))
        if self.auth_error:
            raise self.auth_error
        newest = sorted((m for m in self.messages if m.received > start), key=lambda m: m.received, reverse=True)[:limit]
        return sorted(newest, key=lambda m: m.received)

    def apply(self, message_id: str, op: Op) -> None:
        if self.delay:
            time.sleep(self.delay)
        if op[0] in self.fail_ops:
            raise TransientError(f"{op[0]} failed for {message_id}")
        self.applied.append((message_id, op))


class FakeCalendar:
    def __init__(self, existing=(), fail: Exception | None = None, fail_create: Exception | None = None):
        self.existing = set(existing)
        self.fail = fail
        self.fail_create = fail_create
        self.find_calls = 0
        self.created: list = []

    def find(self, title, day):
        self.find_calls += 1
        if self.fail:
            raise self.fail
        return "E-existing" if (title, day) in self.existing else None

    def create_all_day(self, spec):
        if self.fail:
            raise self.fail
        if self.fail_create:
            raise self.fail_create
        self.created.append(spec)
        return f"E{len(self.created)}"
