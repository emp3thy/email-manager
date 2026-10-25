import base64
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from runner.models import Message
from runner.providers import gmail, graph
from runner.providers.base import TransientError
from runner.providers.gmail import GmailProvider
from runner.providers.graph import GraphProvider
from tests.runner.fakes import FakeProvider

NO_SLEEP = lambda s: None  # noqa: E731
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_provider():
    return GmailProvider(client=httpx.Client(), sleep=NO_SLEEP)


@respx.mock(base_url=gmail.BASE)
def test_gmail_get_body_text_part(respx_mock):
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": b64("Please pay by: 30 Oct 2026")}}]}
    route = respx_mock.get("/messages/m1").mock(return_value=httpx.Response(200, json={"payload": payload, "snippet": "s"}))
    assert gmail_provider().get_body("m1") == "Please pay by: 30 Oct 2026"
    assert route.calls[0].request.url.params["format"] == "full"


@respx.mock(base_url=gmail.BASE)
def test_gmail_get_body_html_only_part(respx_mock):
    html = "<p>Please&nbsp;pay by: <b>30 Oct 2026</b></p><style>x{}</style>"
    payload = {"mimeType": "text/html", "body": {"data": b64(html)}}
    respx_mock.get("/messages/m1").mock(return_value=httpx.Response(200, json={"payload": payload}))
    body = gmail_provider().get_body("m1")
    assert "Please pay by: 30 Oct 2026" in body
    assert "x{}" not in body


@respx.mock(base_url=gmail.BASE)
def test_gmail_get_body_nested_multipart(respx_mock):
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("deep   text\n here")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>html</p>")}}]}]}
    respx_mock.get("/messages/m1").mock(return_value=httpx.Response(200, json={"payload": payload}))
    assert gmail_provider().get_body("m1") == "deep text here"


@respx.mock(base_url=gmail.BASE)
def test_gmail_get_body_falls_back_to_snippet(respx_mock):
    respx_mock.get("/messages/m1").mock(return_value=httpx.Response(200, json={"payload": {"mimeType": "image/png"}, "snippet": "snip &amp; this"}))
    assert gmail_provider().get_body("m1") == "snip & this"


@respx.mock(base_url=gmail.BASE)
def test_gmail_list_calendar_candidates_ignores_labels(respx_mock):
    p = gmail_provider()
    lst = respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "b"}, {"id": "a"}]}))
    for mid, ms, labels in (("a", 1_000, ["INBOX", "Label_1"]), ("b", 2_000, ["INBOX"])):
        respx_mock.get(f"/messages/{mid}").mock(return_value=httpx.Response(200, json={
            "id": mid, "labelIds": labels, "internalDate": str(ms),
            "payload": {"headers": [{"name": "From", "value": "x@example.test"}, {"name": "Subject", "value": "s"}]}}))
    out = p.list_calendar_candidates(datetime(1970, 1, 1, tzinfo=timezone.utc), 10)
    assert [m.id for m in out] == ["a", "b"]
    assert lst.calls[0].request.url.params["q"].endswith("in:inbox")


@respx.mock(base_url=gmail.BASE, assert_all_called=False)
def test_gmail_list_calendar_candidates_cap_keeps_newest_and_fetches_only_limit(respx_mock):
    p = gmail_provider()
    # Gmail lists newest first
    respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "c"}, {"id": "b"}, {"id": "a"}]}))
    routes = {}
    for mid, ms in (("a", 1_000), ("b", 2_000), ("c", 3_000)):
        routes[mid] = respx_mock.get(f"/messages/{mid}").mock(return_value=httpx.Response(200, json={
            "id": mid, "labelIds": ["INBOX"], "internalDate": str(ms),
            "payload": {"headers": [{"name": "From", "value": "x@example.test"}, {"name": "Subject", "value": "s"}]}}))
    out = p.list_calendar_candidates(datetime(1970, 1, 1, tzinfo=timezone.utc), 2)
    assert [m.id for m in out] == ["b", "c"]
    assert sum(r.call_count for r in routes.values()) == 2
    assert routes["a"].call_count == 0


@respx.mock(base_url=graph.BASE)
def test_graph_get_body_requests_text(respx_mock):
    route = respx_mock.get("/messages/m1").mock(return_value=httpx.Response(200, json={"body": {"contentType": "text", "content": "a  b\n c"}}))
    assert GraphProvider(client=httpx.Client(), sleep=NO_SLEEP).get_body("m1") == "a b c"
    req = route.calls[0].request
    assert req.headers["Prefer"] == 'outlook.body-content-type="text"'
    assert req.url.params["$select"] == "body"


@respx.mock(base_url=graph.BASE)
def test_graph_get_body_html_fallback_stripped(respx_mock):
    html = "<script>bad()</script><p>Due&nbsp;<b>30 Oct</b> &amp; more</p>"
    respx_mock.get("/messages/m1").mock(return_value=httpx.Response(200, json={"body": {"contentType": "html", "content": html}}))
    assert GraphProvider(client=httpx.Client(), sleep=NO_SLEEP).get_body("m1") == "Due 30 Oct & more"


@respx.mock(base_url=graph.BASE)
def test_graph_list_calendar_candidates_includes_read_mail(respx_mock):
    route = respx_mock.get("/mailFolders/inbox/messages").mock(return_value=httpx.Response(200, json={"value": [
        {"id": "r", "receivedDateTime": "2026-01-02T00:00:00Z", "subject": "s", "isRead": True,
         "from": {"emailAddress": {"name": "", "address": "a@example.test"}}},
        {"id": "r2", "receivedDateTime": "2026-01-03T00:00:00Z", "subject": "s", "isRead": True}]}))
    out = GraphProvider(client=httpx.Client(), sleep=NO_SLEEP).list_calendar_candidates(T0, 1)
    assert [m.id for m in out] == ["r"]
    params = route.calls[0].request.url.params
    assert "isRead" not in params["$filter"]


@respx.mock(base_url=graph.BASE)
def test_graph_list_calendar_candidates_cap_keeps_newest(respx_mock):
    def item(i, h):
        return {"id": i, "receivedDateTime": f"2026-01-01T{h:02d}:00:00Z", "subject": "s", "isRead": False}
    route = respx_mock.get("/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(200, json={"value": [item("c", 2), item("b", 1), item("a", 0)]}))
    out = GraphProvider(client=httpx.Client(), sleep=NO_SLEEP).list_calendar_candidates(T0, 2)
    assert [m.id for m in out] == ["b", "c"]
    assert route.calls[0].request.url.params["$orderby"] == "receivedDateTime desc"


def test_fake_provider_bodies():
    msgs = [Message(id=i, account="x", sender="s", subject="s", received=T0.replace(day=d), headers=None) for i, d in (("b", 3), ("a", 2))]
    f = FakeProvider("x", msgs, bodies={"a": "hello"})
    assert f.get_body("a") == "hello"
    with pytest.raises(TransientError):
        f.get_body("zzz")
    assert f.body_calls == ["a", "zzz"]
    f.applied.append(("a", ("add_label", "em/x")))
    assert [m.id for m in f.list_calendar_candidates(T0, 1)] == ["b"]
    assert f.candidate_calls == [(T0, 1)]


def test_fake_provider_candidates_cap_keeps_newest_oldest_first():
    msgs = [Message(id=i, account="x", sender="s", subject="s", received=T0 + timedelta(hours=h), headers=None)
            for i, h in (("c", 2), ("a", 0), ("b", 1))]
    f = FakeProvider("x", msgs)
    assert [m.id for m in f.list_calendar_candidates(T0 - timedelta(days=1), 2)] == ["b", "c"]
