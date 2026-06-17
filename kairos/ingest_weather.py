"""Ingest daily weather (Open-Meteo) into SQLite.

Usage (from the repo root):
    python3 -m kairos.ingest_weather                # last ~92 days through today
    python3 -m kairos.ingest_weather --past-days 30

Location comes from KAIROS_LAT / KAIROS_LON / KAIROS_TZ in .env, defaulting to
New York, NY.
"""

from __future__ import annotations

import argparse
import datetime as dt

from . import db, weather
from .config import cfg


def run(past_days: int = 92):
    lat = float(cfg("KAIROS_LAT", "40.71"))
    lon = float(cfg("KAIROS_LON", "-74.01"))
    tz = cfg("KAIROS_TZ", "America/New_York")
    conn = db.connect()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    records = weather.fetch_daily(lat, lon, tz, past_days=past_days)
    n = db.upsert_weather(conn, records, fetched_at)
    db.record_ingest(conn, "weather", "open-meteo", records[-1]["day"] if records else "", fetched_at)
    conn.close()
    return n, lat, lon, tz


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest daily weather into SQLite.")
    p.add_argument("--past-days", type=int, default=92)
    args = p.parse_args()
    n, lat, lon, tz = run(args.past_days)
    print(f"Weather: upserted {n} days for ({lat}, {lon}) [{tz}]")
    print(f"DB: {db.DB_PATH}")


if __name__ == "__main__":
    main()
