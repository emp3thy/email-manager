"""Keep the Graph access token in a OneCLI custom secret granted to Dr Claw."""
from __future__ import annotations

import json
import subprocess
from typing import Callable

SECRET_NAME = "outlook-graph-relay"
HOST_PATTERN = "graph.microsoft.com"
DR_CLAW_AGENT_ID = "4ae0ed0c-a511-4ef5-be2a-06d0c5b489fe"


class OneCliError(Exception):
    pass


def _run(run: Callable, args: list[str]) -> dict:
    proc = run(args, capture_output=True, text=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    try:
        body = json.loads(proc.stdout or "{}")
    except ValueError:
        body = {}
    if proc.returncode != 0:
        raise OneCliError(f"{' '.join(args[:3])} failed: {text.strip()[:300]}")
    return body if isinstance(body, dict) else {}


def _is_not_found(exc: OneCliError) -> bool:
    text = str(exc).lower()
    return "not found" in text or "not_found" in text or "404" in text


def _create_and_attach(value: str, run: Callable) -> str:
    body = _run(
        run,
        [
            "onecli", "secrets", "create",
            "--name", SECRET_NAME,
            "--type", "generic",
            "--value", value,
            "--host-pattern", HOST_PATTERN,
            "--path-pattern", "/*",
            "--header-name", "Authorization",
            "--value-format", "Bearer {value}",
        ],
    )
    secret_id = body.get("id") or (body.get("data") or {}).get("id")
    if not secret_id:
        raise OneCliError(f"secrets create returned no id: {body}")
    _run(run, ["onecli", "agents", "grants", "attach-secret", "--id", DR_CLAW_AGENT_ID, "--secret-id", secret_id])
    return secret_id


def upsert_secret(value: str, secret_id: str | None, run: Callable = subprocess.run) -> str:
    """Create the secret (and grant it to Dr Claw) the first time; update it in place after."""
    if secret_id:
        try:
            _run(run, ["onecli", "secrets", "update", "--id", secret_id, "--value", value])
            return secret_id
        except OneCliError as exc:
            if not _is_not_found(exc):
                raise
    return _create_and_attach(value, run)
