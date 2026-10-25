"""Microsoft Graph mail client for an Outlook.com mailbox, unauthenticated by design."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from ..models import Message
from .base import Op, collapse_ws, html_to_text, request_with_retry

BASE = "https://graph.microsoft.com/v1.0/me"
EM_FOLDERS = ("em-important", "em-newsletters", "em-notifications", "em-promotions", "em-receipts")
PAGE_SIZE = 50
SELECT = "id,receivedDateTime,from,subject,isRead"


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_received(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class GraphProvider:
    name = "outlook"

    def __init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        self.client = client or httpx.Client(timeout=30.0)
        self.sleep = sleep
        self._folders: dict[str, str] | None = None  # displayName -> id
        self.truncated = False  # set by list_since: more candidates existed than `limit`

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        return request_with_retry(self.client, method, url, sleep=self.sleep, **kw)

    # --- folders --------------------------------------------------------------------

    def ensure_labels(self) -> None:
        resp = self._req("GET", f"{BASE}/mailFolders", params={"$top": 100})
        folders = {f["displayName"]: f["id"] for f in resp.json().get("value", [])}
        for name in EM_FOLDERS:
            if name not in folders:
                created = self._req("POST", f"{BASE}/mailFolders", json={"displayName": name})
                folders[name] = created.json()["id"]
        self._folders = folders

    def _folder_map(self) -> dict[str, str]:
        if self._folders is None:
            self.ensure_labels()
        assert self._folders is not None
        return self._folders

    # --- listing --------------------------------------------------------------------

    def list_since(self, start: datetime, limit: int) -> list[Message]:
        self._folder_map()
        return self._list_inbox(f"receivedDateTime ge {_iso_z(start)} and isRead eq false", limit)

    def list_calendar_candidates(self, start: datetime, limit: int) -> list[Message]:
        """Oldest `limit` inbox messages from `start`, read or not. Leaves `truncated` untouched."""
        saved = self.truncated
        try:
            return self._list_inbox(f"receivedDateTime ge {_iso_z(start)}", limit)
        finally:
            self.truncated = saved

    def get_body(self, message_id: str) -> str:
        resp = self._req(
            "GET",
            f"{BASE}/messages/{message_id}",
            params={"$select": "body"},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        body = resp.json().get("body") or {}
        content = body.get("content") or ""
        if (body.get("contentType") or "").lower() == "html":
            return html_to_text(content)
        return collapse_ws(content)

    def _list_inbox(self, filter_expr: str, limit: int) -> list[Message]:
        params = {
            "$filter": filter_expr,
            "$orderby": "receivedDateTime asc",
            "$top": PAGE_SIZE,
            "$select": SELECT,
        }
        url: str | None = f"{BASE}/mailFolders/inbox/messages"
        out: list[Message] = []
        seen: set[str] = set()
        first = True
        self.truncated = False
        while url and len(out) < limit:
            page = self._req("GET", url, params=params if first else None).json()
            first = False
            added = 0
            for item in page.get("value", []) or []:
                mid = item["id"]
                if mid in seen:
                    continue
                if len(out) >= limit:
                    self.truncated = True  # a new message remains beyond the cap
                    break
                seen.add(mid)
                added += 1
                ea = (item.get("from") or {}).get("emailAddress") or {}
                name, addr = (ea.get("name") or "").strip(), (ea.get("address") or "").strip()
                sender = f'"{name}" <{addr}>' if name else addr
                out.append(
                    Message(
                        id=mid,
                        account=self.name,
                        sender=sender,
                        subject=item.get("subject") or "",
                        received=_parse_received(item.get("receivedDateTime", "1970-01-01T00:00:00Z")),
                        headers=None,
                    )
                )
            url = page.get("@odata.nextLink")
            if len(out) >= limit and url:
                self.truncated = True  # the server reports more pages beyond the cap
            if added == 0:
                break  # a page that adds nothing new is a paging fault; never follow it again
        out.sort(key=lambda m: m.received)
        return out[:limit]

    # --- actions --------------------------------------------------------------------

    def apply(self, message_id: str, op: Op) -> None:
        kind, arg = op
        if kind == "mark_read":
            self._req("PATCH", f"{BASE}/messages/{message_id}", json={"isRead": True})
        elif kind == "move_to_folder":
            folder_id = self._folder_map().get(arg)
            if not folder_id:
                raise ValueError(f"unknown Outlook folder {arg!r}")
            self._req("POST", f"{BASE}/messages/{message_id}/move", json={"destinationId": folder_id})
        else:
            raise ValueError(f"outlook cannot apply op {kind!r}")
