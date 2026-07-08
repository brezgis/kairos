"""Insight cards — the Oracle-Lab's output, with a candidate→graduate→archive
lifecycle. The Lab reports vetted findings each run; this module decides whether
each one is shown (active), still proving itself (candidate), or stale (archived).
GET /api/insights serves the active cards.
"""

from __future__ import annotations

import datetime as dt
import json
import re

GRADUATE_RUNS = 2      # reported in >= this many runs → graduate candidate to active
STALE_DAYS = 14        # not reported within this window → archive


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48]


def record_findings(conn, findings, run_date: str) -> dict:
    """Ingest the Lab's vetted findings for a run; manage the lifecycle."""
    reported = set()
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        fid = (f.get("id") or _slug(f.get("title", ""))).strip()
        if not fid:
            continue
        reported.add(fid)
        ev = json.dumps(f.get("evidence")) if f.get("evidence") is not None else None
        row = conn.execute("SELECT status, seen_count FROM insights WHERE id = ?", (fid,)).fetchone()
        if row:
            status, seen = row[0], (row[1] or 0) + 1
            if status in ("candidate", "archived") and seen >= GRADUATE_RUNS:
                new_status = "active"
            elif status == "archived":
                new_status = "candidate"   # reappeared, not yet enough to re-graduate
            else:
                new_status = status
            conn.execute(
                "UPDATE insights SET status=?, seen_count=?, title=?, stat=?, detail=?, confidence=?, "
                "evidence=?, since=?, last_seen=?, last_run=?, updated_at=? WHERE id=?",
                (new_status, seen, f.get("title"), f.get("stat"), f.get("detail"), f.get("confidence"),
                 ev, f.get("since"), _now(), run_date, _now(), fid))
        else:
            conn.execute(
                "INSERT INTO insights(id, status, title, stat, detail, confidence, evidence, since, "
                "seen_count, first_seen, last_seen, last_run, updated_at) "
                "VALUES (?, 'candidate', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (fid, f.get("title"), f.get("stat"), f.get("detail"), f.get("confidence"), ev,
                 f.get("since"), _now(), _now(), run_date, _now()))
    cutoff = (dt.date.fromisoformat(run_date) - dt.timedelta(days=STALE_DAYS)).isoformat()
    # Reset seen_count on archival: a finding that goes stale must re-prove itself
    # (candidate again, then GRADUATE_RUNS fresh sightings) before it re-activates,
    # rather than jumping straight back to active on its first reappearance.
    conn.execute(
        "UPDATE insights SET status='archived', seen_count=0, updated_at=? "
        "WHERE status IN ('active','candidate') AND (last_run IS NULL OR last_run < ?)",
        (_now(), cutoff))
    conn.commit()
    return {"reported": len(reported)}


def active(conn) -> list:
    rows = conn.execute(
        "SELECT id, title, stat, detail, since FROM insights WHERE status = 'active' "
        "ORDER BY COALESCE(confidence, 0) DESC, last_run DESC").fetchall()
    return [{"id": r[0], "title": r[1], "stat": r[2], "detail": r[3], "since": r[4]} for r in rows]


def catalog(conn) -> list:
    """Live findings (active + candidate) — fed to the Lab so it reuses existing
    slugs for the same pattern instead of coining a new id each run (slug drift)."""
    rows = conn.execute(
        "SELECT id, title, status FROM insights WHERE status IN ('active', 'candidate') "
        "ORDER BY status, id").fetchall()
    return [{"id": r[0], "title": r[1], "status": r[2]} for r in rows]
