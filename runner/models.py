"""Provider-neutral message model used by the rules engine and the policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr


@dataclass(frozen=True)
class Message:
    id: str
    account: str  # "gmail" | "outlook"
    sender: str  # raw From header, e.g. '"Acme" <a@b.com>'
    subject: str
    received: datetime  # tz-aware UTC
    headers: dict[str, str] | None = None  # lower-cased header names; Gmail only

    def sender_address(self) -> str:
        """The bare address, lower-cased; the whole sender lowered if none parses."""
        _, addr = parseaddr(self.sender or "")
        return (addr or self.sender or "").strip().lower()

    def sender_display(self) -> str:
        """The display name when present, otherwise the address."""
        name, addr = parseaddr(self.sender or "")
        return name.strip() or addr.strip() or (self.sender or "").strip()
