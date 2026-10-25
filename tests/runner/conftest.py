from datetime import datetime, timezone

import pytest

from runner.models import Message


@pytest.fixture
def msg():
    """Factory for synthetic messages. Never uses real senders."""

    def _make(**overrides):
        base = dict(
            id="m1",
            account="gmail",
            sender="someone@example.test",
            subject="Hello",
            received=datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc),
            headers=None,
        )
        base.update(overrides)
        return Message(**base)

    return _make
