import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from runner.providers.base import AuthError, TransientError
from runner.providers.gmail import BASE, EM_LABELS, GmailProvider

NO_SLEEP = lambda s: None  # noqa: E731


def label_list(names):
    return {"labels": [{"id": f"Label_{i}", "name": n} for i, n in enumerate(names)] + [{"id": "INBOX", "name": "INBOX"}]}


def meta(mid, from_, subject, internal_ms, label_ids=("INBOX",), extra_headers=()):
    headers = [{"name": "From", "value": from_}, {"name": "Subject", "value": subject}]
    headers += [{"name": k, "value": v} for k, v in extra_headers]
    return {"id": mid, "labelIds": list(label_ids), "internalDate": str(internal_ms), "payload": {"headers": headers}}


@pytest.fixture
def provider():
    return GmailProvider(client=httpx.Client(), header_names=["List-Unsubscribe"], sleep=NO_SLEEP)


@respx.mock(base_url=BASE)
def test_ensure_labels_creates_only_missing(respx_mock, provider):
    existing = list(EM_LABELS[:6])
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(existing)))
    created = respx_mock.post("/labels").mock(side_effect=lambda req: httpx.Response(200, json={"id": "Label_new", "name": req.read().decode()}))
    provider.ensure_labels()
    assert created.call_count == 2
    posted = [json.loads(c.request.read()) for c in created.calls]
    assert sorted(p["name"] for p in posted) == sorted(EM_LABELS[6:])


@respx.mock(base_url=BASE)
def test_list_since_pages_and_dedupes_and_orders_oldest_first(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    respx_mock.get("/messages", params__contains={"maxResults": "100"}).mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "b"}, {"id": "a"}], "nextPageToken": "p2"}),
            httpx.Response(200, json={"messages": [{"id": "a"}, {"id": "c"}]}),
        ]
    )
    for mid, ms in (("a", 1_000), ("b", 2_000), ("c", 3_000)):
        respx_mock.get(f"/messages/{mid}").mock(return_value=httpx.Response(200, json=meta(mid, "x@example.test", "s", ms)))
    out = provider.list_since(datetime(2026, 10, 1, tzinfo=timezone.utc), limit=10)
    assert [m.id for m in out] == ["a", "b", "c"]
    assert out[0].received == datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)


@respx.mock(base_url=BASE)
def test_list_since_skips_messages_already_carrying_em_label(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "done"}, {"id": "new"}]}))
    held_id = next(l["id"] for l in label_list(EM_LABELS)["labels"] if l["name"] == "em/held")
    respx_mock.get("/messages/done").mock(return_value=httpx.Response(200, json=meta("done", "x@example.test", "s", 1000, label_ids=("INBOX", held_id))))
    respx_mock.get("/messages/new").mock(return_value=httpx.Response(200, json=meta("new", "x@example.test", "s", 2000)))
    out = provider.list_since(datetime(2026, 10, 1, tzinfo=timezone.utc), limit=10)
    assert [m.id for m in out] == ["new"]


@respx.mock(base_url=BASE)
def test_list_since_query_has_after_and_in_inbox(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    route = respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": []}))
    start = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    provider.list_since(start, limit=10)
    q = route.calls[0].request.url.params["q"]
    assert q == f"after:{int(start.timestamp())} in:inbox"


@respx.mock(base_url=BASE)
def test_metadata_request_includes_rule_header_names(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "m"}]}))
    route = respx_mock.get("/messages/m").mock(
        return_value=httpx.Response(200, json=meta("m", '"Acme" <a@example.test>', "s", 1000, extra_headers=[("List-Unsubscribe", "<mailto:u>")]))
    )
    out = provider.list_since(datetime(2026, 10, 1, tzinfo=timezone.utc), limit=10)
    names = route.calls[0].request.url.params.get_list("metadataHeaders")
    assert {"From", "Subject", "Date", "List-Unsubscribe"} <= set(names)
    assert route.calls[0].request.url.params["format"] == "metadata"
    assert out[0].headers == {"from": '"Acme" <a@example.test>', "subject": "s", "list-unsubscribe": "<mailto:u>"}
    assert out[0].sender_display() == "Acme"


@respx.mock(base_url=BASE)
def test_apply_add_label_and_remove_from_inbox_bodies(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    provider.ensure_labels()
    route = respx_mock.post("/messages/m/modify").mock(return_value=httpx.Response(200, json={}))
    provider.apply("m", ("add_label", "em/receipts"))
    provider.apply("m", ("remove_from_inbox", ""))
    bodies = [json.loads(c.request.read()) for c in route.calls]
    assert bodies[0]["addLabelIds"][0].startswith("Label_")
    assert bodies[1] == {"removeLabelIds": ["INBOX"]}
    with pytest.raises(ValueError):
        provider.apply("m", ("mark_read", ""))


@respx.mock(base_url=BASE)
def test_401_app_not_connected_raises_auth_error_with_connect_url(respx_mock, provider):
    respx_mock.get("/labels").mock(
        return_value=httpx.Response(401, json={"error": "app_not_connected", "connect_url": "http://localhost:10254/p/x/connections?connect=gmail"})
    )
    with pytest.raises(AuthError) as exc:
        provider.ensure_labels()
    assert exc.value.connect_url == "http://localhost:10254/p/x/connections?connect=gmail"


@respx.mock(base_url=BASE)
def test_429_retries_then_transient_error(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    provider.ensure_labels()
    route = respx_mock.post("/messages/m/modify").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "1"})] * 4
    )
    with pytest.raises(TransientError):
        provider.apply("m", ("add_label", "em/receipts"))
    assert route.call_count == 4  # first try + 3 retries
    route.side_effect = [httpx.Response(429), httpx.Response(429), httpx.Response(200, json={})]
    provider.apply("m", ("add_label", "em/receipts"))


@respx.mock(base_url=BASE)
def test_message_received_is_utc_from_internal_date(respx_mock, provider):
    respx_mock.get("/labels").mock(return_value=httpx.Response(200, json=label_list(EM_LABELS)))
    respx_mock.get("/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "m"}]}))
    respx_mock.get("/messages/m").mock(return_value=httpx.Response(200, json=meta("m", "x@example.test", "s", 1_761_300_000_000)))
    out = provider.list_since(datetime(2026, 10, 1, tzinfo=timezone.utc), limit=10)
    assert out[0].received == datetime(2025, 10, 24, 10, 0, tzinfo=timezone.utc)
    assert out[0].account == "gmail"
