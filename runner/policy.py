"""The autonomous action policy: rule action × provider → operations.

Pure table, no I/O. Nothing here ever deletes mail or moves it to Deleted
Items: a `trash` rule becomes a held item for the owner to review.
"""
from __future__ import annotations

from dataclasses import dataclass

from .rules import Rule

Op = tuple[str, str]

BUCKETS = ("keep", "archive", "held", "calendar", "review")
PROVIDERS = ("gmail", "outlook")
HELD_LABEL = "em/held"
REVIEW_LABEL = "em/review"
IMPORTANT_LABEL = "em/important"


@dataclass(frozen=True)
class Decision:
    ops: tuple[Op, ...]
    bucket: str
    label: str | None
    important: bool


def folder_for(label: str) -> str:
    """Gmail label name → Outlook top-level folder name (`em/x` → `em-x`)."""
    return label.replace("/", "-", 1)


def decide(rule: Rule | None, provider: str) -> Decision:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    gmail = provider == "gmail"
    if rule is None:
        ops = (("add_label", REVIEW_LABEL),) if gmail else (("mark_read", ""),)
        return Decision(ops=ops, bucket="review", label=None, important=False)

    label = rule.label
    important = label == IMPORTANT_LABEL
    if rule.action == "keep":
        ops = (("add_label", label),) if gmail else (("mark_read", ""),)
        bucket = "keep"
    elif rule.action == "archive":
        ops = (("add_label", label), ("remove_from_inbox", "")) if gmail else (("move_to_folder", folder_for(label)),)
        bucket = "archive"
    elif rule.action == "trash":
        ops = (("add_label", label), ("add_label", HELD_LABEL)) if gmail else (("mark_read", ""),)
        bucket = "held"
    elif rule.action == "calendar_event":
        ops = (("add_label", label),) if gmail else (("mark_read", ""),)
        bucket = "calendar"
    else:
        raise ValueError(f"unknown rule action {rule.action!r}")
    return Decision(ops=ops, bucket=bucket, label=label, important=important)
