"""Ingest calendar events (Google/iCloud iCal) into SQLite.

    python3 -m kairos.ingest_calendar               # ~180 days back, 30 fwd
    python3 -m kairos.ingest_calendar --days-back 30 --days-fwd 14

Idempotent: each expanded instance is keyed by calendar+uid+start.
Requires KAIROS_CALENDARS in .env and the icalendar/recurring_ical_events libs
(so run with the venv: .venv/bin/python -m kairos.ingest_calendar).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from . import calendars, db


def run(start: dt.date, end: dt.date) -> int:
    conn = db.connect()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    events = calendars.fetch_events(start, end)
    rows = [(
        f"{e['calendar']}|{e['uid']}|{e['start']}",
        e["calendar"], e["day"], e["start"], e["end"], e["summary"], e["location"],
        1 if e["all_day"] else 0, e["duration_min"],
        json.dumps(e, separators=(",", ":")), fetched_at,
    ) for e in events]
    conn.executemany(
        "INSERT OR REPLACE INTO calendar_events"
        "(id, calendar, day, start, end, summary, location, all_day, duration_min, data, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest calendar events into SQLite.")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--days-back", type=int, default=180)
    p.add_argument("--days-fwd", type=int, default=30)
    a = p.parse_args()
    today = dt.date.today()
    start = dt.date.fromisoformat(a.start) if a.start else today - dt.timedelta(days=a.days_back)
    end = dt.date.fromisoformat(a.end) if a.end else today + dt.timedelta(days=a.days_fwd)
    n = run(start, end)
    print(f"Calendar: upserted {n} event instances ({start} -> {end})")
    print("DB:", db.DB_PATH)


if __name__ == "__main__":
    main()
