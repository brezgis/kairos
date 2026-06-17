"""Run all incremental ingests — the cron entrypoint.

    python3 -m kairos.ingest_all

Pulls the last few days of Oura + a week of weather, and Spotify if configured.
Each source is independent; one failing doesn't stop the others.
"""

from __future__ import annotations

import datetime as dt

from . import ingest_oura, ingest_weather
from .config import cfg


def main() -> None:
    today = dt.date.today()
    start = (today - dt.timedelta(days=3)).isoformat()
    end = today.isoformat()

    print("== Oura ==")
    for ep, n in ingest_oura.run(start, end).items():
        print(f"   {ep:26} {n}")

    print("== Weather ==")
    n, lat, lon, tz = ingest_weather.run(past_days=7)
    print(f"   weather days upserted: {n}")

    if cfg("SPOTIFY_REFRESH_TOKEN"):
        print("== Spotify ==")
        from . import ingest_spotify
        print(f"   {ingest_spotify.run()}")
    else:
        print("== Spotify: skipped (no SPOTIFY_REFRESH_TOKEN in .env) ==")


if __name__ == "__main__":
    main()
