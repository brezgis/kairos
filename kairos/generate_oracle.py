"""Generate today's oracle reading — the daily Claude routine.

    python3 -m kairos.generate_oracle            # generate today (force refresh)
    python3 -m kairos.generate_oracle --day 2026-06-17

Runs after ingestion + the morning check-in so the reading reflects real data.
Uses agent-generate (Claude Code primary, local Qwopus fallback).
"""

from __future__ import annotations

import argparse
import datetime as dt

from . import db, oracle


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the daily oracle reading.")
    p.add_argument("--day", default=dt.date.today().isoformat())
    p.add_argument("--no-force", action="store_true", help="use cached reading if present")
    args = p.parse_args()
    conn = db.connect()
    r = oracle.generate(args.day, conn, force=not args.no_force)
    conn.close()
    print(f"[{args.day}] {'(cached)' if r.get('cached') else 'generated'}")
    print("LINE:  ", r["line"])
    print("LETTER:", r["letter"])


if __name__ == "__main__":
    main()
