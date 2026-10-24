import json
import subprocess

import pytest

from relay.onecli import DR_CLAW_AGENT_ID, SECRET_NAME, OneCliError, upsert_secret


class Runner:
    """Records onecli invocations and replays scripted results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        rc, out = self.results.pop(0)
        return subprocess.CompletedProcess(args, rc, stdout=out, stderr="" if rc == 0 else out)


def test_first_time_creates_then_attaches():
    r = Runner([(0, json.dumps({"id": "sec-1", "name": SECRET_NAME})), (0, "{}")])
    sid = upsert_secret("token-a", None, run=r)
    assert sid == "sec-1"
    create, attach = r.calls
    assert create[:3] == ["onecli", "secrets", "create"]
    assert "--name" in create and create[create.index("--name") + 1] == SECRET_NAME
    assert create[create.index("--type") + 1] == "generic"
    assert create[create.index("--value") + 1] == "token-a"
    assert create[create.index("--host-pattern") + 1] == "graph.microsoft.com"
    assert create[create.index("--path-pattern") + 1] == "/*"
    assert create[create.index("--header-name") + 1] == "Authorization"
    assert create[create.index("--value-format") + 1] == "Bearer {value}"
    assert attach == ["onecli", "agents", "grants", "attach-secret", "--id", DR_CLAW_AGENT_ID, "--secret-id", "sec-1"]


def test_existing_secret_is_updated_only():
    r = Runner([(0, json.dumps({"id": "sec-9"}))])
    assert upsert_secret("token-b", "sec-9", run=r) == "sec-9"
    assert r.calls == [["onecli", "secrets", "update", "--id", "sec-9", "--value", "token-b"]]


def test_update_of_missing_secret_falls_back_to_create_and_attach():
    r = Runner([(1, json.dumps({"error": "secret not found", "code": "NOT_FOUND"})), (0, json.dumps({"id": "sec-new"})), (0, "{}")])
    assert upsert_secret("token-c", "sec-gone", run=r) == "sec-new"
    assert [c[:3] for c in r.calls] == [["onecli", "secrets", "update"], ["onecli", "secrets", "create"], ["onecli", "agents", "grants"]]


def test_other_failures_raise():
    r = Runner([(1, json.dumps({"error": "connection refused"}))])
    with pytest.raises(OneCliError):
        upsert_secret("token-d", "sec-9", run=r)
