"""Microsoft identity platform: one-time authorization and refresh-token exchange."""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
from typing import Callable

import httpx

from .config import RelayConfig

SCOPES = "offline_access User.Read Mail.ReadWrite Calendars.ReadWrite"
REDIRECT_URI = "http://localhost:3333/auth/callback"
CALLBACK_PATH = "/auth/callback"


class RelayError(Exception):
    def __init__(self, error: str, description: str = ""):
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description


def auth_url(cfg: RelayConfig) -> str:
    return f"https://login.microsoftonline.com/{cfg.tenant}/oauth2/v2.0/authorize"


def token_url(cfg: RelayConfig) -> str:
    return f"https://login.microsoftonline.com/{cfg.tenant}/oauth2/v2.0/token"


def _post_token(cfg: RelayConfig, data: dict, client: httpx.Client | None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        resp = client.post(token_url(cfg), data=data)
    finally:
        if own:
            client.close()
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        raise RelayError(body.get("error", f"http_{resp.status_code}"), body.get("error_description", resp.text[:200]))
    return body


def refresh(
    cfg: RelayConfig, refresh_token: str, scope: str | None = None, client: httpx.Client | None = None
) -> dict:
    """Exchange a refresh token; the returned document carries the rotated refresh token.

    ``scope`` is the scope saved at the last authorize/refresh. It is sent only
    when given: asking for more than the user consented to (for instance after
    SCOPES gains a permission) makes Microsoft reject the refresh, so the
    refresh never requests SCOPES itself. Without a scope the token keeps the
    permissions it was granted.
    """
    data = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if scope:
        data["scope"] = scope
    body = _post_token(cfg, data, client)
    if not body.get("access_token"):
        raise RelayError("no_access_token", "token endpoint returned no access_token")
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token") or refresh_token,
        "expires_in": int(body.get("expires_in", 0)),
        "scope": body.get("scope", ""),
    }


def print_url(url: str) -> None:
    print("Open this URL in your browser and sign in:\n\n" + url + "\n", flush=True)


def authorize(
    cfg: RelayConfig,
    open_browser: Callable[[str], None] = print_url,
    client: httpx.Client | None = None,
    port: int = 3333,
    timeout_seconds: float = 900.0,
) -> dict:
    """Authorization-code flow with PKCE; waits for the local callback."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = auth_url(cfg) + "?" + urllib.parse.urlencode(
        {
            "client_id": cfg.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )

    got: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - signature fixed by the base class
            return

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            got.update({k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>email-manager relay: sign-in received. You can close this tab.</h2>")
            done.set()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1.0
    thread = threading.Thread(target=lambda: _serve_until(server, done, timeout_seconds), daemon=True)
    thread.start()
    open_browser(url)
    done.wait(timeout_seconds)
    server.server_close()

    if "error" in got:
        raise RelayError(got["error"], got.get("error_description", ""))
    if "code" not in got:
        raise RelayError("timeout", f"no callback received within {int(timeout_seconds)} seconds")
    if got.get("state") != state:
        raise RelayError("state_mismatch", "callback state does not match the request")

    body = _post_token(
        cfg,
        {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "grant_type": "authorization_code",
            "code": got["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "scope": SCOPES,
        },
        client,
    )
    if not body.get("refresh_token"):
        raise RelayError("no_refresh_token", "sign-in succeeded but no refresh token was issued (offline_access missing?)")
    return {
        "access_token": body.get("access_token", ""),
        "refresh_token": body["refresh_token"],
        "expires_in": int(body.get("expires_in", 0)),
        "scope": body.get("scope", ""),
    }


def _serve_until(server: http.server.HTTPServer, done: threading.Event, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not done.is_set() and time.monotonic() < deadline:
        server.handle_request()
