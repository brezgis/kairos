"""Deterministic analysis layer — the shared foundation for both engines.

Turns the raw streams in SQLite into a per-day feature table (`features_daily`):
each numeric metric paired with rolling baselines (7- and 30-day mean) and a
z-score vs the trailing 30-day window. Then assembles the daily "oracle brief" —
the curated, consistent context the Daily Oracle (and the Oracle-Lab) read
instead of raw data.

Robust to missing streams: metrics with no data are simply absent, so this works
from day one and fills in as Oura / check-ins / Spotify accumulate.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics

from . import db

# The detailed `sleep` endpoint, restricted to the main night, is the source of
# truth for sleep durations + physiology (HRV, heart rate, breath). The
# `daily_sleep` doc carries only the score and contributor subscores — its
# total_sleep_duration/efficiency are absent — so those come from `sleep` too.
# Naps arrive as type='late_nap' etc.; type='long_sleep' is the night.
_NIGHT = "FROM oura_records WHERE endpoint='sleep' AND json_extract(data,'$.type')='long_sleep'"

# metric name -> SQL returning rows of (day, numeric value)
NUMERIC_SOURCES = {
    # sleep
    "sleep_score":       "SELECT day, json_extract(data,'$.score') FROM oura_records WHERE endpoint='daily_sleep'",
    "sleep_hours":       f"SELECT day, json_extract(data,'$.total_sleep_duration')/3600.0 {_NIGHT}",
    "sleep_efficiency":  f"SELECT day, json_extract(data,'$.efficiency') {_NIGHT}",
    "deep_sleep_hours":  f"SELECT day, json_extract(data,'$.deep_sleep_duration')/3600.0 {_NIGHT}",
    "rem_sleep_hours":   f"SELECT day, json_extract(data,'$.rem_sleep_duration')/3600.0 {_NIGHT}",
    "light_sleep_hours": f"SELECT day, json_extract(data,'$.light_sleep_duration')/3600.0 {_NIGHT}",
    "sleep_latency_min": f"SELECT day, json_extract(data,'$.latency')/60.0 {_NIGHT}",
    # heart + respiration (overnight)
    "hrv":               f"SELECT day, json_extract(data,'$.average_hrv') {_NIGHT}",
    "resting_hr":        f"SELECT day, json_extract(data,'$.lowest_heart_rate') {_NIGHT}",
    "sleep_hr":          f"SELECT day, json_extract(data,'$.average_heart_rate') {_NIGHT}",
    "avg_breath":        f"SELECT day, json_extract(data,'$.average_breath') {_NIGHT}",
    # readiness
    "readiness_score":  "SELECT day, json_extract(data,'$.score') FROM oura_records WHERE endpoint='daily_readiness'",
    "temp_deviation":   "SELECT day, json_extract(data,'$.temperature_deviation') FROM oura_records WHERE endpoint='daily_readiness'",
    # activity
    "activity_score":   "SELECT day, json_extract(data,'$.score') FROM oura_records WHERE endpoint='daily_activity'",
    "steps":            "SELECT day, json_extract(data,'$.steps') FROM oura_records WHERE endpoint='daily_activity'",
    "active_calories":  "SELECT day, json_extract(data,'$.active_calories') FROM oura_records WHERE endpoint='daily_activity'",
    # weather
    "temp_mean_c":      "SELECT day, temp_mean_c FROM v_weather",
    "temp_max_c":       "SELECT day, temp_max_c FROM v_weather",
    "precip_mm":        "SELECT day, precip_mm FROM v_weather",
    "daylight_h":       "SELECT day, daylight_s/3600.0 FROM v_weather",
    "sunshine_h":       "SELECT day, sunshine_s/3600.0 FROM v_weather",
    "uv_max":           "SELECT day, uv_index_max FROM v_weather",
    # subjective + listening
    "checkin_energy":   "SELECT day, json_extract(data,'$.morning.energy') FROM daily_checkin",
    "spotify_plays":    "SELECT date(played_at), COUNT(*) FROM spotify_plays GROUP BY date(played_at)",
}

WINDOW_SHORT, WINDOW_LONG = 7, 30

# Astronomically/calendar-deterministic metrics: kept as context but never
# flagged as "notable" — their trailing z-score just tracks the seasonal ramp,
# not a real anomaly (e.g. daylight is fixed by date + latitude).
DETERMINISTIC = {"daylight_h"}


def _load_series(conn) -> dict:
    series = {}
    for name, sql in NUMERIC_SOURCES.items():
        rows = {}
        for day, val in conn.execute(sql):
            if day and val is not None:
                try:
                    rows[day] = float(val)
                except (TypeError, ValueError):
                    pass
        series[name] = rows
    return series


def _rolling(sorted_items, day, window):
    """Mean/std of the `window` most recent values strictly before `day`."""
    prior = [v for d, v in sorted_items if d < day][-window:]
    if not prior:
        return None, None
    mean = statistics.fmean(prior)
    std = statistics.pstdev(prior) if len(prior) > 1 else 0.0
    return mean, std


def compute(conn) -> dict:
    series = _load_series(conn)
    sorted_series = {n: sorted(s.items()) for n, s in series.items()}
    all_days = sorted({d for s in series.values() for d in s})
    out = {}
    for day in all_days:
        feats = {}
        for name, s in series.items():
            if day not in s:
                continue
            v = s[day]
            mean7, _ = _rolling(sorted_series[name], day, WINDOW_SHORT)
            mean30, std30 = _rolling(sorted_series[name], day, WINDOW_LONG)
            f = {"v": round(v, 3)}
            if mean7 is not None:
                f["mean7"] = round(mean7, 3)
            if mean30 is not None:
                f["mean30"] = round(mean30, 3)
                f["delta30"] = round(v - mean30, 3)
                if std30 and std30 > 1e-9:
                    f["z30"] = round((v - mean30) / std30, 2)
            feats[name] = f
        out[day] = feats
    return out


def write(conn, features: dict) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = [(day, json.dumps(f, separators=(",", ":")), now) for day, f in features.items()]
    conn.executemany(
        "INSERT OR REPLACE INTO features_daily(day, data, computed_at) VALUES (?, ?, ?)", rows)
    conn.commit()
    return len(rows)


def daily_brief(conn, day: str | None = None) -> dict:
    """Curated context for a given day (default: latest computed)."""
    if day is None:
        row = conn.execute("SELECT max(day) FROM features_daily").fetchone()
        day = row[0] if row else None
    if not day:
        return {"day": None, "note": "no features computed yet — run `python3 -m kairos.analyze`"}
    row = conn.execute("SELECT data FROM features_daily WHERE day = ?", (day,)).fetchone()
    feats = json.loads(row[0]) if row else {}
    notable = sorted(
        ((n, f) for n, f in feats.items()
         if n not in DETERMINISTIC and isinstance(f, dict) and abs(f.get("z30", 0)) >= 1.0),
        key=lambda kv: abs(kv[1]["z30"]), reverse=True)
    active_insights = []
    ins = conn.execute("SELECT value FROM app_state WHERE key='kairos:insights'").fetchone()
    if ins:
        try:
            active_insights = json.loads(ins[0]).get("active", [])
        except Exception:
            pass
    return {
        "day": day,
        "streams_present": sorted(feats.keys()),
        "notable": [{"metric": n, **f} for n, f in notable],
        "values": {n: f.get("v") for n, f in feats.items() if isinstance(f, dict)},
        "features": feats,
        "active_insights": active_insights,
    }
