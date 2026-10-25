import json

import pytest

import relay.__main__ as cli
from relay.ms import RelayError
from relay.onecli import OneCliError


@pytest.fixture
def cfgdir(tmp_path):
    (tmp_path / "relay.env").write_text("MS_CLIENT_ID=cid\nMS_CLIENT_SECRET=sec\nMS_TENANT=consumers\n")
    (tmp_path / "outlook-token.json").write_text(json.dumps({"refresh_token": "rt1", "scope": "s", "secret_id": None}))
    return tmp_path


def test_refresh_success_saves_rotated_token_and_secret_id(cfgdir, monkeypatch):
    monkeypatch.setattr(cli.ms, "refresh", lambda cfg, rt, client=None: {"access_token": "at2", "refresh_token": "rt2", "expires_in": 3599})
    monkeypatch.setattr(cli.onecli, "upsert_secret", lambda value, sid, run=None: "sec-1")
    rc = cli.main(["refresh", "--config-dir", str(cfgdir)])
    doc = json.loads((cfgdir / "outlook-token.json").read_text())
    assert rc == 0 and doc["refresh_token"] == "rt2" and doc["secret_id"] == "sec-1" and doc["source"] == "refresh"


def test_refresh_saves_rotated_token_even_if_onecli_fails_and_exits_1(cfgdir, monkeypatch, capsys):
    monkeypatch.setattr(cli.ms, "refresh", lambda cfg, rt, client=None: {"access_token": "at2", "refresh_token": "rt2", "expires_in": 3599})

    def boom(value, sid, run=None):
        raise OneCliError("gateway down")

    monkeypatch.setattr(cli.onecli, "upsert_secret", boom)
    rc = cli.main(["refresh", "--config-dir", str(cfgdir)])
    doc = json.loads((cfgdir / "outlook-token.json").read_text())
    assert rc == 1 and doc["refresh_token"] == "rt2"
    assert "gateway down" in capsys.readouterr().err


def test_refresh_without_token_file_exits_1_with_hint(tmp_path, monkeypatch, capsys):
    (tmp_path / "relay.env").write_text("MS_CLIENT_ID=cid\nMS_CLIENT_SECRET=sec\nMS_TENANT=consumers\n")
    rc = cli.main(["refresh", "--config-dir", str(tmp_path)])
    assert rc == 1 and "authorize" in capsys.readouterr().err


def test_refresh_microsoft_failure_keeps_file_and_exits_1(cfgdir, monkeypatch, capsys):
    def fail(cfg, rt, client=None):
        raise RelayError("invalid_grant", "AADSTS70000: expired")

    monkeypatch.setattr(cli.ms, "refresh", fail)
    rc = cli.main(["refresh", "--config-dir", str(cfgdir)])
    assert rc == 1
    assert json.loads((cfgdir / "outlook-token.json").read_text())["refresh_token"] == "rt1"
    assert "invalid_grant" in capsys.readouterr().err


def test_status_reports_without_secrets(cfgdir, capsys):
    rc = cli.main(["status", "--config-dir", str(cfgdir)])
    out = capsys.readouterr().out
    assert rc == 0 and "rt1" not in out and "sec" not in out.replace("secret", "") and "refresh token" in out.lower()
