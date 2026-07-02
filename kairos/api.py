"""Kairos FastAPI backend: health, a data summary, and the daily check-in.

Run (from repo root, with the venv):
    .venv/bin/uvicorn kairos.api:app --reload
"""

from __future__ import annotations

import datetime as dt
import json
import re

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, features, insights, oracle, views
from .config import ROOT

WEB_DIR = ROOT / "web"

app = FastAPI(title="Kairos", version="0.1.0")


def get_conn():
    """Per-request SQLite connection, always closed."""
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def day_param(day: str) -> str:
    """Normalize the {day} path parameter; impossible dates are a 422."""
    try:
        return views.norm_day(day)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid day: {day!r}")


@app.get("/health")
def health():
    return {"status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()}


@app.get("/summary")
def summary(conn=Depends(get_conn)):
    oura = {ep: n for ep, n in conn.execute(
        "SELECT endpoint, count(*) FROM oura_records GROUP BY endpoint ORDER BY endpoint")}
    w = conn.execute("SELECT count(*), min(day), max(day) FROM weather_daily").fetchone()
    plays = conn.execute("SELECT count(*) FROM spotify_plays").fetchone()[0]
    checkins = conn.execute("SELECT count(*) FROM daily_checkin").fetchone()[0]
    feats = conn.execute("SELECT count(*) FROM features_daily").fetchone()[0]
    return {
        "oura": oura,
        "weather": {"days": w[0], "from": w[1], "to": w[2]},
        "spotify_plays": plays,
        "checkins": checkins,
        "features_days": feats,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the standalone frontend (de-bundled; talks to /api/* directly)."""
    path = WEB_DIR / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>Kairos</h1><p>Frontend not installed. API at <a href='/docs'>/docs</a>.</p>")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/manifest.webmanifest")
def manifest():
    """Web app manifest with the correct content-type (StaticFiles guesses wrong)."""
    path = WEB_DIR / "manifest.webmanifest"
    if not path.exists():
        return Response('{}', media_type="application/manifest+json")
    return Response(path.read_text(encoding="utf-8"), media_type="application/manifest+json")


@app.post("/sync")
async def sync(request: Request):
    """Receive the app's localStorage (kairos:* keys) and persist it.

    Mirrors every key into app_state, and normalizes per-day entries
    (kairos:YYYY-M-D, non-zero-padded) into daily_checkin keyed by ISO day.
    """
    payload = await request.json()
    entries = payload.get("entries", payload)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    days = 0
    conn = db.connect()
    try:
        for key, value in entries.items():
            value = value if isinstance(value, str) else json.dumps(value)
            conn.execute(
                "INSERT OR REPLACE INTO app_state(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now))
            m = re.match(r"^kairos:(\d{4})-(\d{1,2})-(\d{1,2})$", key)
            if m:
                day = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                conn.execute(
                    "INSERT OR REPLACE INTO daily_checkin(day, data, updated_at) VALUES (?, ?, ?)",
                    (day, value, now))
                days += 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "keys": len(entries), "days": days}


@app.get("/insights")
def legacy_insights(conn=Depends(get_conn)):
    """Return the stored insights blob (what the app reads as kairos:insights)."""
    row = conn.execute("SELECT value FROM app_state WHERE key = 'kairos:insights'").fetchone()
    return json.loads(row[0]) if row else {}


@app.get("/brief")
def brief(day: str | None = None, conn=Depends(get_conn)):
    """The curated daily oracle brief: features, baselines, and notable deltas."""
    return features.daily_brief(conn, day)


# ─── Frontend API (/api/*) — the contract the new bundle calls ───────────────
@app.get("/api/prefs")
def api_get_prefs(conn=Depends(get_conn)):
    return views.get_prefs(conn)


@app.put("/api/prefs")
def api_put_prefs(prefs: dict = Body(...), conn=Depends(get_conn)):
    return views.save_prefs(conn, prefs)


@app.get("/api/day/latest")
def api_day_latest(conn=Depends(get_conn)):
    """The most recent day with a finalized night — what Chronos shows, so the
    stats don't go blank each morning while today's sleep is still syncing."""
    return views.get_day(conn, views.latest_biometric_day(conn))


@app.get("/api/day/{day}")
def api_get_day(day: str = Depends(day_param), conn=Depends(get_conn)):
    return views.get_day(conn, day)


class CheckinReq(BaseModel):
    phase: str
    fields: dict = {}


@app.post("/api/day/{day}/checkin")
def api_checkin(req: CheckinReq, day: str = Depends(day_param), conn=Depends(get_conn)):
    return views.save_checkin(conn, day, req.phase, req.fields)


@app.post("/api/day/{day}/oracle")
def api_oracle(day: str = Depends(day_param)):
    # background generation — returns {state,title,text}; frontend polls /api/day
    return oracle.request(day)


@app.get("/api/history")
def api_history(days: int = 60, conn=Depends(get_conn)):
    return views.history(conn, days)


@app.get("/api/insights")
def api_insights(conn=Depends(get_conn)):
    return insights.active(conn)


@app.get("/api/sources")
def api_sources(conn=Depends(get_conn)):
    return views.sources(conn)


# Serve /kairos-sync.js and any other assets from web/. The explicit "/" route
# above injects the sync bridge; this mount handles everything else.
# Guarded so the API still boots without the frontend present.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
