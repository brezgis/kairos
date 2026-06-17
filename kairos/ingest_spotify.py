"""Ingest Spotify listening history into SQLite.

Spotify's recently-played endpoint returns only the last 50 plays, so run this
every few hours (cron) to accumulate history. Idempotent by played_at.

Requires SPOTIFY_CLIENT_ID / SECRET / REFRESH_TOKEN in .env.
"""

from __future__ import annotations

import datetime as dt
import json

from . import db, spotify


def run() -> dict:
    conn = db.connect()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    plays = spotify.recently_played(limit=50)
    rows = []
    for it in plays:
        t = it["track"]
        rows.append((
            it["played_at"],
            t.get("id"),
            t.get("name"),
            ", ".join(a["name"] for a in t.get("artists", [])),
            json.dumps(it, separators=(",", ":")),
            fetched_at,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO spotify_plays(played_at, track_id, track_name, artists, data, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return {"recently_played": len(rows)}


def main() -> None:
    print("Spotify ingest:", run())
    print("DB:", db.DB_PATH)


if __name__ == "__main__":
    main()
