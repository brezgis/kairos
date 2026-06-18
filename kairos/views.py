"""Read/assemble the shapes the frontend's /api/* calls expect.

Keeps api.py thin: day assembly, metrics mapping, history, sources, cycle-phase
derivation, prefs, and check-in storage live here.
"""

from __future__ import annotations

import datetime as dt
import json

from . import db, features, oracle


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def norm_day(day: str) -> str:
    """Normalize a day key to zero-padded YYYY-MM-DD."""
    try:
        return dt.date.fromisoformat(day).isoformat()
    except ValueError:
        y, m, d = day.split("-")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


# ---- prefs ----------------------------------------------------------------
def get_prefs(conn) -> dict:
    row = conn.execute("SELECT value FROM app_state WHERE key = 'prefs'").fetchone()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    default_mode = "start" if dt.datetime.now().hour < 17 else "close"
    return {
        "day_mode": default_mode,
        "sources": {"oura": True, "calendar": True, "spotify": True, "weather": True},
    }


def save_prefs(conn, prefs: dict) -> dict:
    conn.execute(
        "INSERT OR REPLACE INTO app_state(key, value, updated_at) VALUES ('prefs', ?, ?)",
        (json.dumps(prefs), _now()))
    conn.commit()
    return prefs


# ---- cycle ----------------------------------------------------------------
_FLOW = {"spotting", "light", "medium", "heavy"}


def _flow_value(fields: dict):
    if not isinstance(fields, dict):
        return None
    for k, v in fields.items():
        if "flow" in k.lower() and isinstance(v, str) and v.strip().lower() in _FLOW:
            return v.strip().lower()
    return None


def cycle_for_day(conn, day: str):
    rows = conn.execute(
        "SELECT day, data FROM daily_checkin WHERE day <= ? ORDER BY day", (day,)).fetchall()
    flow_days = []
    for d, data in rows:
        try:
            obj = json.loads(data)
        except Exception:
            continue
        if _flow_value(obj.get("morning") or {}) or _flow_value(obj.get("evening") or {}):
            flow_days.append(d)
    if not flow_days:
        return None, None
    fset = set(flow_days)
    start = flow_days[0]
    for d in flow_days:
        dd = dt.date.fromisoformat(d)
        prev = (dd - dt.timedelta(days=1)).isoformat()
        prev2 = (dd - dt.timedelta(days=2)).isoformat()
        if prev not in fset and prev2 not in fset:
            start = d  # most recent period start on/before `day`
    cday = (dt.date.fromisoformat(day) - dt.date.fromisoformat(start)).days + 1
    if cday < 1 or cday > 45:
        return None, None
    phase = ("menses" if cday <= 5 else "follicular" if cday <= 13
             else "ovulation" if cday <= 15 else "luteal")
    return phase, cday


# ---- metrics --------------------------------------------------------------
def metrics_for_day(conn, day: str) -> dict:
    row = conn.execute("SELECT data FROM features_daily WHERE day = ?", (day,)).fetchone()
    f = json.loads(row[0]) if row else {}

    def v(name, nd=None):
        x = f.get(name)
        val = x.get("v") if isinstance(x, dict) else None
        return round(val, nd) if (val is not None and nd is not None) else val

    phase, cday = cycle_for_day(conn, day)
    return {
        "sleep_score": v("sleep_score"),
        "sleep_hours": v("sleep_hours", 2),
        "readiness": v("readiness_score"),
        "hrv": v("hrv"),                  # not computed yet → null
        "resting_hr": v("resting_hr"),    # not computed yet → null
        "steps": v("steps"),
        "temperature_deviation": v("temp_deviation"),
        "cycle_phase": phase,
        "cycle_day": cday,
        "weather": {
            "temp_c": v("temp_mean_c", 1),
            "summary": None,
            "daylight_h": v("daylight_h", 1),
        },
    }


# ---- day + check-in -------------------------------------------------------
def get_day(conn, day: str) -> dict:
    row = conn.execute("SELECT data FROM daily_checkin WHERE day = ?", (day,)).fetchone()
    ci = json.loads(row[0]) if row else {}
    return {
        "day": day,
        "morning": ci.get("morning"),
        "evening": ci.get("evening"),
        "oracle": oracle.get(conn, day),
        "metrics": metrics_for_day(conn, day),
    }


def save_checkin(conn, day: str, phase: str, fields: dict) -> dict:
    row = conn.execute("SELECT data FROM daily_checkin WHERE day = ?", (day,)).fetchone()
    ci = json.loads(row[0]) if row else {}
    if phase == "full":
        ci["morning"] = fields   # comprehensive close covers the whole day
        ci["evening"] = fields
    else:
        ci[phase] = fields
    conn.execute(
        "INSERT OR REPLACE INTO daily_checkin(day, data, updated_at) VALUES (?, ?, ?)",
        (day, json.dumps(ci), _now()))
    conn.commit()
    # recompute features so today's metrics reflect any logged values
    features.write(conn, features.compute(conn))
    # editing the oracle-relevant check-in invalidates the reading so it regenerates
    if phase in ("morning", "full"):
        oracle.reset(conn, day)
    return get_day(conn, day)


# ---- history (Chronos) ----------------------------------------------------
def history(conn, days: int = 60) -> list:
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    day_set = set()
    for (d,) in conn.execute("SELECT DISTINCT day FROM daily_checkin WHERE day >= ?", (start,)):
        if d:
            day_set.add(d)
    for (d,) in conn.execute(
            "SELECT DISTINCT day FROM oura_records WHERE day >= ? AND day IS NOT NULL", (start,)):
        day_set.add(d)
    items = []
    for d in sorted(day_set):
        row = conn.execute("SELECT data FROM daily_checkin WHERE day = ?", (d,)).fetchone()
        ci = json.loads(row[0]) if row else {}
        m, e = ci.get("morning") or {}, ci.get("evening") or {}
        met = metrics_for_day(conn, d)
        items.append({
            "day": d,
            "metrics": met,
            "cycle_phase": met["cycle_phase"],
            "cycle_day": met["cycle_day"],
            "morning": ci.get("morning"),
            "evening": ci.get("evening"),
            "mood": m.get("mood") or e.get("mood") or [],
            "energy": m.get("energy") if m.get("energy") is not None else e.get("energy"),
            "exercise": e.get("exercise") or m.get("exercise"),
            "oracle_title": oracle.get(conn, d)["title"],
        })
    return items


# ---- sources --------------------------------------------------------------
def sources(conn) -> list:
    def stat(table, daycol="day"):
        cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        last = conn.execute(f"SELECT MAX({daycol}) FROM {table}").fetchone()[0]
        return cnt, last

    out = []
    for key, label, table in [
        ("oura", "Oura", "oura_records"),
        ("weather", "Weather", "weather_daily"),
        ("calendar", "Calendar", "calendar_events"),
    ]:
        cnt, last = stat(table)
        out.append({"key": key, "label": label, "connected": cnt > 0, "last_seen": last, "count": cnt})
    sc = conn.execute("SELECT COUNT(*) FROM spotify_plays").fetchone()[0]
    sl = conn.execute("SELECT substr(MAX(played_at), 1, 10) FROM spotify_plays").fetchone()[0]
    out.append({"key": "spotify", "label": "Spotify", "connected": sc > 0, "last_seen": sl, "count": sc})
    return out
