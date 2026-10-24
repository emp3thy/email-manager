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
    ):
        self.name = name
        self.messages = list(messages or [])
        self.fail_ops = set(fail_ops or ())
        self.auth_error = auth_error
        self.delay = delay
        self.applied: list[tuple[str, Op]] = []
        self.list_calls: list[tuple[datetime, int]] = []
        self.ensure_calls = 0

    def ensure_labels(self) -> None:
        self.ensure_calls += 1
        if self.auth_error:
            raise self.auth_error

    def list_since(self, start: datetime, limit: int) -> list[Message]:
        self.list_calls.append((start, limit))
        if self.auth_error:
            raise self.auth_error
        return [m for m in self.messages if m.received > start][:limit]

    def apply(self, message_id: str, op: Op) -> None:
        if self.delay:
            time.sleep(self.delay)
        if op[0] in self.fail_ops:
            raise TransientError(f"{op[0]} failed for {message_id}")
        self.applied.append((message_id, op))
