"""Recompute the feature store from the raw streams.

    python3 -m kairos.analyze        # recompute features_daily for all days

Run after ingestion (it's part of the daily cron). Cheap and idempotent.
"""

from __future__ import annotations

from . import db, features


def main() -> None:
    conn = db.connect()
    feats = features.compute(conn)
    n = features.write(conn, feats)
    brief = features.daily_brief(conn)
    conn.close()

    print(f"Computed features for {n} day(s).")
    print(f"Latest brief: {brief.get('day')}  |  streams: {brief.get('streams_present')}")
    notable = brief.get("notable") or []
    if notable:
        print("Notable vs 30-day baseline:")
        for item in notable[:8]:
            print(f"   {item['metric']:16} {item['v']:>8}  (z={item.get('z30')}, Δ30={item.get('delta30')})")
    else:
        print("Nothing notable today (everything near baseline, or not enough history yet).")


if __name__ == "__main__":
    main()
