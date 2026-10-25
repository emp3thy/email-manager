import threading
import urllib.parse
from pathlib import Path

import httpx
import pytest
import respx

from relay.config import RelayConfig
from relay.ms import REDIRECT_URI, SCOPES, RelayError, authorize, refresh, token_url

CFG = RelayConfig(client_id="cid-123", client_secret="sec-456", tenant="consumers", config_dir=Path("."))


@respx.mock
def test_refresh_posts_refresh_grant_and_returns_rotated_token(respx_mock):
    route = respx_mock.post(token_url(CFG)).mock(
        return_value=httpx.Response(200, json={"access_token": "at2", "refresh_token": "rt2", "expires_in": 3599, "scope": SCOPES})
    )
    out = refresh(CFG, "rt1", client=httpx.Client())
    form = dict(urllib.parse.parse_qsl(route.calls[0].request.read().decode()))
    assert form == {"client_id": "cid-123", "client_secret": "sec-456", "grant_type": "refresh_token", "refresh_token": "rt1"}
    assert out["access_token"] == "at2" and out["refresh_token"] == "rt2" and out["expires_in"] == 3599


@respx.mock
def test_refresh_with_saved_scope_sends_exactly_that_scope(respx_mock):
    saved = "https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read"
    route = respx_mock.post(token_url(CFG)).mock(
        return_value=httpx.Response(200, json={"access_token": "at2", "refresh_token": "rt2", "expires_in": 3599, "scope": saved})
    )
    refresh(CFG, "rt1", scope=saved, client=httpx.Client())
    form = dict(urllib.parse.parse_qsl(route.calls[0].request.read().decode()))
    assert form["scope"] == saved
    assert "Calendars.ReadWrite" not in form["scope"]


@pytest.mark.parametrize("scope", [None, ""])
@respx.mock
def test_refresh_without_saved_scope_sends_no_scope_field(respx_mock, scope):
    route = respx_mock.post(token_url(CFG)).mock(
        return_value=httpx.Response(200, json={"access_token": "at2", "refresh_token": "rt2", "expires_in": 3599})
    )
    refresh(CFG, "rt1", scope=scope, client=httpx.Client())
    form = dict(urllib.parse.parse_qsl(route.calls[0].request.read().decode()))
    assert "scope" not in form


@respx.mock
def test_refresh_error_raises_relay_error_with_code(respx_mock):
    respx_mock.post(token_url(CFG)).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant", "error_description": "AADSTS70000: expired"})
    )
    with pytest.raises(RelayError) as exc:
        refresh(CFG, "rt1", client=httpx.Client())
    assert exc.value.error == "invalid_grant" and "AADSTS70000" in str(exc.value)


@respx.mock(assert_all_mocked=False)
def test_authorize_builds_pkce_url_and_exchanges_code(respx_mock):
    respx_mock.route(host="127.0.0.1").pass_through()
    token_route = respx_mock.post(token_url(CFG)).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3599, "scope": SCOPES})
    )
    captured = {}

    def fake_browser(url: str) -> None:
        captured["url"] = url
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        captured["q"] = q

        def hit():
            httpx.get(f"http://127.0.0.1:3399/auth/callback?code=the-code&state={q['state']}", timeout=5)

        threading.Timer(0.2, hit).start()

    out = authorize(CFG, open_browser=fake_browser, client=httpx.Client(), port=3399, timeout_seconds=10)
    q = captured["q"]
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == REDIRECT_URI and q["scope"] == SCOPES
    assert q["client_id"] == "cid-123" and q["response_type"] == "code"
    form = dict(urllib.parse.parse_qsl(token_route.calls[0].request.read().decode()))
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
    assert form["redirect_uri"] == REDIRECT_URI and len(form["code_verifier"]) >= 43
    assert out["refresh_token"] == "rt"


def test_relay_scopes_include_calendar():
    assert "Calendars.ReadWrite" in SCOPES
