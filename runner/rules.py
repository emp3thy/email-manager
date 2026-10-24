"""rules.yaml loading and matching.

Patterns are case-insensitive globs (`*`, `?`), matched against the sender
address, the subject, or a named header. First match in file order wins. A
`sender_pattern` on a subject rule is an additional condition on the sender.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import Message

ACTIONS = ("keep", "archive", "trash", "calendar_event")
FIELDS = ("sender", "subject", "header")


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
        rules.append(
            Rule(
                pattern=str(raw["pattern"]),
                match_field=raw["match_field"],
                label=str(raw["label"]),
                action=raw["action"],
                header_name=str(header_name) if header_name else None,
                sender_pattern=str(raw["sender_pattern"]) if raw.get("sender_pattern") else None,
            )
        )
    return rules


def _matches(message: Message, rule: Rule) -> bool:
    if rule.match_field == "sender":
        return glob_match(rule.pattern, message.sender_address()) or glob_match(rule.pattern, message.sender)
    if rule.match_field == "subject":
        if not glob_match(rule.pattern, message.subject):
            return False
        if rule.sender_pattern:
            return glob_match(rule.sender_pattern, message.sender_address()) or glob_match(
                rule.sender_pattern, message.sender
            )
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
