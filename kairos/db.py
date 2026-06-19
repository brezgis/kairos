"""SQLite storage for Kairos.

Design: one flexible table per source that stores each record's raw JSON keyed
by a stable id (idempotent upserts), plus convenience VIEWS that pull common
fields out with SQLite's json_extract. This keeps us robust to Oura's nested,
evolving payloads while staying easy to query. The system-of-record stays in
`data/kairos.db` (gitignored — health data never enters the repo).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .config import ROOT

DB_PATH = ROOT / "data" / "kairos.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS oura_records (
    endpoint   TEXT NOT NULL,
    id         TEXT NOT NULL,
    day        TEXT,
    ts         TEXT,
    data       TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (endpoint, id)
);
CREATE INDEX IF NOT EXISTS idx_oura_ep_day ON oura_records(endpoint, day);

CREATE TABLE IF NOT EXISTS ingest_state (
    source    TEXT NOT NULL,
    endpoint  TEXT NOT NULL,
    last_run  TEXT,
    last_end  TEXT,
    PRIMARY KEY (source, endpoint)
);

CREATE VIEW IF NOT EXISTS v_daily_sleep AS
  SELECT day,
         json_extract(data, '$.score')                AS score,
         json_extract(data, '$.total_sleep_duration') AS total_sleep_s,
         json_extract(data, '$.efficiency')           AS efficiency
  FROM oura_records WHERE endpoint = 'daily_sleep' ORDER BY day;

CREATE VIEW IF NOT EXISTS v_daily_readiness AS
  SELECT day,
         json_extract(data, '$.score')               AS score,
         json_extract(data, '$.temperature_deviation') AS temp_deviation
  FROM oura_records WHERE endpoint = 'daily_readiness' ORDER BY day;

CREATE VIEW IF NOT EXISTS v_daily_activity AS
  SELECT day,
         json_extract(data, '$.score')           AS score,
         json_extract(data, '$.steps')           AS steps,
         json_extract(data, '$.active_calories')  AS active_calories
  FROM oura_records WHERE endpoint = 'daily_activity' ORDER BY day;

CREATE TABLE IF NOT EXISTS weather_daily (
    day        TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    data       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS v_weather AS
  SELECT day,
         json_extract(data, '$.temperature_2m_mean') AS temp_mean_c,
         json_extract(data, '$.temperature_2m_max')  AS temp_max_c,
         json_extract(data, '$.temperature_2m_min')  AS temp_min_c,
         json_extract(data, '$.precipitation_sum')   AS precip_mm,
         json_extract(data, '$.daylight_duration')   AS daylight_s,
         json_extract(data, '$.sunshine_duration')   AS sunshine_s,
         json_extract(data, '$.uv_index_max')        AS uv_index_max
  FROM weather_daily ORDER BY day;

CREATE TABLE IF NOT EXISTS checkins (
    ts         TEXT PRIMARY KEY,
    day        TEXT,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spotify_plays (
    played_at  TEXT PRIMARY KEY,
    track_id   TEXT,
    track_name TEXT,
    artists    TEXT,
    data       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_checkin (
    day        TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features_daily (
    day         TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id           TEXT PRIMARY KEY,
    calendar     TEXT,
    day          TEXT,
    start        TEXT,
    end          TEXT,
    summary      TEXT,
    location     TEXT,
    all_day      INTEGER,
    duration_min INTEGER,
    data         TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cal_day ON calendar_events(day);

CREATE TABLE IF NOT EXISTS oracle (
    day        TEXT PRIMARY KEY,
    state      TEXT NOT NULL DEFAULT 'none',
    title      TEXT,
    text       TEXT,
    source     TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS insights (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'candidate',  -- candidate | active | archived
    title       TEXT,
    stat        TEXT,
    detail      TEXT,
    confidence  REAL,
    evidence    TEXT,                               -- JSON: n, effect, p, method, controls
    since       TEXT,
    seen_count  INTEGER DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    last_run    TEXT,
    updated_at  TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    return conn


def _day_from(r: dict):
    for k in ("day", "timestamp", "bedtime_start", "start_datetime", "start"):
        v = r.get(k)
        if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
            return v[:10]
    return None


def _hash(r: dict) -> str:
    return hashlib.md5(json.dumps(r, sort_keys=True).encode()).hexdigest()[:16]


def upsert_records(conn: sqlite3.Connection, endpoint: str, records, fetched_at: str) -> int:
    rows = []
    for r in records:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or r.get("timestamp") or _hash(r))
        rows.append((
            endpoint,
            rid,
            r.get("day") or _day_from(r),
            r.get("timestamp") or r.get("bedtime_start") or r.get("start_datetime"),
            json.dumps(r, separators=(",", ":")),
            fetched_at,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO oura_records(endpoint, id, day, ts, data, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_weather(conn: sqlite3.Connection, records, fetched_at: str, source: str = "open-meteo") -> int:
    rows = [
        (r["day"], source, json.dumps(r, separators=(",", ":")), fetched_at)
        for r in records if r.get("day")
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO weather_daily(day, source, data, fetched_at) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def record_ingest(conn: sqlite3.Connection, source: str, endpoint: str, last_end: str, last_run: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state(source, endpoint, last_run, last_end) VALUES (?, ?, ?, ?)",
        (source, endpoint, last_run, last_end),
    )
    conn.commit()
