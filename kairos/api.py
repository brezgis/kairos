"""Kairos FastAPI backend: health, a data summary, and the daily check-in.

Run (from repo root, with the venv):
    .venv/bin/uvicorn kairos.api:app --reload
"""

from __future__ import annotations

import datetime as dt
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, features, oracle
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
        checkins = conn.execute("SELECT count(*) FROM daily_checkin").fetchone()[0]
        feats = conn.execute("SELECT count(*) FROM features_daily").fetchone()[0]
    finally:
        conn.close()
    return {
        "oura": oura,
        "weather": {"days": w[0], "from": w[1], "to": w[2]},
        "spotify_plays": plays,
        "checkins": checkins,
        "features_days": feats,
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


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the Claude Design bundle with the sync bridge injected.

    The file on disk is never modified — we inject the <script> tag at serve time.
    """
    path = WEB_DIR / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>Kairos</h1><p>Frontend not installed. API at <a href='/docs'>/docs</a>.</p>")
    html = path.read_text(encoding="utf-8")
    tag = '<script src="/kairos-sync.js?v=4"></script>'
    if tag not in html:
        html = html.replace("</body>", tag + "</body>", 1)
    return HTMLResponse(html)


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
def insights():
    """Return the stored insights blob (what the app reads as kairos:insights)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'kairos:insights'").fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else {}


@app.get("/brief")
def brief(day: str | None = None):
    """The curated daily oracle brief: features, baselines, and notable deltas."""
    conn = db.connect()
    try:
        return features.daily_brief(conn, day)
    finally:
        conn.close()


class OracleReq(BaseModel):
    day: str | None = None
    force: bool = False
    prompt: str | None = None   # the app's prompt (ignored — we build from real data)
    entry: dict | None = None   # the day's localStorage entry (fresh check-in)


@app.post("/oracle")
def oracle_endpoint(req: OracleReq):
    """Generate (or return cached) the day's reading, as the LINE:/LETTER: text the
    app's parseOracle() expects. Sync def → runs in a threadpool so the
    agent-generate subprocess doesn't block the event loop."""
    day = req.day or dt.date.today().isoformat()
    morning, force = None, req.force
    if req.entry:
        morning = req.entry.get("morning") or req.entry
        force = True  # the app just submitted a check-in → regenerate with it
    conn = db.connect()
    try:
        r = oracle.generate(day, conn, force=force, morning=morning)
    finally:
        conn.close()
    return {"text": f"LINE: {r['line']}\nLETTER: {r['letter']}", "line": r["line"], "letter": r["letter"]}


# Serve /kairos-sync.js and any other assets from web/. The explicit "/" route
# above injects the sync bridge; this mount handles everything else.
# Guarded so the API still boots without the frontend present.
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
