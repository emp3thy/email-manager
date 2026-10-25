"""`python -m relay authorize | refresh | status [--config-dir DIR]`

refresh: exchange the stored refresh token, persist the rotated one *before*
touching OneCLI, then upsert the Graph secret. Exit 1 on any failure with the
reason on stderr, so systemd records it.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import ms, onecli
from .config import DEFAULT_CONFIG_DIR, ConfigError, load_config
from .tokens import TokenStore


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cmd_authorize(cfg) -> int:
    store = TokenStore(cfg.token_path)
    tokens = ms.authorize(cfg)
    existing = store.load() or {}
    store.save(
        {
            "refresh_token": tokens["refresh_token"],
            "scope": tokens.get("scope", ""),
            "saved_at": _now(),
            "secret_id": existing.get("secret_id"),
            "source": "authorize",
        }
    )
    print(f"Sign-in complete; refresh token saved to {cfg.token_path}. Now run: python -m relay refresh")
    return 0


def cmd_refresh(cfg) -> int:
    store = TokenStore(cfg.token_path)
    doc = store.load()
    if not doc or not doc.get("refresh_token"):
        print(f"no refresh token at {cfg.token_path}; run `python -m relay authorize` first", file=sys.stderr)
        return 1
    try:
        tokens = ms.refresh(cfg, doc["refresh_token"], scope=doc.get("scope"))
    except ms.RelayError as exc:
        print(f"token refresh failed: {exc}", file=sys.stderr)
        return 1
    doc.update({"refresh_token": tokens["refresh_token"], "scope": tokens.get("scope") or doc.get("scope", ""), "saved_at": _now(), "source": "refresh"})
    store.save(doc)  # the rotated token is persisted before anything else can fail
    try:
        secret_id = onecli.upsert_secret(tokens["access_token"], doc.get("secret_id"))
    except onecli.OneCliError as exc:
        print(f"OneCLI secret update failed: {exc}", file=sys.stderr)
        return 1
    if secret_id != doc.get("secret_id"):
        doc["secret_id"] = secret_id
        store.save(doc)
    print(f"refreshed; access token valid for {tokens.get('expires_in', '?')}s; OneCLI secret {secret_id}")
    return 0


def cmd_status(cfg) -> int:
    doc = TokenStore(cfg.token_path).load()
    if not doc:
        print(f"no refresh token stored at {cfg.token_path}")
        return 0
    print(f"refresh token: present (saved {doc.get('saved_at', '?')}, source {doc.get('source', '?')})")
    print(f"OneCLI secret id: {doc.get('secret_id') or 'not created yet'}")
    print(f"client id: {cfg.client_id}  tenant: {cfg.tenant}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m relay")
    p.add_argument("command", choices=["authorize", "refresh", "status"])
    p.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        cfg = load_config(args.config_dir)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return {"authorize": cmd_authorize, "refresh": cmd_refresh, "status": cmd_status}[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
