"""Command line: `python -m runner [--max N] [--budget-seconds S] [--dry-run] [--accounts a,b]`.

Last two stdout lines are the contract the NanoClaw task reads:
    DIGEST: data/last-digest.txt
    VERDICT: REPORT | QUIET
Exit 0 on a completed run, 1 on a crash (which still prints VERDICT: REPORT).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timezone
from pathlib import Path

from .digest import decide_verdict, render, render_error
from .providers.gmail import GmailProvider
from .providers.graph import GraphProvider
from .rules import load_rules
from .run import RunConfig, run, utcnow
from .state import State

REPO = Path(__file__).resolve().parents[1]
DIGEST_REL = "data/last-digest.txt"


def build_providers(header_names: list[str]) -> dict:
    return {"gmail": GmailProvider(header_names=header_names), "outlook": GraphProvider()}


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m runner", description="Autonomous inbox processing run.")
    p.add_argument("--max", type=int, default=500, dest="max_messages")
    p.add_argument("--budget-seconds", type=float, default=600.0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--accounts", default=None, help="comma-separated subset, e.g. gmail,outlook")
    p.add_argument("--data-dir", type=Path, default=REPO / "data")
    p.add_argument("--rules", type=Path, default=REPO / "rules.yaml")
    p.add_argument("--state", type=Path, default=REPO / "state.json")
    return p.parse_args(argv)


def _emit(data_dir: Path, text: str, verdict: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "last-digest.txt").write_text(text + "\n", encoding="utf-8")
    with (data_dir / "last-run-summary.md").open("a", encoding="utf-8") as f:
        f.write(text + "\n\n")
    print(f"DIGEST: {DIGEST_REL}")
    print(f"VERDICT: {verdict}")


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    started = utcnow()
    processed = 0
    try:
        rules = load_rules(args.rules)
        state = State.load(args.state)
        cfg = RunConfig(
            data_dir=args.data_dir,
            rules_path=args.rules,
            state_path=args.state,
            max_messages=args.max_messages,
            budget_seconds=args.budget_seconds,
            dry_run=args.dry_run,
            accounts=[a.strip() for a in args.accounts.split(",")] if args.accounts else None,
        )
        header_names = sorted({r.header_name for r in rules if r.header_name})
        providers = build_providers(header_names)
        summary = run(cfg, providers, state, rules)
        processed = summary.processed_total()
        summary.verdict = decide_verdict(summary, state, utcnow())
        runs_dir = args.data_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%dT%H%M%S.%fZ")  # microseconds: two runs in one second must not collide
        (runs_dir / f"{stamp}.json").write_text(summary.to_json() + "\n", encoding="utf-8")
        if not cfg.dry_run:
            state.save()
        _emit(args.data_dir, render(summary), summary.verdict)
        return 0
    except Exception as exc:  # noqa: BLE001 — the verdict line must always be printed
        logging.getLogger("runner").exception("run crashed")
        _emit(args.data_dir, render_error(started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), exc, processed), "REPORT")
        return 1


if __name__ == "__main__":
    sys.exit(main())
