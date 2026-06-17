"""Backfill and incremental ingestion of Oura data into SQLite.

Usage (from the repo root):
    python3 -m kairos.ingest_oura                 # backfill last ~2 years -> today
    python3 -m kairos.ingest_oura --start 2026-06-01 --end 2026-06-16
    python3 -m kairos.ingest_oura --days 3        # incremental: last N days

Re-running is safe: records upsert by id, so overlapping ranges just refresh.
A single endpoint failing (e.g. a scope/availability issue) is logged and
skipped rather than aborting the whole run.
"""

from __future__ import annotations

import argparse
import datetime as dt

from . import db, oura

# Date-range endpoints (start_date / end_date).
DATE_ENDPOINTS = [
    "daily_sleep",
    "daily_readiness",
    "daily_activity",
    "daily_spo2",
    "daily_stress",
    "daily_resilience",
    "daily_cardiovascular_age",
    "sleep",
    "sleep_time",
    "workout",
    "session",
    "vO2_max",
    "enhanced_tag",
]
# Datetime-range endpoints (start_datetime / end_datetime).
DATETIME_ENDPOINTS = ["heartrate"]


def run(start_date: str, end_date: str) -> dict:
    conn = db.connect()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    summary: dict = {}

    try:
        info = oura.fetch_single("personal_info")
        summary["personal_info"] = db.upsert_records(conn, "personal_info", [info], fetched_at)
    except oura.OuraError as e:
        summary["personal_info"] = f"skip ({str(e)[:80]})"

    for ep in DATE_ENDPOINTS:
        try:
            recs = list(oura.fetch_range(ep, start_date, end_date))
            summary[ep] = db.upsert_records(conn, ep, recs, fetched_at)
        except oura.OuraError as e:
            summary[ep] = f"skip ({str(e)[:80]})"

    for ep in DATETIME_ENDPOINTS:
        try:
            recs = list(oura.fetch_range(ep, start_date, end_date, datetime_range=True))
            summary[ep] = db.upsert_records(conn, ep, recs, fetched_at)
        except oura.OuraError as e:
            summary[ep] = f"skip ({str(e)[:80]})"

    db.record_ingest(conn, "oura", "all", end_date, fetched_at)
    conn.close()
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest Oura data into SQLite.")
    p.add_argument("--start", help="start date YYYY-MM-DD")
    p.add_argument("--end", default=dt.date.today().isoformat(), help="end date YYYY-MM-DD")
    p.add_argument("--days", type=int, help="incremental: ingest the last N days")
    args = p.parse_args()

    end = args.end
    if args.days:
        start = (dt.date.fromisoformat(end) - dt.timedelta(days=args.days)).isoformat()
    else:
        start = args.start or (dt.date.today() - dt.timedelta(days=730)).isoformat()

    print(f"Ingesting Oura {start} -> {end} ...\n")
    summary = run(start, end)
    print("Ingest summary (records upserted per endpoint):")
    for ep, n in summary.items():
        print(f"  {ep:28} {n}")
    print(f"\nDB: {db.DB_PATH}")


if __name__ == "__main__":
    main()
