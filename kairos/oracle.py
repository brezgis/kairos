"""The Daily Oracle — turns the curated brief into a reading (title + text).

Reuses the user's `~/.local/bin/agent-generate` (headless Claude Code → local Qwopus
fallback). Output is Markdown — a `# Title` heading + one paragraph — which
normalize-letter (run by agent-generate) preserves and validates; we parse the
heading into `title` and the body into `text`.

Generation runs in the BACKGROUND: request() marks the day 'generating', spawns a
thread, and returns immediately; the frontend polls GET /api/day until 'ready'.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from . import db, features
from .config import cfg

AGENT_GENERATE = Path(os.path.expanduser("~/.local/bin/agent-generate"))
TITLE_MAX, TEXT_MAX = 48, 620

_inflight: set = set()
_lock = threading.Lock()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _morning(conn, day: str) -> dict:
    row = conn.execute("SELECT data FROM daily_checkin WHERE day = ?", (day,)).fetchone()
    if not row:
        return {}
    try:
        obj = json.loads(row[0])
        return obj.get("morning") or obj.get("evening") or obj
    except Exception:
        return {}


def _has_checkin(conn, day: str) -> bool:
    row = conn.execute("SELECT data FROM daily_checkin WHERE day = ?", (day,)).fetchone()
    if not row:
        return False
    try:
        obj = json.loads(row[0])
        return bool(obj.get("morning") or obj.get("evening"))
    except Exception:
        return False


def get(conn, day: str) -> dict:
    row = conn.execute("SELECT state, title, text FROM oracle WHERE day = ?", (day,)).fetchone()
    if not row:
        return {"state": "none", "title": "", "text": ""}
    return {"state": row[0] or "none", "title": row[1] or "", "text": row[2] or ""}


def _store(day, state, title=None, text=None, source=None) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO oracle(day, state, title, text, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (day, state, title, text, source, _now()))
        conn.commit()
    finally:
        conn.close()


def reset(conn, day: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO oracle(day, state, title, text, source, created_at) "
        "VALUES (?, 'none', NULL, NULL, NULL, ?)", (day, _now()))
    conn.commit()


def _prompt(brief: dict, morning: dict, out_path: str) -> str:
    data = {
        "day": brief.get("day"),
        "notable_vs_baseline": brief.get("notable"),
        "features": brief.get("features"),
        "morning_checkin": morning,
    }
    return (
        "You are Kairos — an empirical oracle. From a person's real data for the day "
        "(sleep, readiness, weather, listening, their morning check-in) you write a short daily "
        "reading: an 'empirical horoscope' grounded entirely in the numbers, never invented.\n\n"
        "Write Markdown in EXACTLY this shape:\n"
        "# <a short evocative title — max 6 words, reads as a heading, not a sentence>\n"
        "\n"
        "<the reading: ONE flowing paragraph, about 70-100 words>\n\n"
        "Rules:\n"
        "- The first line MUST start with '# ' and be a short title.\n"
        "- The reading is one paragraph — no lists, no extra headings, no preamble or sign-off.\n"
        "- Use ONLY the data below. Reference real figures/patterns; if a field is missing or null, "
        "don't mention it and never invent a number. If there's almost no data yet, say so gently "
        "and invite them to log the day.\n"
        "- Address 'you'. End with one small, concrete suggestion grounded in the data.\n"
        "- Mention music/listening ONLY if it's genuinely relevant or striking — otherwise leave it out.\n"
        "- Do NOT use any tools and do NOT search the web. Just write.\n\n"
        f"DATA (JSON):\n{json.dumps(data, indent=2, default=str)}\n\n"
        f"Write the finished Markdown (the # title line, a blank line, then the paragraph) to this "
        f"exact file:\n  {out_path}\n"
    )


def _generate_text(brief: dict, morning: dict) -> str:
    if not AGENT_GENERATE.exists():
        raise RuntimeError(f"agent-generate not found at {AGENT_GENERATE}")
    with tempfile.TemporaryDirectory() as d:
        prompt_file = Path(d) / "prompt.txt"
        out_file = Path(d) / "reading.md"
        prompt_file.write_text(_prompt(brief, morning, str(out_file)))
        env = {
            **os.environ,
            "AGENT_MIN_BYTES": "200",
            "AGENT_CLAUDE_TIMEOUT": cfg("ORACLE_CLAUDE_TIMEOUT", "240"),
        }
        r = subprocess.run(
            [str(AGENT_GENERATE), str(prompt_file), str(out_file)],
            env=env, capture_output=True, text=True, timeout=1800,
        )
        if not out_file.exists() or not out_file.read_text().strip():
            raise RuntimeError(f"agent-generate produced no oracle output (rc={r.returncode})")
        return out_file.read_text().strip()


def _parse(raw: str):
    raw = raw.strip()
    title, body = "", raw
    m = re.search(r"(?m)^#{1,3}\s+(.+?)\s*$", raw)
    if m:
        title = m.group(1).strip()
        body = raw[m.end():].strip()
    if not title:
        first = raw.split(". ")[0].strip().rstrip(".")
        title, body = first, raw
    title = title.strip().strip('"').strip("*").strip()
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX - 1].rstrip() + "…"
    body = body.strip()
    if len(body) > TEXT_MAX:
        cut = body[:TEXT_MAX]
        dot = cut.rfind(". ")
        body = cut[:dot + 1] if dot > 300 else cut.rstrip() + "…"
    return title, body


def _produce(day: str):
    conn = db.connect()
    try:
        brief = features.daily_brief(conn, day)
        morning = _morning(conn, day)
    finally:
        conn.close()
    return _parse(_generate_text(brief, morning))


def generate_now(day: str) -> dict:
    """Synchronous generation (for the CLI / daily routine)."""
    _store(day, "generating")
    try:
        title, body = _produce(day)
        _store(day, "ready", title, body, "agent-generate")
        return {"state": "ready", "title": title, "text": body}
    except Exception:
        _store(day, "none")
        raise


def _run(day: str) -> None:
    try:
        title, body = _produce(day)
        _store(day, "ready", title, body, "agent-generate")
    except Exception:
        _store(day, "none")
    finally:
        with _lock:
            _inflight.discard(day)


def request(day: str, force: bool = False) -> dict:
    """Background generation. Returns the current state immediately."""
    conn = db.connect()
    try:
        cur = get(conn, day)
        has = _has_checkin(conn, day)
    finally:
        conn.close()
    if not has:
        return {"state": "none", "title": "", "text": ""}
    if cur["state"] == "ready" and not force:
        return cur
    if cur["state"] == "generating":
        return cur
    with _lock:
        if day in _inflight:
            return {"state": "generating", "title": "", "text": ""}
        _inflight.add(day)
    _store(day, "generating")
    threading.Thread(target=_run, args=(day,), daemon=True).start()
    return {"state": "generating", "title": "", "text": ""}
