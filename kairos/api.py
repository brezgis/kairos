"""Kairos FastAPI backend: health, a data summary, and the daily check-in.

Run (from repo root, with the venv):
    .venv/bin/uvicorn kairos.api:app --reload
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import db
from .config import ROOT

WEB_DIR = ROOT / "web"

app = FastAPI(title="Kairos", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()}


@app.get("/summary")
def summary():
    conn = db.connect()
    try:
        oura = {ep: n for ep, n in conn.execute(
            "SELECT endpoint, count(*) FROM oura_records GROUP BY endpoint ORDER BY endpoint")}
        w = conn.execute("SELECT count(*), min(day), max(day) FROM weather_daily").fetchone()
        plays = conn.execute("SELECT count(*) FROM spotify_plays").fetchone()[0]
        checkins = conn.execute("SELECT count(*) FROM checkins").fetchone()[0]
    finally:
        conn.close()
    return {
        "oura": oura,
        "weather": {"days": w[0], "from": w[1], "to": w[2]},
        "spotify_plays": plays,
        "checkins": checkins,
    }


@app.post("/checkin")
async def checkin(request: Request):
    """Accept the daily check-in form. Stores whatever fields are submitted."""
    payload = await request.json()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    day = payload.get("day") or dt.date.today().isoformat()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO checkins(ts, day, data, created_at) VALUES (?, ?, ?, ?)",
            (payload.get("ts") or now, day, json.dumps(payload), now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "day": day, "stored_fields": list(payload.keys())}


# Serve the bundled frontend (web/index.html) at / plus any static assets.
# Mounted last so the API routes above take precedence; guarded so the API
# still boots in a checkout without the frontend present.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
