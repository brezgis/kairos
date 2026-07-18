"""The Daily Oracle — turns the curated brief into a reading (title + text).

Voice: warm, wry, a little mystical/knowing — a fortune-teller × gossipy-hairdresser
× scientist who keeps receipts. It's given the user's bio (bio.md), its own
rolling memory (oracle_memory.md), the Lab's active insights, and today's data, and
it grounds every line in real numbers.

Delegates generation to an external `~/.local/bin/agent-generate` helper
(headless Claude Code → local LLM fallback). Output is Markdown (# Title +
paragraph), parsed into title/text. Generation runs in the BACKGROUND; the
frontend polls.
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
from .config import cfg, ROOT

AGENT_GENERATE = Path(os.path.expanduser("~/.local/bin/agent-generate"))
TITLE_MAX, TEXT_MAX = 48, 620
BIO_PATH = ROOT / "bio.md"
MEMORY_PATH = ROOT / "oracle_memory.md"

VOICE = (
    "You are the Oracle — the voice of Kairos. You read the user's real data and tell them what it "
    "means for today.\n\n"
    "Your voice: warm, wry, knowing — a close friend who happens to have read all your numbers and "
    "won't bore you with them. Perceptive, a little playful, with the faintest knowing undertone. "
    "Lightly mystical at most — a wink, not a séance. The insight comes from the data; you just "
    "don't make them do the arithmetic.\n\n"
    "How you speak:\n"
    "- To 'you', intimately — you know the user; this is a private conversation, not a report.\n"
    "- INTERPRET, don't recite. Say what the data MEANS for their day — how they're doing, what to "
    "watch, what would help. Lead with the 'so what'. Prefer 'you'll want water and an easy start' "
    "over 'it's 27C with readiness at 71'.\n"
    "- Cite a specific number only when it's genuinely the crux — at most one or two, never a string "
    "of figures. Most readings need none.\n"
    "- Dry humour and a gentle callout when earned. Avoid tropey lines ('the universe', 'trust the "
    "process', 'lean into', 'honour your body', 'the tank is low').\n\n"
    "Hard rules:\n"
    "- Ground everything in the data/context provided; never invent. Do NOT cite calendar/seasonal "
    "facts (daylight length, sunrise, day of year) as if they were news or anomalies — they're just "
    "the time of year, not a signal about them.\n"
    "- Read what IS, not the future. No fate, no astrology, no predictions.\n"
    "- Know the user's background (below) but don't recite it back — let it inform what you notice.\n"
    "- If the data is thin, say so plainly and ask for more; don't fake depth.\n"
    "- End on one small, concrete suggestion that follows from what you actually saw."
)

_inflight: set = set()
_lock = threading.Lock()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_text(path, tail: int | None = None) -> str:
    try:
        s = Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""
    return s[-tail:] if tail else s


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


def _prompt(brief: dict, morning: dict, bio: str, memory: str, out_path: str) -> str:
    data = {
        "day": brief.get("day"),
        "notable_deviations": brief.get("notable"),   # real anomalies (seasonal/daylight excluded)
        "today_values": brief.get("values"),          # raw values for context — NOT for reciting
        "active_insights": brief.get("active_insights"),
        "morning_checkin": morning,
    }
    return (
        VOICE + "\n\n"
        f"ABOUT THE USER (context — know this, don't recite it):\n{bio.strip() or '(not provided yet)'}\n\n"
        f"YOUR MEMORY (notes you've kept on them — build on these, don't repeat yourself):\n"
        f"{memory.strip() or '(empty — this is early days)'}\n\n"
        f"TODAY'S DATA (JSON):\n{json.dumps(data, indent=2, default=str)}\n\n"
        "Write Markdown in EXACTLY this shape:\n"
        "# <short evocative title, max 6 words, as a heading>\n"
        "\n"
        "<the reading: ONE flowing paragraph, ~70-100 words — no lists, no extra headings, no preamble>\n\n"
        f"Write that (the # title line, a blank line, then the paragraph) to this exact file:\n  {out_path}\n\n"
        f"Then, using your file tools, APPEND 1-2 terse dated lines to {MEMORY_PATH} — anything worth "
        "remembering for next time (a theme forming, something they logged, a callback). Only append; do not "
        "rewrite the file. Do not search the web or use any other tools."
    )


def _generate_text(brief: dict, morning: dict, bio: str, memory: str) -> str:
    if not AGENT_GENERATE.exists():
        raise RuntimeError(f"agent-generate not found at {AGENT_GENERATE}")
    with tempfile.TemporaryDirectory() as d:
        prompt_file = Path(d) / "prompt.txt"
        out_file = Path(d) / "reading.md"
        prompt_file.write_text(_prompt(brief, morning, bio, memory, str(out_file)))
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
    bio = _read_text(BIO_PATH)
    memory = _read_text(MEMORY_PATH, tail=2000)
    return _parse(_generate_text(brief, morning, bio, memory))


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
    # _inflight (not the DB) decides whether a generation is actually running:
    # a DB state of 'generating' with nothing in flight means a previous process
    # died mid-generation (e.g. restart) and the day would otherwise stay stuck.
    with _lock:
        if day in _inflight:
            return {"state": "generating", "title": "", "text": ""}
        _inflight.add(day)
    _store(day, "generating")
    threading.Thread(target=_run, args=(day,), daemon=True).start()
    return {"state": "generating", "title": "", "text": ""}
