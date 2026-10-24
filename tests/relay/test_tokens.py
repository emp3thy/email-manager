import os
import stat

from relay.tokens import TokenStore


def test_load_returns_none_when_missing(tmp_path):
    assert TokenStore(tmp_path / "outlook-token.json").load() is None


def test_save_is_atomic_and_private(tmp_path):
    p = tmp_path / "outlook-token.json"
    store = TokenStore(p)
    store.save({"refresh_token": "r1", "scope": "x"})
    assert store.load() == {"refresh_token": "r1", "scope": "x"}
    assert not (tmp_path / "outlook-token.json.tmp").exists()
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_save_overwrites_previous_document(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.save({"refresh_token": "r1"})
    store.save({"refresh_token": "r2", "secret_id": "abc"})
    assert store.load() == {"refresh_token": "r2", "secret_id": "abc"}
