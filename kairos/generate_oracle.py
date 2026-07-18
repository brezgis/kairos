"""Generate today's oracle reading — the daily Claude routine.

    python3 -m kairos.generate_oracle            # generate today (force refresh)
    python3 -m kairos.generate_oracle --day 2026-06-17

Runs after ingestion + the morning check-in so the reading reflects real data.
Uses the agent-generate helper (headless Claude Code primary, local LLM fallback).
"""

from __future__ import annotations

import argparse
import datetime as dt

from . import db, oracle


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the daily oracle reading.")
    p.add_argument("--day", default=dt.date.today().isoformat())
    args = p.parse_args()
    r = oracle.generate_now(args.day)
    print(f"[{args.day}] {r['state']}")
    print("TITLE:", r["title"])
    print("TEXT: ", r["text"])


if __name__ == "__main__":
    main()
