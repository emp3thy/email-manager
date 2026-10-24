"""Gmail REST client (users.messages / users.labels), unauthenticated by design."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from ..models import Message
from .base import Op, request_with_retry

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
EM_LABELS = (
    "em/important",
    "em/newsletters",
    "em/notifications",
    "em/promotions",
    "em/receipts",
    "em/spam",
    "em/held",
    "em/review",
)
PAGE_SIZE = 100
BASE_HEADERS = ("From", "Subject", "Date")


class GmailProvider:
    name = "gmail"

    def __init__(
        self,
        client: httpx.Client | None = None,
        header_names: list[str] | tuple[str, ...] = (),
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client or httpx.Client(timeout=30.0)
        self.header_names = [h for h in header_names if h and h not in BASE_HEADERS]
        self.sleep = sleep
        self._labels: dict[str, str] | None = None  # name -> id

    # --- labels -------------------------------------------------------------------

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        return request_with_retry(self.client, method, url, sleep=self.sleep, **kw)

    def ensure_labels(self) -> None:
        resp = self._req("GET", f"{BASE}/labels")
        labels = {l["name"]: l["id"] for l in resp.json().get("labels", [])}
        for name in EM_LABELS:
            if name not in labels:
                created = self._req(
                    "POST",
                    f"{BASE}/labels",
                    json={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
                )
                labels[name] = created.json()["id"]
        self._labels = labels

    def _label_map(self) -> dict[str, str]:
        if self._labels is None:
            self.ensure_labels()
        assert self._labels is not None
        return self._labels

    # --- listing --------------------------------------------------------------------

    def list_since(self, start: datetime, limit: int) -> list[Message]:
        labels = self._label_map()
        em_ids = {lid for name, lid in labels.items() if name.startswith("em/")}
        query = f"after:{int(start.timestamp())} in:inbox"
        ids: list[str] = []
        seen: set[str] = set()
        token: str | None = None
        while len(ids) < limit:
            params: dict[str, str | int] = {"q": query, "maxResults": PAGE_SIZE}
            if token:
                params["pageToken"] = token
            page = self._req("GET", f"{BASE}/messages", params=params).json()
            added = 0
            for item in page.get("messages", []) or []:
                mid = item["id"]
                if mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
                    added += 1
            token = page.get("nextPageToken")
            if not token or added == 0:
                break  # no token, or a page that adds nothing new (paging fault): stop
        out: list[Message] = []
        header_params = [("format", "metadata")] + [("metadataHeaders", h) for h in (*BASE_HEADERS, *self.header_names)]
        for mid in ids:
            data = self._req("GET", f"{BASE}/messages/{mid}", params=header_params).json()
            if em_ids & set(data.get("labelIds", []) or []):
                continue
            headers = {h["name"].lower(): h.get("value", "") for h in data.get("payload", {}).get("headers", [])}
            received = datetime.fromtimestamp(int(data.get("internalDate", "0")) / 1000, tz=timezone.utc)
            out.append(
                Message(
                    id=mid,
                    account=self.name,
                    sender=headers.get("from", ""),
                    subject=headers.get("subject", ""),
                    received=received,
                    headers=headers,
                )
            )
        out.sort(key=lambda m: m.received)
        return out[:limit]

    # --- actions --------------------------------------------------------------------

    def apply(self, message_id: str, op: Op) -> None:
        kind, arg = op
        if kind == "add_label":
            body = {"addLabelIds": [self._label_map()[arg]]}
        elif kind == "remove_from_inbox":
            body = {"removeLabelIds": ["INBOX"]}
        else:
            raise ValueError(f"gmail cannot apply op {kind!r}")
        self._req("POST", f"{BASE}/messages/{message_id}/modify", json=body)
