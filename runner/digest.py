"""Human digest text and the REPORT / QUIET verdict."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .run import Summary
from .state import State

DISPLAY_NAMES = {"gmail": "Gmail", "outlook": "Outlook"}
LOCAL_TZ = "Europe/London"


def _local_hhmm(iso_utc: str, local_tz: str) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(local_tz)).strftime("%H:%M")


def _day_month(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    return f"{d.day} {d.strftime('%b')}"


def render(summary: Summary, local_tz: str = LOCAL_TZ) -> str:
    when = _local_hhmm(summary.started, local_tz)
    head = f"📬 Email run {when}" + (" (dry run)" if summary.dry_run else "")
    parts = ", ".join(f"{DISPLAY_NAMES.get(a, a)} {s.processed}" for a, s in summary.accounts.items())
    capped = [DISPLAY_NAMES.get(a, a) for a, s in summary.accounts.items() if s.capped]
    processed = f"Processed: {summary.processed_total()} ({parts})" if parts else f"Processed: {summary.processed_total()}"
    if capped:
        processed += f" — capped ({', '.join(capped)}); the rest is picked up next run"
    lines = [head, processed]
    if summary.calendar_created:
        label = "Would create" if summary.dry_run else "Calendar"
        items = ", ".join(f"{c['title']} {_day_month(c['date'])}" for c in summary.calendar_created)
        lines.append(f"📅 {label}: {items}")
    if summary.calendar_skipped:
        lines.append(f"📅 Already in calendar: {summary.calendar_skipped}")
    if summary.important:
        lines.append(f"⭐ Important: {len(summary.important)}")
        for item in summary.important:
            lines.append(f"  • {item['from']} — \"{item['subject']}\"")
    if summary.calendar_review:
        lines.append(f"🕒 Needs calendar review: {summary.calendar_review}")
    if summary.held:
        lines.append(f"✋ Held for review (would-be trash): {summary.held}")
    if summary.review:
        lines.append(f"📝 Review (unmatched): {summary.review}")
    if summary.errors:
        lines.append(f"⚠️ Errors: {len(summary.errors)} (see data/runner.log)")
        lines.extend(f"  • {e}" for e in summary.errors[:3])
    for account, status in summary.auth.items():
        if status == "ok":
            continue
        name = DISPLAY_NAMES.get(account, account)
        if account == "calendar" and status == "unauthorized":
            lines.append("⚠️ Calendar needs re-authorising: add Calendars.ReadWrite and run python -m relay authorize")
        elif account == "outlook":
            lines.append(f"⚠️ {name} needs re-authorising: run `python -m relay authorize` on the host.")
        elif status == "not_connected" and summary.connect_urls.get(account):
            lines.append(f"⚠️ {name} not connected: {summary.connect_urls[account]}")
        else:
            lines.append(f"⚠️ {name} authentication failed ({status}); see data/runner.log")
    return "\n".join(lines)


def render_error(started_iso: str, exc: BaseException, processed: int, local_tz: str = LOCAL_TZ) -> str:
    when = _local_hhmm(started_iso, local_tz)
    return f"⚠️ Email run {when}: {type(exc).__name__}: {exc}. {processed} messages processed before the error."


def decide_verdict(summary: Summary, state: State, now: datetime) -> str:
    """REPORT when there is something to say; auth problems re-report at most every six hours."""
    report = bool(summary.important or summary.held or summary.review or summary.calendar_review or summary.calendar_created or summary.errors)
    for account, status in summary.auth.items():
        if status == "ok":
            continue
        condition = f"{account}_{status}"
        if state.should_report(condition, now):
            state.mark_reported(condition, now)
            report = True
    return "REPORT" if report else "QUIET"
