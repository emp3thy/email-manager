"""Gmail REST client (users.messages / users.labels), unauthenticated by design."""
from __future__ import annotations

import base64
import html
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from ..models import Message
from ..rules import LABELS
from .base import Op, collapse_ws, html_to_text, request_with_retry

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
EM_LABELS = LABELS
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
        self.truncated = False  # set by list_since: more candidates existed than `limit`

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
        """Oldest `limit` unprocessed inbox messages in the window.

        Gmail lists newest first, so every id in the window is collected (ids are
        cheap, 100 per call) and the cap is applied after sorting oldest-first;
        taking the first `limit` ids as listed would keep the newest and let
        `last_run` jump past the oldest forever.
        """
        labels = self._label_map()
        em_ids = {lid for name, lid in labels.items() if name.startswith("em/")}
        ids = self._collect_ids(f"after:{int(start.timestamp())} in:inbox")
        out: list[Message] = []
        for mid in ids:
            data = self._fetch_metadata(mid)
            if em_ids & set(data.get("labelIds", []) or []):
                continue
            out.append(self._to_message(mid, data))
        out.sort(key=lambda m: m.received)
        self.truncated = len(out) > limit
        return out[:limit]

    def list_calendar_candidates(self, start: datetime, limit: int) -> list[Message]:
        """Newest `limit` inbox messages after `start` (oldest first), processed or not (no em/ label skip).

        Gmail lists newest first, so metadata is fetched only for the first `limit` ids.
        """
        ids = self._collect_ids(f"after:{int(start.timestamp())} in:inbox")[:limit]
        out = [self._to_message(mid, self._fetch_metadata(mid)) for mid in ids]
        out.sort(key=lambda m: m.received)
        return out

    def _collect_ids(self, query: str) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        token: str | None = None
        while True:
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
        return ids

    def _fetch_metadata(self, mid: str) -> dict:
        header_params = [("format", "metadata")] + [("metadataHeaders", h) for h in (*BASE_HEADERS, *self.header_names)]
        return self._req("GET", f"{BASE}/messages/{mid}", params=header_params).json()

    def _to_message(self, mid: str, data: dict) -> Message:
        headers = {h["name"].lower(): h.get("value", "") for h in data.get("payload", {}).get("headers", [])}
        received = datetime.fromtimestamp(int(data.get("internalDate", "0")) / 1000, tz=timezone.utc)
        return Message(
            id=mid,
            account=self.name,
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            received=received,
            headers=headers,
        )

    # --- bodies ---------------------------------------------------------------------

    def get_body(self, message_id: str) -> str:
        """Plain-text body: first text/plain part, else first text/html stripped, else the snippet."""
        data = self._req("GET", f"{BASE}/messages/{message_id}", params={"format": "full"}).json()
        payload = data.get("payload") or {}
        plain = self._find_part(payload, "text/plain")
        if plain is not None:
            return collapse_ws(plain)
        markup = self._find_part(payload, "text/html")
        if markup is not None:
            return html_to_text(markup)
        return collapse_ws(html.unescape(data.get("snippet", "") or ""))

    def _find_part(self, part: dict, mime: str) -> str | None:
        raw = (part.get("body") or {}).get("data")
        if part.get("mimeType") == mime and raw:
            padded = raw + "=" * (-len(raw) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        for sub in part.get("parts") or []:
            found = self._find_part(sub, mime)
            if found is not None:
                return found
        return None

    # --- actions --------------------------------------------------------------------

    def apply(self, message_id: str, op: Op) -> None:
        kind, arg = op
        if kind == "add_label":
            label_id = self._label_map().get(arg)
            if not label_id:
                raise ValueError(f"unknown Gmail label {arg!r}")
            body = {"addLabelIds": [label_id]}
        elif kind == "remove_from_inbox":
            body = {"removeLabelIds": ["INBOX"]}
        else:
            raise ValueError(f"gmail cannot apply op {kind!r}")
        self._req("POST", f"{BASE}/messages/{message_id}/modify", json=body)
