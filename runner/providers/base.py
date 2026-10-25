"""Shared provider interface, errors and the retrying HTTP helper.

Providers carry no credentials: inside the NanoClaw container every request
goes through the OneCLI proxy, which injects the account's credential.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime
from typing import Callable, Protocol

import httpx

from ..models import Message

Op = tuple[str, str]

MAX_RETRIES = 3
DEFAULT_RETRY_AFTER = 2.0
RETRYABLE = (429, 502, 503, 504)


class AuthError(Exception):
    """The account cannot be used this run (not connected, token rejected)."""

    def __init__(self, message: str, connect_url: str | None = None):
        super().__init__(message)
        self.connect_url = connect_url


class TransientError(Exception):
    """A request failed after retries; the message is skipped and retried next run."""


class Provider(Protocol):
    name: str

    def ensure_labels(self) -> None: ...

    def list_since(self, start: datetime, limit: int) -> list[Message]: ...

    def apply(self, message_id: str, op: Op) -> None: ...

    def get_body(self, message_id: str) -> str: ...

    def list_calendar_candidates(self, start: datetime, limit: int) -> list[Message]: ...


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def html_to_text(markup: str) -> str:
    """Plain text from HTML: drop style/script blocks, strip tags, unescape entities, collapse whitespace."""
    markup = re.sub(r"(?is)<(style|script)\b.*?</\1\s*>", " ", markup)
    return collapse_ws(html.unescape(re.sub(r"<[^>]+>", " ", markup)))


def _auth_error_from(resp: httpx.Response) -> AuthError:
    connect_url = None
    detail = resp.text[:200]
    try:
        body = resp.json()
        if isinstance(body, dict):
            connect_url = body.get("connect_url") or body.get("secret_url")
            detail = body.get("message") or body.get("error") or detail
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or str(detail)
    except ValueError:
        pass
    return AuthError(f"{resp.request.method} {resp.request.url.host}: HTTP {resp.status_code} {detail}", connect_url)


def _is_rate_limit_403(resp: httpx.Response) -> bool:
    """Gmail signals quota exhaustion with 403 + reason *RateLimitExceeded; that is retryable, not auth."""
    try:
        body = resp.json()
    except ValueError:
        return False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    reasons = {e.get("reason", "") for e in err.get("errors", []) if isinstance(e, dict)}
    return any(r in ("rateLimitExceeded", "userRateLimitExceeded") for r in reasons)


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    sleep: Callable[[float], None] = time.sleep,
    retry_transport: bool = True,
    **kw,
) -> httpx.Response:
    """Issue a request; raise AuthError on 401/403, retry 429/5xx up to MAX_RETRIES.

    Transport errors (timeouts, dropped connections) are retried too, unless
    ``retry_transport`` is False: then the httpx error is raised as is on the
    first occurrence, so the caller of a non-idempotent request can check
    whether it took effect before trying again.
    """
    attempt = 0
    while True:
        try:
            resp = client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            if not retry_transport:
                raise
            if attempt >= MAX_RETRIES:
                raise TransientError(f"{method} {url}: {exc}") from exc
            attempt += 1
            sleep(DEFAULT_RETRY_AFTER)
            continue
        if resp.status_code in (401, 403) and not _is_rate_limit_403(resp):
            raise _auth_error_from(resp)
        if resp.status_code in RETRYABLE or (resp.status_code == 403 and _is_rate_limit_403(resp)):
            if attempt >= MAX_RETRIES:
                raise TransientError(f"{method} {url}: HTTP {resp.status_code} after {attempt} retries")
            attempt += 1
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else DEFAULT_RETRY_AFTER
            except ValueError:
                delay = DEFAULT_RETRY_AFTER
            sleep(delay)
            continue
        if resp.status_code >= 400:
            raise TransientError(f"{method} {url}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp
