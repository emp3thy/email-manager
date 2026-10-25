"""rules.yaml loading and matching.

Patterns are case-insensitive globs (`*`, `?`), matched against the sender
address, the subject, or a named header. First match in file order wins. A
`sender_pattern` on a subject rule is an additional condition on the sender.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path

import yaml

from .models import Message

ACTIONS = ("keep", "archive", "trash", "calendar_event")
EXTRACTOR_NAMES = ("pay_by", "arriving")
FIELDS = ("sender", "subject", "header")
# Every label the runner knows how to apply. Gmail creates these as labels; Outlook has a
# folder only for the first five (`em/x` -> `em-x`), so an `archive` rule must use one of those.
LABELS = (
    "em/important",
    "em/newsletters",
    "em/notifications",
    "em/promotions",
    "em/receipts",
    "em/spam",
    "em/held",
    "em/review",
)
ARCHIVABLE_LABELS = LABELS[:5]


class RulesError(ValueError):
    """rules.yaml is missing, unreadable or malformed."""


@dataclass(frozen=True)
class Rule:
    pattern: str
    match_field: str
    label: str
    action: str
    header_name: str | None = None
    sender_pattern: str | None = None
    date_extractor: str | None = None
    calendar_title: str | None = None
    calendar_title_from: str | None = None


def glob_match(pattern: str, value: str) -> bool:
    return fnmatch.fnmatchcase((value or "").lower(), (pattern or "").lower())


def load_rules(path: Path) -> list[Rule]:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RulesError(f"cannot read rules file {path}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
        raise RulesError(f"{path}: expected a top-level 'rules' list")
    rules: list[Rule] = []
    for i, raw in enumerate(doc["rules"]):
        if not isinstance(raw, dict):
            raise RulesError(f"{path}: rule #{i + 1} is not a mapping")
        for key in ("pattern", "match_field", "label", "action"):
            if key not in raw or raw[key] in (None, ""):
                raise RulesError(f"{path}: rule #{i + 1} is missing '{key}'")
        if raw["match_field"] not in FIELDS:
            raise RulesError(f"{path}: rule #{i + 1} has unknown match_field {raw['match_field']!r}")
        if raw["action"] not in ACTIONS:
            raise RulesError(f"{path}: rule #{i + 1} has unknown action {raw['action']!r}")
        header_name = raw.get("header_name")
        if raw["match_field"] == "header" and not header_name:
            raise RulesError(f"{path}: rule #{i + 1} is a header rule without 'header_name'")
        if str(raw["label"]) not in LABELS:
            raise RulesError(f"{path}: rule #{i + 1} has unknown label {raw['label']!r} (known: {', '.join(LABELS)})")
        if raw["action"] == "archive" and str(raw["label"]) not in ARCHIVABLE_LABELS:
            raise RulesError(f"{path}: rule #{i + 1} archives to {raw['label']!r}, which has no Outlook folder")
        date_extractor = calendar_title = calendar_title_from = None
        if raw["action"] == "calendar_event":
            if raw.get("date_extractor") not in EXTRACTOR_NAMES:
                raise RulesError(f"{path}: rule #{i + 1} needs date_extractor ({'|'.join(EXTRACTOR_NAMES)})")
            if not raw.get("calendar_title") and not raw.get("calendar_title_from"):
                raise RulesError(f"{path}: rule #{i + 1} needs calendar_title or calendar_title_from")
            if raw.get("calendar_title_from") not in (None, "subject"):
                raise RulesError(
                    f"{path}: rule #{i + 1} has unknown calendar_title_from {raw['calendar_title_from']!r}"
                )
            date_extractor = raw["date_extractor"]
            calendar_title = str(raw["calendar_title"]) if raw.get("calendar_title") else None
            calendar_title_from = raw.get("calendar_title_from")
        rules.append(
            Rule(
                pattern=str(raw["pattern"]),
                match_field=raw["match_field"],
                label=str(raw["label"]),
                action=raw["action"],
                header_name=str(header_name) if header_name else None,
                sender_pattern=str(raw["sender_pattern"]) if raw.get("sender_pattern") else None,
                date_extractor=date_extractor,
                calendar_title=calendar_title,
                calendar_title_from=calendar_title_from,
            )
        )
    return rules


def _sender_matches(pattern: str, message: Message) -> bool:
    """Match the address; fall back to the raw From text only when no address parses.

    Display-name text must never satisfy a sender rule: '"alerts@bank.example"
    <phish@evil.example>' is not from bank.example.
    """
    _, parsed = parseaddr(message.sender or "")
    if parsed:
        return glob_match(pattern, message.sender_address())
    return glob_match(pattern, message.sender)


def _matches(message: Message, rule: Rule) -> bool:
    if rule.match_field == "sender":
        return _sender_matches(rule.pattern, message)
    if rule.match_field == "subject":
        if not glob_match(rule.pattern, message.subject):
            return False
        if rule.sender_pattern:
            return _sender_matches(rule.sender_pattern, message)
        return True
    if rule.match_field == "header":
        if not message.headers or not rule.header_name:
            return False
        value = message.headers.get(rule.header_name.lower())
        return value is not None and glob_match(rule.pattern, value)
    return False


def match(message: Message, rules: list[Rule]) -> Rule | None:
    for rule in rules:
        if _matches(message, rule):
            return rule
    return None
