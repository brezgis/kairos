"""Run all incremental ingests — the cron entrypoint.

    python3 -m kairos.ingest_all

Pulls the last few days of Oura + a week of weather, and Spotify if configured.
Each source is independent; one failing doesn't stop the others. Exits non-zero
if any source failed so the systemd unit shows the failure.
"""

from __future__ import annotations

import datetime as dt
import traceback

from . import db, features, ingest_oura, ingest_weather
from .config import cfg


def _source(name: str, fn) -> bool:
    print(f"== {name} ==")
    try:
        fn()
        return True
    except Exception:
        traceback.print_exc()
        print(f"   {name} FAILED — continuing with remaining sources")
        return False


def main() -> None:
    today = dt.date.today()
    start = (today - dt.timedelta(days=3)).isoformat()
    end = today.isoformat()
    failed = []

    def oura():
        for ep, n in ingest_oura.run(start, end).items():
            print(f"   {ep:26} {n}")

    def weather():
        n, lat, lon, tz = ingest_weather.run(past_days=7)
        print(f"   weather days upserted: {n}")

    def spotify():
        from . import ingest_spotify
        print(f"   {ingest_spotify.run()}")

    def calendar():
        from . import ingest_calendar
        n = ingest_calendar.run(today - dt.timedelta(days=30), today + dt.timedelta(days=30))
        print(f"   event instances upserted: {n}")

    def compute_features():
        conn = db.connect()
        try:
            print(f"   computed {features.write(conn, features.compute(conn))} day(s)")
        finally:
            conn.close()

    for name, fn, enabled, skip_note in (
        ("Oura", oura, True, ""),
        ("Weather", weather, True, ""),
        ("Spotify", spotify, bool(cfg("SPOTIFY_REFRESH_TOKEN")),
         "no SPOTIFY_REFRESH_TOKEN in .env"),
        ("Calendar", calendar, bool(cfg("KAIROS_CALENDARS") or cfg("ICLOUD_USERNAME")),
         "no KAIROS_CALENDARS or ICLOUD_USERNAME in .env"),
        ("Features", compute_features, True, ""),
    ):
        if not enabled:
            print(f"== {name}: skipped ({skip_note}) ==")
            continue
        if not _source(name, fn):
            failed.append(name)

    if failed:
        raise SystemExit(f"ingest finished with failures: {', '.join(failed)}")


if __name__ == "__main__":
    main()
