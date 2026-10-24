"""One autonomous run: window, fetch, match, act, summarise.

Every message's operations complete before the next message starts, and
`last_run` only ever advances past messages whose operations all succeeded,
so a killed or repeated run never double-processes and never skips mail.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .policy import decide
from .providers.base import AuthError, Provider, TransientError
from .rules import Rule, match
from .state import State

log = logging.getLogger("runner")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RunConfig:
    data_dir: Path
    rules_path: Path
    state_path: Path
    max_messages: int = 500
    budget_seconds: float = 600.0
    dry_run: bool = False
    accounts: list[str] | None = None


@dataclass
class AccountStats:
    fetched: int = 0
    processed: int = 0
    errors: int = 0
    capped: bool = False


@dataclass
class Summary:
    started: str
    finished: str
    dry_run: bool
    accounts: dict[str, AccountStats]
    important: list[dict] = field(default_factory=list)
    held: int = 0
    review: int = 0
    calendar_review: int = 0
    errors: list[str] = field(default_factory=list)
    auth: dict[str, str] = field(default_factory=dict)
    connect_urls: dict[str, str] = field(default_factory=dict)
    verdict: str = "QUIET"

    def processed_total(self) -> int:
        return sum(a.processed for a in self.accounts.values())

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def run(
    cfg: RunConfig,
    providers: dict[str, Provider],
    state: State,
    rules: list[Rule],
    now: Callable[[], datetime] = utcnow,
) -> Summary:
    started = now()
    deadline = time.monotonic() + cfg.budget_seconds
    summary = Summary(started=_iso(started), finished=_iso(started), dry_run=cfg.dry_run, accounts={})
    accounts = cfg.accounts or state.accounts()

    for account in accounts:
        provider = providers.get(account)
        if provider is None:
            log.warning("no provider for account %s; skipping", account)
            continue
        stats = AccountStats()
        summary.accounts[account] = stats
        summary.auth[account] = "ok"
        window_start = state.window_start(account, started)
        try:
            provider.ensure_labels()
            messages = provider.list_since(window_start, cfg.max_messages)
        except AuthError as exc:
            summary.auth[account] = "not_connected" if exc.connect_url else "unauthorized"
            if exc.connect_url:
                summary.connect_urls[account] = exc.connect_url
            log.error("%s: authentication problem: %s", account, exc)
            continue
        except (TransientError, httpx.HTTPError) as exc:
            summary.errors.append(f"{account}: list: {exc}")
            stats.errors += 1
            log.error("%s: listing failed: %s", account, exc)
            continue

        seen: set[str] = set()
        clean = True
        for message in messages:
            if message.id in seen:
                continue
            seen.add(message.id)
            stats.fetched += 1
            if time.monotonic() > deadline:
                stats.capped = True
                clean = False
                log.warning("%s: budget exhausted after %d messages", account, stats.processed)
                break
            rule = match(message, rules)
            decision = decide(rule, account)
            ok = True
            auth_lost = False
            if not cfg.dry_run:
                for op in decision.ops:
                    try:
                        provider.apply(message.id, op)
                    except AuthError as exc:
                        summary.auth[account] = "not_connected" if exc.connect_url else "unauthorized"
                        if exc.connect_url:
                            summary.connect_urls[account] = exc.connect_url
                        log.error("%s: authentication lost mid-run: %s", account, exc)
                        ok = False
                        clean = False
                        auth_lost = True
                        break
                    except (TransientError, httpx.HTTPError, ValueError) as exc:
                        summary.errors.append(f"{account}: {op[0]} {message.id}: {exc}")
                        stats.errors += 1
                        ok = False
                        clean = False
                        log.error("%s: %s on %s failed: %s", account, op[0], message.id, exc)
                        break
            if auth_lost:
                break  # the rest of this account cannot be processed this run
            if not ok:
                continue
            stats.processed += 1
            if decision.bucket == "held":
                summary.held += 1
            elif decision.bucket == "review":
                summary.review += 1
            elif decision.bucket == "calendar_review":
                summary.calendar_review += 1
            if decision.important:
                summary.important.append({"account": account, "from": message.sender_display(), "subject": message.subject})
            # Advance only while every earlier message in this window succeeded: once an
            # op has failed, last_run freezes before that message so the next run retries it.
            if not cfg.dry_run and clean:
                state.advance(account, message.received)
        else:
            # loop finished without a budget break or auth loss
            if getattr(provider, "truncated", False):
                stats.capped = True
                clean = False
        if not cfg.dry_run and clean and not stats.capped:
            state.advance(account, started)
        if stats.capped:
            log.info("%s: capped at %d; the remainder is picked up next run", account, cfg.max_messages)

    summary.finished = _iso(now())
    return summary
