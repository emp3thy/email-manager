import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from runner.providers.base import AuthError, TransientError
from runner.providers.graph import BASE, EM_FOLDERS, GraphProvider

NO_SLEEP = lambda s: None  # noqa: E731


def folders(names):
    return {"value": [{"id": f"F{i}", "displayName": n} for i, n in enumerate(names)] + [{"id": "INBOXID", "displayName": "Inbox"}]}


def gmsg(mid, name, addr, subject, received):
    return {
        "id": mid,
        "receivedDateTime": received,
        "subject": subject,
        "isRead": False,
        "from": {"emailAddress": {"name": name, "address": addr}},
    }


@pytest.fixture
def provider():
    return GraphProvider(client=httpx.Client(), sleep=NO_SLEEP)


@respx.mock(base_url=BASE)
def test_ensure_folders_creates_only_missing(respx_mock, provider):
    respx_mock.get("/mailFolders").mock(return_value=httpx.Response(200, json=folders(EM_FOLDERS[:3])))
    created = respx_mock.post("/mailFolders").mock(
        side_effect=lambda req: httpx.Response(201, json={"id": "Fnew", "displayName": json.loads(req.read())["displayName"]})
    )
    provider.ensure_labels()
    assert sorted(json.loads(c.request.read())["displayName"] for c in created.calls) == sorted(EM_FOLDERS[3:])


@respx.mock(base_url=BASE)
def test_list_since_filter_orderby_and_paging(respx_mock, provider):
    respx_mock.get("/mailFolders").mock(return_value=httpx.Response(200, json=folders(EM_FOLDERS)))
    # The page-2 route must be registered first: respx returns the first route that matches.
    second = respx_mock.get("/mailFolders/inbox/messages", params__contains={"page": "2"}).mock(
        return_value=httpx.Response(200, json={"value": [gmsg("a", "A", "a@example.test", "s1", "2026-10-20T10:00:00Z"), gmsg("b", "B", "b@example.test", "s2", "2026-10-21T10:00:00Z")]})
    )
    first = respx_mock.get("/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [gmsg("a", "A", "a@example.test", "s1", "2026-10-20T10:00:00Z")],
                "@odata.nextLink": BASE + "/mailFolders/inbox/messages?$skip=50&page=2",
            },
        )
    )
    start = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    out = provider.list_since(start, limit=10)
    params = first.calls[0].request.url.params
    assert params["$filter"] == "receivedDateTime ge 2026-10-17T12:00:00Z and isRead eq false"
    assert params["$orderby"] == "receivedDateTime asc"
    assert params["$top"] == "50"
    assert "id,receivedDateTime,from,subject,isRead" in params["$select"]
    assert second.called
    assert [m.id for m in out] == ["a", "b"]
    assert out[1].received == datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)


@respx.mock(base_url=BASE)
def test_list_since_stops_when_a_page_adds_nothing_new(respx_mock, provider):
    """A next link that keeps returning the same messages must not loop forever."""
    respx_mock.get("/mailFolders").mock(return_value=httpx.Response(200, json=folders(EM_FOLDERS)))
    route = respx_mock.get("/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(
            200,
            json={"value": [gmsg("a", "A", "a@example.test", "s1", "2026-10-20T10:00:00Z")],
                  "@odata.nextLink": BASE + "/mailFolders/inbox/messages?$skip=50"},
        )
    )
    out = provider.list_since(datetime(2026, 10, 17, tzinfo=timezone.utc), limit=10)
    assert [m.id for m in out] == ["a"]
    assert route.call_count == 2


@respx.mock(base_url=BASE)
def test_sender_built_from_from_email_address(respx_mock, provider):
    respx_mock.get("/mailFolders").mock(return_value=httpx.Response(200, json=folders(EM_FOLDERS)))
    respx_mock.get("/mailFolders/inbox/messages").mock(
        return_value=httpx.Response(200, json={"value": [gmsg("a", "Acme Billing", "no-reply@acme.example", "Invoice", "2026-10-20T10:00:00Z")]})
    )
    out = provider.list_since(datetime(2026, 10, 17, tzinfo=timezone.utc), limit=10)
    assert out[0].sender == '"Acme Billing" <no-reply@acme.example>'
    assert out[0].sender_address() == "no-reply@acme.example"
    assert out[0].sender_display() == "Acme Billing"
    assert out[0].account == "outlook" and out[0].headers is None


@respx.mock(base_url=BASE)
def test_apply_mark_read_and_move_bodies(respx_mock, provider):
    respx_mock.get("/mailFolders").mock(return_value=httpx.Response(200, json=folders(EM_FOLDERS)))
    provider.ensure_labels()
    patched = respx_mock.patch("/messages/m").mock(return_value=httpx.Response(200, json={}))
    moved = respx_mock.post("/messages/m/move").mock(return_value=httpx.Response(201, json={}))
    provider.apply("m", ("mark_read", ""))
    provider.apply("m", ("move_to_folder", "em-receipts"))
    assert json.loads(patched.calls[0].request.read()) == {"isRead": True}
    body = json.loads(moved.calls[0].request.read())
    assert body == {"destinationId": "F4"}  # em-receipts is index 4 in EM_FOLDERS
    with pytest.raises(ValueError):
        provider.apply("m", ("add_label", "em/x"))


@respx.mock(base_url=BASE)
def test_401_raises_auth_error(respx_mock, provider):
    respx_mock.get("/mailFolders").mock(
        return_value=httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken", "message": "expired"}})
    )
    with pytest.raises(AuthError) as exc:
        provider.ensure_labels()
    assert "expired" in str(exc.value) or "InvalidAuthenticationToken" in str(exc.value)


@respx.mock(base_url=BASE)
def test_429_honours_retry_after(respx_mock, provider):
    respx_mock.get("/mailFolders").mock(return_value=httpx.Response(200, json=folders(EM_FOLDERS)))
    provider.ensure_labels()
    delays = []
    provider.sleep = delays.append
    route = respx_mock.patch("/messages/m").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={})]
    )
    provider.apply("m", ("mark_read", ""))
    assert delays == [3.0] and route.call_count == 2
    route.side_effect = [httpx.Response(429)] * 4
    with pytest.raises(TransientError):
        provider.apply("m", ("mark_read", ""))
